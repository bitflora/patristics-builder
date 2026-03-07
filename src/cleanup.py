"""
Cleanup script: removes unwanted manuscripts, fixes metadata, and removes
bad verse_refs from the database.

Passes (in order):
  1. Hard-delete non-corpus manuscripts (Bibles, Greek NTs) — file + DB rows removed
  2. Purge false-positive verse_refs from known manuscripts
  3. Fix malformed author names via lookup table
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
