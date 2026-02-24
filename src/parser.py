"""
Citation parser: scans manuscript TXT files for Bible verse references
and records them (with passage offsets) in the SQLite database.

Usage:
  python src/parser.py                          # parse all files in manuscripts/
  python src/parser.py manuscripts/mort.txt     # parse a single file
  python src/parser.py --dry-run manuscripts/mort.txt  # print matches, no DB write
  python src/parser.py --stats                  # show DB stats after parsing

The parser is idempotent: re-running a file first deletes its existing rows.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Allow running from project root or src/
sys.path.insert(0, str(Path(__file__).parent))

from bible_data import ABBREV_LOOKUP, ABBREV_LIST, roman_to_int, is_roman, BOOKS
from db import get_connection, create_schema, upsert_manuscript, delete_refs_for_manuscript, DB_PATH

MANUSCRIPTS_DIR = Path(__file__).parent.parent / "manuscripts"

# ── Manuscript metadata ───────────────────────────────────────────────────────
# Maps filename → (author, title, year, ccel_url)
# Add entries here as you download more manuscripts.
METADATA: dict[str, tuple] = {
    "mort.txt":        ("John Owen",        "Of the Mortification of Sin in Believers", 1656, None),
    "government.txt":  ("Richard Allestree","The Government of the Tongue",             1674, None),
    "sermons.txt":     ("Meister Eckhart",  "Sermons",                                  1300, None),
    "warrant3.txt":    ("Alvin Plantinga",  "Warranted Christian Belief",               2000, None),
}

# ── Sentence splitting ────────────────────────────────────────────────────────

# Abbreviations that commonly precede a period but do NOT end a sentence.
# Used to re-join incorrectly split fragments.
_ABBREV_ENDINGS = {
    "mr", "mrs", "dr", "prof", "rev", "st", "sts", "vs", "etc", "cf",
    "viz", "vol", "vols", "ch", "chap", "no", "fig", "ms", "mss", "op",
    "cit", "pp", "gen", "ex", "lev", "num", "deut", "josh", "judg", "psa",
    "ps", "prov", "eccl", "isa", "jer", "lam", "ezek", "dan", "hos",
    "matt", "mar", "luk", "joh", "rom", "cor", "gal", "eph", "phil", "col",
    "thess", "tim", "tit", "heb", "jas", "jam", "pet", "rev", "i", "ii", "iii",
}

# Split on: sentence-ending punct, optional close-quotes, whitespace, capital/quote
_SENT_SPLIT_RE = re.compile(
    r'([.!?]["\'\)]*)\s+(?=[A-Z"\(\[])'
)


def split_sentences(text: str) -> list[str]:
    """Split text into sentences using a simple heuristic."""
    # Use findall to get the split points, then reconstruct
    parts = _SENT_SPLIT_RE.split(text)
    # parts alternates: [text, punct, text, punct, ...]
    sentences: list[str] = []
    buf = ""
    i = 0
    while i < len(parts):
        chunk = parts[i]
        punct = parts[i + 1] if i + 1 < len(parts) else ""
        buf += chunk + punct
        i += 2

        # Decide whether to break here
        # Check the last word before the punctuation (potential abbreviation)
        last_word_m = re.search(r'(\w+)\s*[.!?]["\'\)]*$', buf.rstrip())
        last_word = last_word_m.group(1).lower() if last_word_m else ""

        if punct and last_word not in _ABBREV_ENDINGS:
            sentences.append(buf.strip())
            buf = ""

    if buf.strip():
        sentences.append(buf.strip())

    # Remove empty and re-join very short fragments (< 10 chars, likely bad splits)
    result: list[str] = []
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if result and len(sent) < 10:
            result[-1] += " " + sent
        else:
            result.append(sent)
    return result


# ── Citation regex ────────────────────────────────────────────────────────────

def _build_citation_re() -> re.Pattern:
    """
    Build a compiled regex that matches Bible citations in a variety of formats:

      Rom. viii. 13          - abbreviated book, Roman chapter
      Romans 8:13            - full name, arabic
      St John vi. 44         - saint prefix, Roman chapter
      1 Cor. 15:10           - numbered book, arabic
      Ps. xxvii.             - chapter only (no verse)
      Rom. viii. 13-17       - verse range
      Gal. v. 16, 17         - multiple verses (handled post-match)
    """
    # Escape abbrevs for regex, sorted longest-first for greedy matching
    escaped = [re.escape(a) for a in ABBREV_LIST]

    # Book pattern: word boundary, optional "Saint" prefix, then abbreviation.
    # End with optional dot; require the next char to NOT be a word char
    # (this prevents "Roma" from matching "Romans" and "Gen" from matching "General").
    book_pat = (
        r'\b'
        r'(?:(?:Saint|St\.?)\s+)?'   # optional St / Saint prefix
        r'(?:' + '|'.join(escaped) + r')'
        r'\.?'                         # optional trailing dot
        r'(?=\W|$)'                    # must end at a non-word char (or EOL)
    )

    # Chapter: Roman or Arabic numerals
    ch_pat = r'(?:[ivxlcdmIVXLCDM]+|\d+)'

    # Verse: one or more digits, optional range/list
    verse_pat = r'(?:\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*\d+)*)'

    # Separators between components
    sep = r'[\s\.:]+'

    # Full citation: book sep chapter [sep verse]
    full = (
        r'(?P<book>' + book_pat + r')'
        r'(?:' + sep + r'(?P<chapter>' + ch_pat + r')'
        r'(?:' + sep + r'(?P<verse>' + verse_pat + r'))?'
        r')?'
    )
    return re.compile(full, re.IGNORECASE)


CITATION_RE = _build_citation_re()

# Verse list split (handles "13, 14" or "13-15" after a match)
_VERSE_LIST_RE = re.compile(r'\d+')


def _parse_verse_field(verse_str: str | None) -> tuple[int | None, int | None]:
    """
    Parse verse field like "13", "13-17", "13, 14, 15" into (verse_start, verse_end).
    Multi-verse lists return (first, last).
    Returns (None, None) for chapter-level references.
    """
    if not verse_str:
        return None, None
    nums = [int(n) for n in _VERSE_LIST_RE.findall(verse_str)]
    if not nums:
        return None, None
    first, last = nums[0], nums[-1]
    # Treat "n-n" (same verse repeated) as a single verse
    return first, (last if last != first else None)


def _resolve_book(raw: str) -> dict | None:
    """
    Resolve a raw book string (with possible dots, 'St', etc.) to a canonical book dict.
    Returns None if not found.
    """
    # Normalise: remove dots, collapse spaces, strip 'St.'/'Saint' prefix, lowercase
    s = raw.lower()
    s = re.sub(r'\bst(?:\.?\s+|aint\s+)', '', s)  # remove St/Saint prefix
    s = re.sub(r'\.', '', s)                        # remove dots
    s = re.sub(r'\s+', ' ', s).strip()              # normalise whitespace

    # Direct lookup
    if s in ABBREV_LOOKUP:
        return ABBREV_LOOKUP[s]

    # Try without trailing punctuation noise
    s2 = s.rstrip(' .')
    if s2 in ABBREV_LOOKUP:
        return ABBREV_LOOKUP[s2]

    return None


# ── Passage window extraction ─────────────────────────────────────────────────

def _find_paragraph_bounds(text: str, char_offset: int) -> tuple[int, int]:
    """
    Given a char_offset within text, find the start and end of the paragraph
    (delimited by blank lines, i.e. two or more consecutive newlines).
    Returns (para_start, para_end) as character offsets.
    """
    # Blank-line boundary: two or more newlines (possibly with spaces between)
    blank_line = re.compile(r'\n[ \t]*\n')

    # Find last blank line before offset
    para_start = 0
    for m in blank_line.finditer(text, 0, char_offset):
        para_start = m.end()

    # Find next blank line after offset
    m = blank_line.search(text, char_offset)
    para_end = m.start() if m else len(text)

    return para_start, para_end


MAX_SENTENCES = 10


def extract_passage_offsets(text: str, citation_offset: int) -> tuple[int, int]:
    """
    Find the passage window (up to MAX_SENTENCES sentences) surrounding
    citation_offset. Returns (passage_start_offset, passage_end_offset).
    """
    para_start, para_end = _find_paragraph_bounds(text, citation_offset)
    para_text = text[para_start:para_end]

    sentences = split_sentences(para_text)

    if len(sentences) <= MAX_SENTENCES:
        return para_start, para_end

    # Find which sentence contains the citation
    cite_rel = citation_offset - para_start
    cursor = 0
    cite_sent_idx = 0
    for i, sent in enumerate(sentences):
        # Find this sentence's position in para_text
        pos = para_text.find(sent, cursor)
        if pos == -1:
            pos = cursor
        if pos <= cite_rel <= pos + len(sent):
            cite_sent_idx = i
        cursor = pos + len(sent)

    # Take a window of MAX_SENTENCES centred on cite_sent_idx
    half = MAX_SENTENCES // 2
    start_idx = max(0, cite_sent_idx - half)
    end_idx = min(len(sentences), start_idx + MAX_SENTENCES)
    start_idx = max(0, end_idx - MAX_SENTENCES)

    window_sentences = sentences[start_idx:end_idx]
    window_text = " ".join(window_sentences)

    # Find the actual char offsets of the window in the original text
    first_sent = window_sentences[0]
    last_sent = window_sentences[-1]

    # Locate window start in para_text
    win_start_rel = para_text.find(first_sent)
    win_end_rel = para_text.rfind(last_sent)
    if win_end_rel != -1:
        win_end_rel += len(last_sent)
    else:
        win_end_rel = len(para_text)

    if win_start_rel == -1:
        win_start_rel = 0

    return para_start + win_start_rel, para_start + win_end_rel


# ── Main parsing logic ────────────────────────────────────────────────────────

def parse_file(
    path: Path,
    conn,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """
    Parse a manuscript file, extract citations, and insert rows into verse_refs.
    Returns the number of citations found.
    """
    filename = path.name
    meta = METADATA.get(filename, (None, None, None, None))
    author, title, year, ccel_url = meta

    print(f"\nParsing: {filename}")
    if author:
        print(f"  Author: {author}  |  Title: {title}")

    text = path.read_text(encoding="utf-8", errors="replace")

    if not dry_run:
        manuscript_id = upsert_manuscript(conn, filename, author, title, year, ccel_url)
        delete_refs_for_manuscript(conn, manuscript_id)

    count = 0
    rows = []

    for m in CITATION_RE.finditer(text):
        raw_book = m.group("book")
        raw_chapter = m.group("chapter")
        raw_verse = m.group("verse")

        if not raw_chapter:
            # No chapter found — ambiguous, skip
            continue

        book = _resolve_book(raw_book)
        if book is None:
            continue

        # Resolve chapter (Roman or Arabic)
        chapter_str = raw_chapter.strip().rstrip(".")
        if is_roman(chapter_str):
            chapter = roman_to_int(chapter_str)
        else:
            try:
                chapter = int(chapter_str)
            except ValueError:
                continue

        if chapter is None or chapter < 1 or chapter > book["chapters"]:
            continue

        verse_start, verse_end = _parse_verse_field(raw_verse)

        citation_offset = m.start()
        passage_start, passage_end = extract_passage_offsets(text, citation_offset)

        if verbose or dry_run:
            ref_str = f"{book['name']} {chapter}"
            if verse_start:
                ref_str += f":{verse_start}"
                if verse_end:
                    ref_str += f"-{verse_end}"
            passage_preview = text[passage_start:passage_start + 80].replace("\n", " ")
            print(f"  {ref_str:30s}  …{passage_preview}…")

        count += 1
        rows.append((
            manuscript_id if not dry_run else 0,
            book["name"],
            book["slug"],
            chapter,
            verse_start,
            verse_end,
            citation_offset,
            passage_start,
            passage_end,
        ))

    if not dry_run and rows:
        conn.executemany(
            """INSERT INTO verse_refs
               (manuscript_id, book, book_slug, chapter,
                verse_start, verse_end,
                citation_offset, passage_start_offset, passage_end_offset)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()

    print(f"  -> {count} citations found")
    return count


def show_stats(conn) -> None:
    print("\n-- Database statistics ----------------------------------------------")
    row = conn.execute("SELECT COUNT(*) AS n FROM manuscripts").fetchone()
    print(f"  Manuscripts:  {row['n']}")
    row = conn.execute("SELECT COUNT(*) AS n FROM verse_refs").fetchone()
    print(f"  Verse refs:   {row['n']}")
    print("\n  Top 20 most-referenced chapters:")
    rows = conn.execute("""
        SELECT book, chapter, COUNT(*) AS n
        FROM verse_refs
        GROUP BY book, chapter
        ORDER BY n DESC
        LIMIT 20
    """).fetchall()
    for r in rows:
        print(f"    {r['book']} {r['chapter']:>3}  —  {r['n']} refs")


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse manuscript files for Bible citations")
    ap.add_argument("files", nargs="*", help="Specific files to parse (default: all in manuscripts/)")
    ap.add_argument("--dry-run", action="store_true", help="Print matches without writing to DB")
    ap.add_argument("--verbose", "-v", action="store_true", help="Print each citation found")
    ap.add_argument("--stats", action="store_true", help="Show DB stats after parsing")
    args = ap.parse_args()

    create_schema()
    conn = get_connection()

    if args.files:
        paths = [Path(f) for f in args.files]
    else:
        paths = sorted(MANUSCRIPTS_DIR.glob("*.txt"))
        # Skip tiny/test files
        paths = [p for p in paths if p.stat().st_size > 1000]

    total = 0
    for path in paths:
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            continue
        total += parse_file(path, conn, dry_run=args.dry_run, verbose=args.verbose)

    print(f"\nTotal citations across all files: {total}")

    if args.stats and not args.dry_run:
        show_stats(conn)

    conn.close()


if __name__ == "__main__":
    main()
