"""
Parse ThML XML files (downloaded by fetch_thml.py) and insert citations into SQLite.

Only <scripRef> elements that carry a 'parsed' attribute are processed.
The 'parsed' attribute format is semicolon-delimited segments, each:
    version|BookName|fromChapter|fromVerse|toChapter|toVerse

A clean plain-text version of each work is saved alongside the XML so that the
Go builder can extract passage text using the stored character offsets.

Usage:
  python src/parse_thml.py                         # parse all files in manifest
  python src/parse_thml.py ccel_thml/kempis/imit.xml   # single file
  python src/parse_thml.py --stats                 # show DB stats after parsing
  python src/parse_thml.py --dry-run               # print citations, no DB write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))

from bible_data import BY_NAME, ABBREV_LOOKUP, BOOKS
from db import get_connection, create_schema, delete_refs_for_manuscript, upsert_manuscript, DB_PATH
from parser import extract_passage_offsets, _normalize_creator

PROJECT_ROOT = Path(__file__).parent.parent
CCEL_THML_DIR = PROJECT_ROOT / "manuscripts" / "ccel_thml"
MANIFEST_PATH = CCEL_THML_DIR / "manifest.json"

# XML element local-names whose text content should be excluded from clean text.
# Their .tail (text after their closing tag) is still included — it belongs to
# the parent element's prose flow.
_SKIP_CONTENT_TAGS = frozenset({
    "ThML.head",
    "head",
    "note",       # footnotes / marginal notes
    "scripCom",   # scripture commentary marker (no prose content)
    "index",      # index entries
    "pb",         # page-break markers
    "milestone",
})

# Compiled once: matches numeric book prefix without a space, e.g. "1John" → "1 John"
_NUM_PREFIX_RE = re.compile(r"^([1-4])([A-Za-z])")


def _local(tag) -> str:
    """Strip XML namespace URI from a tag, returning just the local name.
    Returns empty string for non-element nodes (comments, PIs) whose tag is callable."""
    if callable(tag):
        return ""  # lxml Comment / ProcessingInstruction nodes
    return tag.split("}")[-1] if "}" in tag else tag


# ── Book name resolution ──────────────────────────────────────────────────────

# Build a normalised name → book dict lookup that covers full names and
# common CCEL variations ("Psalm" vs "Psalms", "1John" vs "1 John", etc.)
_NAME_LOOKUP: dict[str, dict] = {}
for _b in BOOKS:
    _NAME_LOOKUP[_b["name"].lower()] = _b
    # also register slug as lookup key ("1-corinthians" → same book)
    _NAME_LOOKUP[_b["slug"]] = _b
    # singular/plural variants for Psalms
    if _b["name"] == "Psalms":
        _NAME_LOOKUP["psalm"] = _b
    if _b["name"] == "Song of Solomon":
        _NAME_LOOKUP["song of songs"] = _b
        _NAME_LOOKUP["canticle of canticles"] = _b
    if _b["name"] == "Revelation":
        _NAME_LOOKUP["revelations"] = _b
        _NAME_LOOKUP["apocalypse"] = _b

# Merge abbreviation lookup as well (lower priority)
for _k, _v in ABBREV_LOOKUP.items():
    _NAME_LOOKUP.setdefault(_k, _v)


def _resolve_book_name(name: str) -> dict | None:
    """
    Map a book name from a ThML 'parsed' attribute segment to a canonical book dict.
    Returns None if the name cannot be resolved.
    """
    name = name.strip()
    lower = name.lower()

    if lower in _NAME_LOOKUP:
        return _NAME_LOOKUP[lower]

    # Handle "1John" → "1 john", "2Cor" → "2 cor", etc.
    spaced = _NUM_PREFIX_RE.sub(r"\1 \2", lower)
    if spaced != lower and spaced in _NAME_LOOKUP:
        return _NAME_LOOKUP[spaced]

    return None


# ── ThML XML parsing ──────────────────────────────────────────────────────────

def _load_xml(path: Path) -> etree._Element | None:
    """
    Parse a ThML file with lxml's recovery parser (handles missing DTD entities,
    malformed markup, etc.).  Returns the root element, or None on failure.
    """
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    try:
        tree = etree.parse(str(path), parser)
        return tree.getroot()
    except Exception as exc:
        print(f"  [XML error] {path.name}: {exc}", file=sys.stderr)
        return None


def _extract_metadata(root: etree._Element) -> dict:
    """
    Extract author, title, year, author_id, book_id from <ThML.head>.
    Returns a dict with those keys (values may be None if not found).
    """
    result = {"author": None, "title": None, "year": None, "author_id": None, "book_id": None}

    def find_text(tag: str) -> str | None:
        # Search anywhere in document for the given local tag name
        for el in root.iter():
            if _local(el.tag) == tag and el.text:
                return el.text.strip()
        return None

    raw_title = find_text("DC.Title")
    if raw_title:
        result["title"] = raw_title

    raw_creator = find_text("DC.Creator")
    if raw_creator:
        result["author"] = _normalize_creator(raw_creator)

    result["author_id"] = find_text("authorID")
    result["book_id"] = find_text("bookID")

    # Year: prefer a DC.Date with sub="Published" or sub="Original";
    # fall back to any DC.Date that contains a 4-digit year in the range 100–1999
    for el in root.iter():
        if _local(el.tag) != "DC.Date" or not el.text:
            continue
        year_m = re.search(r"\b(1\d{3}|[2-9]\d{2})\b", el.text)
        if year_m:
            candidate = int(year_m.group(1))
            sub = el.get("sub", "").lower()
            if sub in ("published", "original", "written", "composed"):
                result["year"] = candidate
                break
            if result["year"] is None:
                result["year"] = candidate

    return result


class _TextBuilder:
    """
    Walks an lxml element tree in document order, concatenating text nodes into
    a clean string while recording the char offset of every <scripRef> element.

    Elements in _SKIP_CONTENT_TAGS have their text/children suppressed; their
    .tail is still included (it belongs to the parent's prose flow).
    """

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._offset: int = 0
        # list of (element, offset_at_start_of_scripRef_text)
        self.scripref_hits: list[tuple[etree._Element, int]] = []

    def _append(self, text: str) -> None:
        if text:
            self._parts.append(text)
            self._offset += len(text)

    def walk(self, el: etree._Element, in_skip: bool = False) -> None:
        tag = _local(el.tag)
        entering_skip = tag in _SKIP_CONTENT_TAGS
        skip_content = in_skip or entering_skip

        if not skip_content:
            if tag == "scripRef":
                self.scripref_hits.append((el, self._offset))
            self._append(el.text)
            for child in el:
                self.walk(child, in_skip=False)
        else:
            # Still recurse into children so their tails (belonging to this
            # skipped element) are also suppressed, but we must visit them to
            # handle deeply-nested tails correctly.
            for child in el:
                self.walk(child, in_skip=True)

        # Tail always belongs to the parent; include it unless the parent is skipped.
        if not in_skip:
            self._append(el.tail)

    @property
    def text(self) -> str:
        return "".join(self._parts)


# ── Parsed-attribute citation decoding ───────────────────────────────────────

def _parse_parsed_attr(parsed: str) -> list[dict]:
    """
    Decode a ThML 'parsed' attribute string into a list of citation dicts.

    Format: version|Book|fromChapter|fromVerse|toChapter|toVerse
    Multiple citations separated by semicolons.

    Returns a list of dicts with keys:
        book_entry, chapter, verse_start, verse_end
    Segments that cannot be resolved are silently skipped.
    """
    results = []
    for segment in parsed.split(";"):
        segment = segment.strip()
        if not segment:
            continue
        parts = segment.split("|")
        if len(parts) != 6:
            continue
        _version, book_name, from_ch_s, from_v_s, to_ch_s, to_v_s = parts
        book_entry = _resolve_book_name(book_name)
        if book_entry is None:
            continue
        try:
            from_ch = int(from_ch_s)
            from_v = int(from_v_s)
            to_v = int(to_v_s)
        except ValueError:
            continue

        if from_ch < 1:
            continue  # whole-book reference; no useful chapter to store

        verse_start = from_v if from_v > 0 else None
        verse_end = to_v if (to_v > 0 and to_v != from_v) else None

        results.append(
            {
                "book_entry": book_entry,
                "chapter": from_ch,
                "verse_start": verse_start,
                "verse_end": verse_end,
            }
        )
    return results


# ── Per-file parsing ──────────────────────────────────────────────────────────

def parse_thml_file(
    xml_path: Path,
    conn,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """
    Parse one ThML XML file, extract citations, and insert into the DB.
    Saves a clean .txt companion file alongside the .xml.
    Returns the number of citation rows inserted.
    """
    root = _load_xml(xml_path)
    if root is None:
        return 0

    meta = _extract_metadata(root)
    author = meta["author"]
    title = meta["title"]
    year = meta["year"]
    author_id = meta["author_id"]
    book_id = meta["book_id"]

    # Fall back to directory/stem if metadata is absent
    if not author_id:
        author_id = xml_path.parent.name
    if not book_id:
        book_id = xml_path.stem

    ccel_url = f"https://ccel.org/ccel/{author_id}/{book_id}"
    # Filename relative to project root — this is what the builder will read
    txt_path = xml_path.with_suffix(".txt")
    rel_filename = str(txt_path.relative_to(PROJECT_ROOT)).replace("\\", "/")

    print(f"\nParsing: {xml_path.relative_to(PROJECT_ROOT)}")
    if author or title:
        print(f"  {author or '?'}  |  {title or '?'}")

    # Build clean text and collect scripRef offsets
    builder = _TextBuilder()
    builder.walk(root)
    clean_text = builder.text

    # Save clean text file (builder uses this via stored offsets)
    if not dry_run:
        txt_path.write_text(clean_text, encoding="utf-8")

    if not dry_run:
        # Conflict resolution: if a txt-sourced row exists for this ccel_url,
        # delete it so the ThML version takes priority.
        existing = conn.execute(
            "SELECT id, source_format FROM manuscripts WHERE ccel_url = ?", (ccel_url,)
        ).fetchone()
        if existing and existing["source_format"] == "txt":
            delete_refs_for_manuscript(conn, existing["id"])
            conn.execute("DELETE FROM manuscripts WHERE id = ?", (existing["id"],))

        manuscript_id = upsert_manuscript(
            conn, rel_filename,
            author=author, title=title, year=year,
            ccel_url=ccel_url, category="Other",
            source_format="thml",
        )
        delete_refs_for_manuscript(conn, manuscript_id)

    rows: list[tuple] = []

    for el, cite_offset in builder.scripref_hits:
        parsed_attr = el.get("parsed")
        if not parsed_attr:
            continue  # only process structurally-tagged citations

        citations = _parse_parsed_attr(parsed_attr)
        if not citations:
            continue

        passage_start, passage_end = extract_passage_offsets(clean_text, cite_offset)

        for cit in citations:
            be = cit["book_entry"]
            if verbose or dry_run:
                ref_str = f"{be['name']} {cit['chapter']}"
                if cit["verse_start"]:
                    ref_str += f":{cit['verse_start']}"
                    if cit["verse_end"]:
                        ref_str += f"-{cit['verse_end']}"
                preview = clean_text[passage_start:passage_start + 80].replace("\n", " ")
                print(f"  {ref_str:30s}  …{preview}…")

            rows.append((
                manuscript_id if not dry_run else 0,
                be["name"],
                be["slug"],
                cit["chapter"],
                cit["verse_start"],
                cit["verse_end"],
                cite_offset,
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

    print(f"  -> {len(rows)} citation rows")
    return len(rows)


# ── Entry point ───────────────────────────────────────────────────────────────

def _paths_from_manifest() -> list[Path]:
    if not MANIFEST_PATH.exists():
        print(f"Manifest not found at {MANIFEST_PATH}. Run fetch_thml.py first.", file=sys.stderr)
        sys.exit(1)
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        entries = json.load(f)
    paths = []
    for entry in entries:
        if entry.get("status") in ("downloaded", "cached") and entry.get("local_path"):
            p = Path(entry["local_path"])
            if p.exists():
                paths.append(p)
    return paths


def show_stats(conn) -> None:
    print("\n-- Database statistics ------------------------------------------")
    for fmt in ("thml", "txt"):
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM manuscripts WHERE source_format = ?", (fmt,)
        ).fetchone()
        print(f"  Manuscripts ({fmt}): {row['n']}")
    row = conn.execute("SELECT COUNT(*) AS n FROM verse_refs").fetchone()
    print(f"  Verse refs total: {row['n']}")
    print("\n  Top 20 most-referenced chapters:")
    rows = conn.execute("""
        SELECT book, chapter, COUNT(*) AS n
        FROM verse_refs GROUP BY book, chapter
        ORDER BY n DESC LIMIT 20
    """).fetchall()
    for r in rows:
        print(f"    {r['book']} {r['chapter']:>3}  —  {r['n']} refs")


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse ThML XML files for Bible citations")
    ap.add_argument("files", nargs="*", metavar="FILE",
                    help="Specific .xml files to parse (default: all from manifest)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print citations without writing to DB")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Print each citation found")
    ap.add_argument("--stats", action="store_true",
                    help="Show DB stats after parsing")
    args = ap.parse_args()

    create_schema()
    conn = get_connection()

    if args.files:
        paths = [Path(f) for f in args.files]
    else:
        paths = _paths_from_manifest()

    total = 0
    for path in paths:
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            continue
        total += parse_thml_file(path, conn, dry_run=args.dry_run, verbose=args.verbose)

    print(f"\nTotal citation rows: {total}")

    if args.stats and not args.dry_run:
        show_stats(conn)

    conn.close()


if __name__ == "__main__":
    main()
