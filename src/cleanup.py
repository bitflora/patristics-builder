"""
Cleanup script: removes unwanted manuscripts, fixes metadata, and removes
bad verse_refs from the database.

Passes (in order):
  1. Hard-delete non-corpus manuscripts (Bibles, Greek NTs) — file + DB rows removed
  2. Purge false-positive verse_refs from known manuscripts
  3. Fix malformed author names via lookup table
  3b. Fill missing years (per-file table, then known patristic author dates)
  4. Remove exact duplicate verse_refs
  5. Fix abbreviated inverted verse ranges (e.g. 21-6 meaning 21-26)
  6. Report zero-ref manuscripts (no auto-delete)

Usage:
  python src/cleanup.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db import get_connection, DB_PATH
from parse_thml import ANF_AUTHOR_MAP

MANUSCRIPTS_DIR = Path(__file__).parent.parent / "manuscripts"

# Manuscripts to delete entirely (file deleted, DB rows removed).
# These are non-patristic works that do not belong in the corpus.
DELETE_ENTIRELY = {
    "bible_asv.txt",        # ASV Bible translation
    "bible_gdsp.txt",       # The New Testament, An American Translation
    "bible_sblgnt.txt",     # SBL Greek New Testament
    "bible_sblgnt_sym.txt", # SBL Greek New Testament with Editorial Symbols
}

# Manuscripts whose verse_refs are known false positives and should be purged.
PURGE_REFS = {"chesterton_queertrades.txt"}

# Author name corrections: maps stored bad value → canonical display name.
AUTHOR_FIXES: dict[str, str] = {
    "anselm":                                    "Anselm of Canterbury",
    "arminius":                                  "Jacobus Arminius",
    "baker":                                     "Augustine Baker",
    "baxter":                                    "Richard Baxter",
    "benedict":                                  "Benedict of Nursia",
    "boethius":                                  "Boethius",
    "bonar":                                     "Horatius Bonar",
    "browne":                                    "Thomas Browne",
    "calvin":                                    "John Calvin",
    "chesterton":                                "G.K. Chesterton",
    "crosby":                                    "Fanny Crosby",
    "dante":                                     "Dante Alighieri",
    "decaussade":                                "Jean-Pierre de Caussade",
    "dostoevsky":                                "Fyodor Dostoevsky",
    "drummond":                                  "Henry Drummond",
    "edwards":                                   "Jonathan Edwards",
    "finney":                                    "Charles G. Finney",
    "flavel":                                    "John Flavel",
    "hudson_jh":                                 "J. Hudson Taylor",
    "irenaeus":                                  "Irenaeus",
    "julian":                                    "Julian of Norwich",
    "kempis":                                    "Thomas à Kempis",
    "law":                                       "William Law",
    "luther":                                    "Martin Luther",
    "macdonald":                                 "George MacDonald",
    "milton":                                    "John Milton",
    "nee":                                       "Watchman Nee",
    "of Clairvaux, Saint Bernard":               "Bernard of Clairvaux",
    "pascal":                                    "Blaise Pascal",
    "robertson":                                 "William Robertson",
    "rolle":                                     "Richard Rolle",
    "ruysbroeck":                                "John of Ruysbroeck",
    "schaff":                                    "Philip Schaff",
    "singh":                                     "Sadhu Sundar Singh",
    "suso":                                      "Henry Suso",
    "tauler":                                    "Johannes Tauler",
    "the Great, Saint Albert":                   "Albert the Great",
    "thomson":                                   "Andrew Thomson",
    "watson":                                    "Thomas Watson",
    "wesley":                                    "John Wesley",
    "whitefield":                                "George Whitefield",
    # Degree-prefixed names
    "D.D. Thomas Charles Edwards":               "Thomas Charles Edwards",
    "M.A., D.D. George Adam Smith":              "George Adam Smith",
    # Date-embedded names
    "Herbert Mortimer, 1833-1909 Luckock":       "Herbert Mortimer Luckock",
    "John, ca. 360-ca. 435 Cassian":             "John Cassian",
    "Miguel de, 1628-1696. Molinos":             "Miguel de Molinos",
}


# Year fixes for manuscripts whose headers carry no usable date.
# Maps filename → approximate year of composition / first publication.
# Only applied where year IS NULL, so parsed dates always win. Compilation
# files (e.g. ANF volumes) only have their yearless sub-work rows touched.
_T = "manuscripts/ccel_thml/"
YEAR_FIXES: dict[str, int] = {
    # Commentaries, dictionaries, reference works
    _T + "jamieson/jfb.txt":                    1871,
    _T + "barnes/ntnotes.txt":                  1832,
    _T + "oxford/helps.txt":                    1875,
    _T + "torrey/ttt.txt":                      1897,
    _T + "easton/ebd2.txt":                     1897,
    _T + "smith_w/bibledict.txt":               1863,
    _T + "henry/mhc1.txt":                      1708,
    _T + "henry/mhc2.txt":                      1708,
    _T + "henry/mhc3.txt":                      1710,
    _T + "henry/mhc4.txt":                      1712,
    _T + "henry/mhc5.txt":                      1710,
    _T + "henry/mhc6.txt":                      1714,
    _T + "henry/mhcc.txt":                      1706,
    _T + "johnson_bw/pnt.txt":                  1891,
    _T + "johnson_bw/john.txt":                 1886,
    _T + "mcgarvey/gospels.txt":                1905,
    _T + "mcgarvey/acts.txt":                   1863,
    _T + "groom/bible.txt":                     1921,
    _T + "moffat/jampetjud.txt":                1928,
    _T + "plummer/expositorjamesjude.txt":      1891,
    _T + "chadwick/mark.txt":                   1887,
    _T + "fudge/ourman.txt":                    1973,
    _T + "daubney/additions.txt":               1906,
    _T + "berkhof/newtestament.txt":            1915,
    "maclaren_iicor_tim.txt":                   1910,
    _T + "hitchcock/bible_names.txt":           1869,
    # Creeds, confessions, liturgy
    _T + "schaff/creeds1.txt":                  1877,
    _T + "schaff/creeds3.txt":                  1877,
    _T + "schaff/npnf214.txt":                  1900,
    _T + "anonymous/heidelberg.txt":            1563,
    _T + "anonymous/bcf.txt":                   1677,
    _T + "anonymous/scotconf.txt":              1560,
    _T + "anonymous/scotpsalter.txt":           1650,
    _T + "anonymous/canonsofdort.txt":          1619,
    _T + "anonymous/confutatio.txt":            1530,
    _T + "anonymous/westminster1.txt":          1647,
    _T + "anonymous/westminster2.txt":          1647,
    _T + "anonymous/westminster3.txt":          1646,
    _T + "anonymous/menaion.txt":               1862,
    _T + "anonymous/eh1916.txt":                1916,
    _T + "brannan/hstcrcon.txt":                1998,
    _T + "shann/euchology.txt":                 1891,
    _T + "luckock_h/studies.txt":               1881,
    # Anonymous early texts inside ANF compilations
    _T + "schaff/anf07.txt":                     100,  # Didache
    _T + "schaff/anf08.txt":                     150,  # Testaments of the Twelve Patriarchs
    _T + "schaff/anf09.txt":                     150,  # Gospel of Peter
    # Ancient & medieval
    _T + "philo/works.txt":                       40,
    _T + "augustine/enchiridion.txt":            421,
    _T + "augustine/doctrine.txt":               397,
    _T + "cassian/conferences.txt":              428,
    _T + "eucherius/formulae.txt":               440,
    _T + "eucherius/contempt.txt":               432,
    _T + "dionysius/works.txt":                  500,
    _T + "dionysius/celestial.txt":              500,
    _T + "boethius/trinity.txt":                 520,
    _T + "benedict/rule.txt":                    530,
    _T + "declan/life.txt":                     1914,  # Power's edition of the Irish Life
    "bernard_st_malachy.txt":                   1149,
    "ccel_thml/abelard/misfortunes.txt":        1132,
    _T + "abelard/misfortunes.txt":             1132,
    _T + "maimonides/guide.txt":                1190,
    _T + "aquinas/nature_grace.txt":            1270,
    "ccel_thml/albert/cleaving.txt":            1280,
    _T + "anonymous/theologia.txt":             1350,
    _T + "suso/susolife.txt":                   1362,
    _T + "julian/revelations.txt":              1395,
    _T + "kempis/imitation.txt":                1418,
    # Reformation & early modern
    _T + "calvin/treatise_relics.txt":          1543,
    _T + "calvin/prayer.txt":                   1559,
    _T + "calvin/chr_life.txt":                 1559,
    _T + "catherine_g/life.txt":                1551,
    _T + "habermann/dailyprayers.txt":          1567,
    _T + "teresa/castle2.txt":                  1577,
    _T + "ursinus/gospel.txt":                  1583,
    _T + "ursinus/catechism.txt":               1583,
    _T + "jacob_behmen/dialogues_on_the_supersensual_life.txt": 1622,
    "jacob_behmen_dialogues_on_the_supersensual_life.txt":      1622,
    _T + "watson/contentment.txt":              1653,
    _T + "watson/beatitudes.txt":               1660,
    _T + "watson/cordial.txt":                  1663,
    _T + "watson/prayer.txt":                   1692,
    _T + "watson/divinity.txt":                 1692,
    _T + "watson/commandments.txt":             1692,
    _T + "collins/divinesongs.txt":             1653,
    _T + "bunyan/grace.txt":                    1666,
    _T + "bunyan/holy_war.txt":                 1682,
    _T + "kelly/gerhardtsong.txt":              1667,
    _T + "traherne/centuries.txt":              1670,
    _T + "molinos/guide.txt":                   1675,
    "guyon_spiritual_torrents.txt":             1682,
    _T + "guyon/prayer.txt":                    1685,
    _T + "guyon/song.txt":                      1688,
    _T + "ray/persuasive.txt":                  1700,
    "fenelon_existence_god.txt":                1712,
    _T + "watts/divsongs.txt":                  1715,
    _T + "watts/psalmshymns.txt":               1719,
    "ccel_thml/addison/evidences.txt":          1730,
    "law_apracticaltreat.txt":                  1726,
    _T + "law/apracticaltreat.txt":             1726,
    "law_humbleearnest.txt":                    1761,
    _T + "law/humbleearnest.txt":               1761,
    _T + "young_e/night.txt":                   1742,
    _T + "wesley/sermons.txt":                  1746,
    _T + "newton/olneyhymns.txt":               1779,
    _T + "quadrupani/light.txt":                1795,
    # 19th century
    _T + "clarke/entire_sanct.txt":             1826,
    _T + "upham/maxims.txt":                    1847,
    _T + "baker_r/germanpulpit.txt":            1829,
    _T + "welch/pulpit_reformation.txt":        1834,
    "welch_pulpit_reformation.txt":             1834,
    _T + "bangs/history1.txt":                  1838,
    _T + "bangs/history2.txt":                  1839,
    _T + "bangs/history3.txt":                  1840,
    _T + "bangs/history4.txt":                  1841,
    _T + "bangs/alphabetic.txt":                1841,
    _T + "anonymous/jasher.txt":                1840,
    _T + "cox/sacredhymns.txt":                 1841,
    _T + "longfellow_s/bookhymns.txt":          1848,
    _T + "west_ce/analogy.txt":                 1848,
    _T + "thomson/owenlife.txt":                1850,
    _T + "griffin/sufferings.txt":              1852,
    _T + "rutherford_a/election.txt":           1854,
    _T + "borthwick/hll.txt":                   1854,
    _T + "young_j/christ.txt":                  1855,
    _T + "winkworth/lyra.txt":                  1855,
    _T + "winkworth/life.txt":                  1858,
    _T + "winkworth/chorales.txt":              1863,
    _T + "winkworth/singers.txt":               1869,
    "macdonald_phantastes_faerie.txt":          1858,
    _T + "pressense/early.txt":                 1862,
    _T + "neale/easternhymns.txt":              1862,
    _T + "waring/hymns.txt":                    1850,
    _T + "campbell/christianhymns.txt":         1865,
    _T + "spurgeon/morneve.txt":                1865,
    _T + "spurgeon/proverbs.txt":               1892,
    _T + "tischendorf/origins.txt":             1867,
    _T + "bruce/twelve.txt":                    1871,
    _T + "finney/power.txt":                    1871,
    _T + "smith_hw/secret.txt":                 1875,
    _T + "smith_hw/comfort.txt":                1906,
    _T + "anonymous/daily_light.txt":           1875,
    _T + "chatfield/greeksongs.txt":            1876,
    _T + "havergal/keptuse.txt":                1879,
    _T + "dore/gallery.txt":                    1880,
    "burgon_revision_revised.txt":              1883,
    _T + "drummond/natural_law.txt":            1883,
    _T + "drummond/greatest.txt":               1890,
    _T + "drummond/ideal.txt":                  1897,
    _T + "drummond/new_ev.txt":                 1899,
    "philip_allan_pilgrim.txt":                 1884,
    _T + "smith_geo/carey.txt":                 1885,
    _T + "murray/prayer.txt":                   1885,
    _T + "murray/new_life.txt":                 1891,
    _T + "murray/deeper.txt":                   1895,
    _T + "murray/waiting.txt":                  1896,
    _T + "murray/lords_table.txt":              1897,
    _T + "murray/true_vine.txt":                1898,
    _T + "murray/working.txt":                  1901,
    _T + "white/controversy.txt":               1888,
    _T + "white/steps.txt":                     1892,
    _T + "white/desire.txt":                    1898,
    _T + "white/acts.txt":                      1911,
    _T + "white/prophets.txt":                  1917,
    _T + "lewis_he/sswales.txt":                1889,
    _T + "terrill_jg/redfield.txt":             1889,
    _T + "palgrave/sacredsong.txt":             1889,
    "macdonald_there_back.txt":                 1891,
    _T + "orr/view.txt":                        1893,
    _T + "roberts_bh/holiness.txt":             1893,
    _T + "shaw_sb/incidents.txt":               1893,
    _T + "bevan/tersteegen.txt":                1894,
    _T + "bevan/matelda.txt":                   1896,
    _T + "bevan/friends.txt":                   1887,
    _T + "bevan/tersteegen2.txt":               1899,
    _T + "ramsay/paul_roman.txt":               1895,
    _T + "ramsay/letters.txt":                  1904,
    _T + "brownlie/latinhymns.txt":             1896,
    _T + "brownlie/greekhymns.txt":             1900,
    _T + "brownlie/easternhymns.txt":           1902,
    _T + "brownlie/officehymns.txt":            1904,
    _T + "brownlie/hymnsmorning.txt":           1911,
    _T + "brownlie/russianhymns.txt":           1920,
    _T + "crosby/indianhymnal.txt":             1898,
    # 20th century
    _T + "james/varieties.txt":                 1902,
    _T + "dickinson/musicchurch.txt":           1902,
    _T + "robertson/history.txt":               1904,
    _T + "hutton/moravian.txt":                 1909,
    _T + "benson/psalmody.txt":                 1909,
    _T + "torrey/work_holy_spirit.txt":         1910,
    _T + "gardner/cell.txt":                    1910,
    _T + "jowett/calvary.txt":                  1911,
    _T + "underhill/mysticism.txt":             1911,
    _T + "bett/methhymns.txt":                  1913,
    _T + "nutter/hymnwriters.txt":              1915,
    _T + "pink/inspiration.txt":                1917,
    _T + "pink/return.txt":                     1918,
    _T + "hewitt/gerhardt.txt":                 1918,
    _T + "feltoe/dionysius.txt":                1918,
    _T + "rolt/dionysius.txt":                  1920,
    _T + "bounds/purpose.txt":                  1920,
    _T + "pink/gospels.txt":                    1921,
    _T + "pink/antichrist.txt":                 1923,
    _T + "reeves/hymnlit.txt":                  1924,
    _T + "unknown/kneeling.txt":                1924,
    _T + "bartleman/deity.txt":                 1926,
    _T + "pink/godhood.txt":                    1929,
    _T + "pink/just.txt":                       1937,
    _T + "pink/law.txt":                        1938,
    _T + "ryden/hymnstory.txt":                 1930,
    _T + "boettner/predest.txt":                1932,
    "boettner_predest_de.txt":                  1932,
    _T + "messenger/earlyhymns.txt":            1942,
    _T + "manning/wesleyhymns.txt":             1942,
    _T + "garrison/histdisciple.txt":           1945,
    "ccel_thml/aaberg/hymnsdenmark.txt":        1945,
    _T + "anderson/prayer.txt":                 1946,
    _T + "potts/prayerearly.txt":               1953,
    _T + "pasko/saints.txt":                    2012,
    _T + "pasko/reflections.txt":               2013,
    _T + "pasko/meditation.txt":                2013,
}


def remove_manuscript(conn, manuscript_id: int, filename: str) -> None:
    """Delete verse_refs and the manuscripts row for the given id."""
    conn.execute("DELETE FROM verse_refs WHERE manuscript_id = ?", (manuscript_id,))
    conn.execute("DELETE FROM manuscripts WHERE id = ?", (manuscript_id,))


def main() -> None:
    ap = argparse.ArgumentParser(description="Clean up the patristics database")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be done without modifying anything")
    args = ap.parse_args()

    dry = args.dry_run
    prefix = "[DRY RUN] " if dry else ""

    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = get_connection()

    # ------------------------------------------------------------------ #
    # Step 1: Hard-delete non-corpus manuscripts                          #
    # ------------------------------------------------------------------ #
    print("=== Step 1: Hard-delete non-corpus manuscripts ===")
    deleted_step1 = 0
    for filename in sorted(DELETE_ENTIRELY):
        row = conn.execute(
            "SELECT id, author, title FROM manuscripts WHERE filename = ?", (filename,)
        ).fetchone()
        if row is None:
            print(f"  {filename}: not found in database, skipping")
            continue

        ref_count = conn.execute(
            "SELECT COUNT(*) FROM verse_refs WHERE manuscript_id = ?", (row["id"],)
        ).fetchone()[0]

        print(f"  {prefix}DELETE  {filename}  ({row['author']}, \"{row['title']}\", {ref_count} refs)")

        src = MANUSCRIPTS_DIR / filename
        if not dry:
            remove_manuscript(conn, row["id"], filename)
            if src.exists():
                src.unlink()
                print(f"    -> deleted file {src}")
            else:
                print(f"    -> file not found on disk: {src}")
            deleted_step1 += 1

    if deleted_step1 == 0 and not dry:
        print("  Nothing to delete.")

    # ------------------------------------------------------------------ #
    # Step 2: Purge false-positive verse_refs                             #
    # ------------------------------------------------------------------ #
    print("\n=== Step 2: Purge false-positive verse_refs ===")
    for filename in sorted(PURGE_REFS):
        row = conn.execute(
            "SELECT id, author, title FROM manuscripts WHERE filename = ?", (filename,)
        ).fetchone()
        if row is None:
            print(f"  {filename}: not found in database, skipping")
            continue

        ref_count = conn.execute(
            "SELECT COUNT(*) FROM verse_refs WHERE manuscript_id = ?", (row["id"],)
        ).fetchone()[0]

        print(f"  {prefix}PURGE REFS  {filename}  ({ref_count} refs removed)")
        if not dry:
            conn.execute("DELETE FROM verse_refs WHERE manuscript_id = ?", (row["id"],))

    # ------------------------------------------------------------------ #
    # Step 3: Fix malformed author names                                  #
    # ------------------------------------------------------------------ #
    print("\n=== Step 3: Fix malformed author names ===")
    fixed_authors = 0
    for bad, good in sorted(AUTHOR_FIXES.items()):
        rows = conn.execute(
            "SELECT id, filename FROM manuscripts WHERE author = ?", (bad,)
        ).fetchall()
        for row in rows:
            print(f"  {prefix}FIX AUTHOR  {row['filename']}")
            print(f"    {bad!r:50s}  ->  {good!r}")
            if not dry:
                conn.execute("UPDATE manuscripts SET author = ? WHERE id = ?", (good, row["id"]))
                fixed_authors += 1

    if fixed_authors == 0 and not dry:
        print("  No author fixes needed.")

    # ------------------------------------------------------------------ #
    # Step 3b: Fill missing years                                         #
    # ------------------------------------------------------------------ #
    print("\n=== Step 3b: Fill missing years ===")
    fixed_years = 0
    for filename, year in sorted(YEAR_FIXES.items()):
        rows = conn.execute(
            "SELECT id, author FROM manuscripts WHERE filename = ? AND year IS NULL",
            (filename,),
        ).fetchall()
        for row in rows:
            print(f"  {prefix}FIX YEAR  {filename}  ({row['author']})  ->  {year}")
            if not dry:
                conn.execute("UPDATE manuscripts SET year = ? WHERE id = ?", (year, row["id"]))
            fixed_years += 1

    # Fall back to the known date of the (first) author, e.g. NPNF volumes
    # parsed before parse_thml learned this fallback.
    author_years = {name: yr for name, yr in ANF_AUTHOR_MAP.values() if yr is not None}
    for row in conn.execute(
        "SELECT id, filename, author FROM manuscripts WHERE year IS NULL AND author IS NOT NULL"
    ).fetchall():
        year = author_years.get(row["author"].split(", ")[0])
        if year is None:
            continue
        print(f"  {prefix}FIX YEAR  {row['filename']}  ({row['author']})  ->  {year}")
        if not dry:
            conn.execute("UPDATE manuscripts SET year = ? WHERE id = ?", (year, row["id"]))
        fixed_years += 1

    print(f"  {'Would fix' if dry else 'Fixed'} {fixed_years} years.")

    # ------------------------------------------------------------------ #
    # Step 4: Remove exact duplicate verse_refs                           #
    # ------------------------------------------------------------------ #
    print("\n=== Step 4: Remove exact duplicate verse_refs ===")
    dup_groups = conn.execute(
        """
        SELECT manuscript_id, citation_offset, book_slug, chapter, verse_start,
               MIN(id) AS keep_id, COUNT(*) AS cnt
        FROM verse_refs
        GROUP BY manuscript_id, citation_offset, book_slug, chapter, verse_start
        HAVING cnt > 1
        """
    ).fetchall()

    total_dups = sum(r["cnt"] - 1 for r in dup_groups)
    print(f"  Found {len(dup_groups)} duplicate groups ({total_dups} rows to remove)")

    if not dry and dup_groups:
        for r in dup_groups:
            conn.execute(
                """DELETE FROM verse_refs
                   WHERE manuscript_id = ? AND citation_offset = ?
                     AND book_slug = ? AND chapter = ?
                     AND (verse_start = ? OR (verse_start IS NULL AND ? IS NULL))
                     AND id != ?""",
                (r["manuscript_id"], r["citation_offset"],
                 r["book_slug"], r["chapter"],
                 r["verse_start"], r["verse_start"],
                 r["keep_id"]),
            )
        print(f"  Removed {total_dups} duplicate rows.")

    # ------------------------------------------------------------------ #
    # Step 5: Fix abbreviated inverted verse ranges                       #
    # ------------------------------------------------------------------ #
    # Pattern: verse_start >= 10, verse_end is a single digit < verse_start,
    # and prepending the leading digit(s) of verse_start to verse_end gives a
    # plausible range end (e.g. verse_start=21, verse_end=6 → corrected_end=26).
    print("\n=== Step 5: Fix abbreviated inverted verse ranges ===")
    inverted = conn.execute(
        """
        SELECT id, book_slug, chapter, verse_start, verse_end
        FROM verse_refs
        WHERE verse_start IS NOT NULL AND verse_end IS NOT NULL
          AND verse_start > verse_end
          AND verse_end < 10
          AND verse_start >= 10
        """
    ).fetchall()

    fixed_ranges = 0
    unfixable = 0
    for r in inverted:
        vs = r["verse_start"]
        ve_raw = r["verse_end"]
        # Prepend the same leading digit(s) from verse_start
        # e.g. vs=21 ve=6 → "2"+"6"=26; vs=134 ve=7 → "13"+"7"=137
        prefix_digits = str(vs)[:-1]  # everything except last digit
        candidate = int(prefix_digits + str(ve_raw))
        if candidate > vs:
            print(f"  {prefix}FIX RANGE  id={r['id']}  {r['book_slug']} {r['chapter']}:"
                  f"{vs}-{ve_raw}  ->  {vs}-{candidate}")
            if not dry:
                conn.execute("UPDATE verse_refs SET verse_end = ? WHERE id = ?",
                             (candidate, r["id"]))
                fixed_ranges += 1
        else:
            unfixable += 1

    print(f"  Fixed {fixed_ranges if not dry else len([r for r in inverted if int(str(r['verse_start'])[:-1] + str(r['verse_end'])) > r['verse_start']])} ranges"
          f", {unfixable} unfixable (left as-is)")

    # ------------------------------------------------------------------ #
    # Step 6: Report zero-ref manuscripts (no auto-delete)                #
    # ------------------------------------------------------------------ #
    print("\n=== Step 6: Zero-ref manuscripts (report only) ===")
    zero_ref_rows = conn.execute(
        """
        SELECT m.id, m.filename, m.author, m.title, m.category
        FROM manuscripts m
        LEFT JOIN verse_refs vr ON vr.manuscript_id = m.id
        GROUP BY m.id
        HAVING COUNT(vr.id) = 0
        ORDER BY m.category, m.author, m.filename
        """
    ).fetchall()

    if not zero_ref_rows:
        print("  None found.")
    else:
        print(f"  {len(zero_ref_rows)} manuscripts with zero verse_refs:")
        for row in zero_ref_rows:
            author = row["author"] or "(no author)"
            title = row["title"] or "(no title)"
            cat = row["category"] or "(no category)"
            print(f"  [{cat}]  {row['filename']}")
            print(f"    {author} — {title}")

    # ------------------------------------------------------------------ #
    # Commit and summarise                                                 #
    # ------------------------------------------------------------------ #
    if not dry:
        conn.commit()
        print("\nDatabase changes committed.")
    else:
        print("\n[DRY RUN] No changes made.")

    conn.close()

    print("\nDone. Run  go run ./cmd/builder --clean  to rebuild the static output.")


if __name__ == "__main__":
    main()
