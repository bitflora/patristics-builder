"""
Categorise manuscripts in the database.

Reads each manuscript file, extracts the CCEL Subjects header field, and
combines that with author name, title, filename, and year heuristics to assign
a category to every manuscript row.

Usage:
  python src/categorize.py            # categorise all manuscripts
  python src/categorize.py --dry-run  # print decisions without writing to DB
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db import get_connection, DB_PATH

MANUSCRIPTS_DIR = Path(__file__).parent.parent / "manuscripts"


# ── CCEL subjects extraction ───────────────────────────────────────────────────

def _parse_ccel_subjects(text: str) -> set[str]:
    """
    Extract the CCEL Subjects field from a manuscript header and return a
    lower-cased set of individual subject tokens.

    E.g. "CCEL Subjects: All; Classic; Early;" → {"all", "classic", "early"}
    """
    sep_re = re.compile(r'_{10,}')
    matches = list(sep_re.finditer(text, 0, 4000))
    if len(matches) < 2:
        return set()
    header = text[matches[0].end():matches[1].start()]
    m = re.search(r'^\s*CCEL Subjects:\s*(.+)', header, re.MULTILINE | re.IGNORECASE)
    if not m:
        return set()
    raw = m.group(1)
    return {s.strip().lower() for s in raw.split(';') if s.strip()}


# ── Category determination ─────────────────────────────────────────────────────

# Known author surname fragments mapped to their category (lower-cased).
# Order matters only within each call — the function checks these in a defined
# priority sequence, not by dict order.
_PATRISTICS_AUTHORS = {
    "augustine", "athanasius", "cassian", "chrysostom", "origen",
    "jerome", "ambrose", "basil", "gregory", "cyprian", "tertullian",
    "irenaeus", "clement", "eusebius", "lactantius", "hilary", "leo",
    "cyril", "ephrem", "ignatius", "polycarp", "papias", "dionysius",
}

_MEDIEVAL_AUTHORS = {
    "eckhart", "bonaventure", "bernard", "anselm", "matelda", "aquinas",
    "guyon", "tauler", "rolle", "hilton", "kempe", "kempis", "fenelon",
    "law",  # William Law — 18th c. but deeply mystical/medieval in character
}

_REFORMATION_AUTHORS = {
    "calvin", "luther", "melanchthon", "zwingli", "knox", "tyndale",
    "cranmer", "beza", "bucer", "bullinger", "erasmus",
}

_PURITAN_AUTHORS = {
    "owen", "baxter", "bunyan", "flavel", "goodwin", "watson", "boston",
    "manton", "howe", "gurnall", "charnock", "sibbes", "allestree",
    "mason", "rutherford", "mead", "vincent", "swinnock", "love",
    "edwards", "shepard", "fisher",  # Shepard (Puritan), Fisher (Marrow of Divinity)
    "arndt",   # Johann Arndt — German Lutheran proto-Pietist (pre-Puritan era but closely aligned)
}

_SERMON_AUTHORS = {
    "spurgeon", "maclaren", "moody", "whyte", "dods",
}

_SYSTEMATIC_AUTHORS = {
    "berkhof", "bavinck", "hodge", "warfield", "turretin", "dabney",
    "kuyper", "forsyth",
}

_APOLOGETICS_AUTHORS = {
    "chesterton", "macdonald", "kierkegaard", "plantinga", "lewis",
    "pascal", "newman", "sayers", "coleridge",
}

_DEVOTIONAL_AUTHORS = {
    "murray", "pink", "torrey", "smith",  # Hannah Whitall Smith
    "gordon",  # S.D. Gordon (Quiet Talks series)
    "havergal", "underhill",  # Evelyn Underhill (20th c. mysticism writer)
    "fenelon",  # also in _MEDIEVAL_AUTHORS but catches modern versions
    "wesley",  # John Wesley — devotional/holiness tradition
    "newton",  # John Newton — hymn writer / evangelical
    "moule",   # H.C.G. Moule — devotional/pastoral
    "orr",     # C.E. Orr — holiness movement
    "quadrupani",  # Carlo Quadrupani — Catholic devotional
    "steele",  # Daniel Steele — holiness
    "upham",   # T.C. Upham — holiness/Fenelon translator
    "pasko",   # Mark Pasko — contemporary devotional
    "inge",    # W.R. Inge — Anglican mysticism/apologetics
    "oman",    # John Oman — Scottish Presbyterian theology
}


def _author_matches(author_lower: str, names: set[str]) -> bool:
    return any(n in author_lower for n in names)


def determine_category(
    filename: str,
    author: str | None,
    title: str | None,
    year: int | None,
    ccel_subjects: set[str],
) -> str:
    fn = filename.lower()
    al = (author or "").lower()
    tl = (title or "").lower()
    subj = ccel_subjects  # already lower-cased

    # 0. Ante-Nicene / Nicene Fathers series (NPNF/ANF collections edited by Schaff)
    if 'anf' in fn or 'npnf' in fn:
        return "Patristics"

    # 1. Biblical Commentary — check before Scripture so Expositor's Bible series
    #    (which CCEL tags as "Bibles") is correctly classified
    if "expositor" in fn or "expositor" in tl:
        return "Biblical Commentary"
    if "commentary" in tl or "commentary" in fn:
        return "Biblical Commentary"
    if re.match(r'(an? )?exposition of\b', tl):
        return "Biblical Commentary"
    # Word Pictures, Bible studies, synthetic studies, etc.
    if "word pictures" in tl or "bible studies" in tl or "synthetic bible" in tl:
        return "Biblical Commentary"

    # 2. Scripture — Bible texts, catechisms, confessions of faith
    if "bibles" in subj or "bible" in subj:
        return "Scripture"
    scripture_title_kws = {"catechism", "confession of faith", "heidelberg",
                           "westminster", "book of jasher", "book of common prayer",
                           "augsburg confession", "apology of the augsburg",
                           "scottish confession", "scots confession"}
    if any(k in tl for k in scripture_title_kws):
        return "Scripture"

    # 3. Patristics — CCEL "Early" / "Early Church", year < 600, or known author
    if "early" in subj or "early church" in subj:
        return "Patristics"
    if year and year < 600:
        return "Patristics"
    if _author_matches(al, _PATRISTICS_AUTHORS):
        return "Patristics"
    # Works *about* the apostolic/early church fathers by later scholars
    if "apostolic fathers" in tl or "early christian" in tl or "early church" in tl:
        return "Patristics"
    # Secondary works on individual church fathers (e.g. "St. Dionysius of Alexandria")
    if re.search(r'\bst\.?\s+\w+ of \w+', tl) and any(
            k in tl for k in {"alexandria", "hippo", "antioch", "carthage", "caesarea"}):
        return "Patristics"

    # 4. Medieval — CCEL "Mysticism", year 600-1500, or known medieval author
    if "mysticism" in subj:
        return "Medieval"
    if year and 600 <= year < 1500:
        return "Medieval"
    if _author_matches(al, _MEDIEVAL_AUTHORS):
        return "Medieval"
    # Cloud of Unknowing is anonymous — catch by filename
    if "cloud" in fn:
        return "Medieval"

    # 5. Systematic Theology — before Church History so "Systematic Theology" titles win
    if _author_matches(al, _SYSTEMATIC_AUTHORS):
        return "Systematic Theology"
    if "systematic theology" in tl or "dogmatics" in tl or "dogmatic theology" in tl:
        return "Systematic Theology"

    # 6. Church History — CCEL "History" or title keyword
    if "history" in subj:
        return "Church History"
    if "history" in tl or "history" in fn or "historical" in tl:
        return "Church History"
    if "huguenot" in tl or "huguenot" in fn:
        return "Church History"
    # Menno Simons — Anabaptist, treat as Church History
    if "menno simons" in al or "menno simon" in al:
        return "Church History"
    # Biographies and hagiographies
    if re.match(r'^(life of|lives of)\b', tl):
        return "Church History"
    ch_history_title_kws = {"eirenicon", "primitive christianity", "rise and progress",
                             "american religious movement"}
    if any(k in tl for k in ch_history_title_kws):
        return "Church History"

    # 7. Reformation — known Reformation authors or document titles
    if _author_matches(al, _REFORMATION_AUTHORS):
        return "Reformation"
    reformation_title_kws = {"institutes", "bondage of the will", "small catechism",
                              "large catechism", "smalcald", "formula of concord",
                              "thirty-nine articles", "confutatio", "pulpit of the reformation"}
    if any(k in tl for k in reformation_title_kws):
        return "Reformation"

    # 8. Puritan — known Puritan authors
    if _author_matches(al, _PURITAN_AUTHORS):
        return "Puritan"

    # 9. Sermons — known sermon preachers or "sermon" in title/filename
    if _author_matches(al, _SERMON_AUTHORS):
        return "Sermons"
    if "sermon" in tl or "sermon" in fn:
        return "Sermons"

    # 10. Apologetics — known apologetics authors or apologetics keywords in title
    if _author_matches(al, _APOLOGETICS_AUTHORS):
        return "Apologetics"
    if "apologetics" in tl or "defence of" in tl or "defense of" in tl:
        return "Apologetics"

    # 11. Devotional — known devotional authors, CCEL "Christian Life", or keywords
    if _author_matches(al, _DEVOTIONAL_AUTHORS):
        return "Devotional"
    if "christian life" in subj:
        return "Devotional"
    devotional_kws = {"devotion", "prayer", "meditation", "spiritual",
                      "way of peace", "way of holiness", "holy living",
                      "mortification", "imitation", "uniformity", "piety",
                      "the soul of", "waiting on", "with christ",
                      "comfort for", "christian's secret", "kept for",
                      "love enthroned", "plain account of christian"}
    if any(k in tl for k in devotional_kws):
        return "Devotional"
    # Hymns and devotional poetry
    hymn_kws = {"hymn", "hymns", "psalms and hymns", "spiritual songs", "sacred songs",
                "night thoughts", "religious poems", "poetical works", "divine songs",
                "sacred hymns"}
    if any(k in tl for k in hymn_kws) or "hymn" in fn:
        return "Devotional"
    # More devotional title patterns
    more_devotional_kws = {"light and peace", "holy life", "in his steps",
                           "to my younger", "comfort against", "sufferings of christ",
                           "maxims of the saints", "meditating on scripture",
                           "reflections on the christian", "quiet talks"}
    if any(k in tl for k in more_devotional_kws):
        return "Devotional"
    # Biblical reference works
    if "helps to the study" in tl or "revision revised" in tl or "word pictures" in tl:
        return "Biblical Commentary"
    if "genesis to revelation" in tl or "notes on the bible" in tl:
        return "Biblical Commentary"
    if "bible gallery" in tl or "eckhart" in tl:
        return "Biblical Commentary" if "bible gallery" in tl else "Medieval"
    # Works about a specific person → Church History
    if "thomas boston" in tl or "psalmody" in tl or "william carey" in tl:
        return "Church History"
    # Philo — Jewish-Hellenistic philosopher, precursor to early Christianity
    if "philo" in al:
        return "Patristics"

    return "Other"


# ── Migration & update ────────────────────────────────────────────────────────

def add_column_if_missing(conn) -> None:
    try:
        conn.execute("ALTER TABLE manuscripts ADD COLUMN category TEXT")
        conn.commit()
        print("Added 'category' column to manuscripts table.")
    except Exception as exc:
        if "duplicate column name" in str(exc).lower():
            print("'category' column already exists — skipping ALTER TABLE.")
        else:
            raise


def categorise_all(dry_run: bool = False) -> None:
    conn = get_connection()
    add_column_if_missing(conn)

    rows = conn.execute(
        "SELECT id, filename, author, title, year FROM manuscripts ORDER BY author, title"
    ).fetchall()

    print(f"\nCategorising {len(rows)} manuscripts…\n")

    tally: Counter = Counter()
    updates = []

    for row in rows:
        manuscript_id = row["id"]
        filename = row["filename"]
        author = row["author"]
        title = row["title"]
        year = row["year"]

        # Try to read the file for CCEL subjects
        path = MANUSCRIPTS_DIR / filename
        if not path.exists():
            # Try archive subdirectory
            path = MANUSCRIPTS_DIR / "archive" / filename
        subjects: set[str] = set()
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                subjects = _parse_ccel_subjects(text)
            except OSError:
                pass

        category = determine_category(filename, author, title, year, subjects)
        tally[category] += 1
        updates.append((category, manuscript_id))

        line = f"  [{category:22s}]  {author or 'Unknown':30s}  {title or filename}"
        print(line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))

    if not dry_run:
        with conn:
            conn.executemany(
                "UPDATE manuscripts SET category = ? WHERE id = ?",
                updates,
            )
        print(f"\nUpdated {len(updates)} manuscripts.")

    print("\n-- Category summary -----------------------------------------------------")
    for cat, count in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"  {cat:25s}  {count:3d}")

    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Categorise manuscripts in the DB")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print decisions without writing to DB")
    args = ap.parse_args()
    categorise_all(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
