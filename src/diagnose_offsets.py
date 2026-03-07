"""
Diagnose ThML citation offset mismatches.

Reads the DB and on-disk .txt/.xml files without re-parsing or writing anything.

Usage:
  python src/diagnose_offsets.py <filename_stem> <book> <chapter> [<verse>]
  python src/diagnose_offsets.py wp_matt john 11 33
  python src/diagnose_offsets.py --all-thml          # check all ThML manuscripts

For each matching DB row the script:
  1. Slices the .txt file at [citation_offset : citation_offset+120] and shows what's there.
  2. Slices at [passage_start_offset : passage_end_offset] and shows the passage preview.
  3. Re-walks the XML with _TextBuilder, finds the matching <scripRef>, and computes a
     fresh offset — then compares it against the stored value to distinguish H1/H2/H3.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db import get_connection, DB_PATH
from parse_thml import _TextBuilder, _local, PROJECT_ROOT, CCEL_THML_DIR, _load_xml

PREVIEW_LEN = 120
CONTEXT_AROUND = 50


def _short(text: str, start: int, length: int) -> str:
    snippet = text[start : start + length].replace("\n", " ").strip()
    return repr(snippet)


def diagnose_row(row: sqlite3.Row, verbose: bool = False) -> bool:
    """
    Diagnose one verse_ref row.  Returns True if offsets look correct.
    """
    filename = row["filename"]          # relative path like manuscripts/ccel_thml/watts/wp_matt.txt
    citation_offset = row["citation_offset"]
    passage_start = row["passage_start_offset"]
    passage_end = row["passage_end_offset"]
    book = row["book"]
    chapter = row["chapter"]
    verse_start = row["verse_start"]
    verse_end = row["verse_end"]

    ref_str = f"{book} {chapter}"
    if verse_start:
        ref_str += f":{verse_start}"
        if verse_end:
            ref_str += f"-{verse_end}"

    stem = Path(filename).stem  # e.g. "wp_matt"

    # Normalise to repo-root-relative path, matching Go builder's behaviour:
    # filenames without a "manuscripts/" prefix need it prepended.
    norm_filename = filename if filename.startswith("manuscripts/") else "manuscripts/" + filename

    print(f"\n{'='*70}")
    print(f"  {stem}  |  {ref_str}")
    print(f"  DB citation_offset:      {citation_offset}")
    print(f"  DB passage window:       [{passage_start} : {passage_end}]  ({passage_end - passage_start} chars)")

    # ── Step 1: read the .txt file ─────────────────────────────────────────────
    txt_path = PROJECT_ROOT / norm_filename
    if not txt_path.exists():
        print(f"  [ERROR] .txt file not found: {txt_path}")
        return False

    clean_text = txt_path.read_text(encoding="utf-8")
    txt_len = len(clean_text)

    if citation_offset >= txt_len:
        print(f"  [ERROR] citation_offset {citation_offset} >= file length {txt_len}  → DEFINITE STALE (H2)")
        return False

    text_at_cite = _short(clean_text, citation_offset, PREVIEW_LEN)
    passage_preview = _short(clean_text, passage_start, 80)

    print(f"  Text at citation_offset: {text_at_cite}")
    print(f"  Passage preview:         {passage_preview}")

    # ── Step 2: recompute offset from XML ─────────────────────────────────────
    xml_path = txt_path.with_suffix(".xml")
    if not xml_path.exists():
        print(f"  [SKIP] No .xml found at {xml_path} — cannot recompute offset")
        return True

    root = _load_xml(xml_path)
    if root is None:
        print(f"  [SKIP] Could not parse {xml_path}")
        return True

    builder = _TextBuilder()
    builder.walk(root)
    rebuilt_text = builder.text

    # Find all scripRef hits that match this citation's book/chapter/verse
    from parse_thml import _parse_parsed_attr
    matching_hits: list[tuple] = []
    for el, offset in builder.scripref_hits:
        parsed_attr = el.get("parsed")
        if not parsed_attr:
            continue
        for cit in _parse_parsed_attr(parsed_attr):
            be = cit["book_entry"]
            if (be["name"].lower() == book.lower()
                    and cit["chapter"] == chapter
                    and (verse_start is None or cit["verse_start"] == verse_start)):
                matching_hits.append((el, offset, cit))
                break

    if not matching_hits:
        print(f"  [WARN] No matching <scripRef> found in XML for {ref_str}")
        return True

    # ── Step 3: compare ────────────────────────────────────────────────────────
    # Find the hit (if any) whose recomputed offset matches the DB citation_offset.
    exact_match = None
    for el, fresh_offset, cit in matching_hits:
        if fresh_offset == citation_offset:
            exact_match = (el, fresh_offset, cit)
            break

    if exact_match is not None:
        el, fresh_offset, cit = exact_match
        scripref_text = (el.text or "").strip()
        txt_at_offset = clean_text[citation_offset : citation_offset + max(len(scripref_text), 40)]
        if scripref_text and scripref_text in txt_at_offset:
            print(f"  scripRef.text:           {repr(scripref_text)}")
            print(f"  ✓ Offset is correct and scripRef text is present at that location")
        elif scripref_text:
            print(f"  scripRef.text:           {repr(scripref_text)}")
            print(f"  ~ Offset matches recomputed, but scripRef text not literally present")
            print(f"    .txt has: {repr(txt_at_offset[:60])}")
        else:
            print(f"  ✓ Offset matches (scripRef has no text node)")
        return True
    else:
        # No hit matched — genuine mismatch
        print(f"  ✗ NO matching scripRef at citation_offset {citation_offset}")
        print(f"  Recomputed offsets for '{ref_str}' in this XML:")
        for el, fresh_offset, cit in matching_hits:
            delta = fresh_offset - citation_offset
            scripref_text = (el.text or "").strip()
            rebuilt_at = _short(rebuilt_text, fresh_offset, 80)
            print(f"    offset {fresh_offset:>8d}  (delta: {delta:+d})  scripRef={repr(scripref_text)}")
            print(f"      text: {rebuilt_at}")
        if rebuilt_text == clean_text:
            print(f"  .txt matches rebuilt text  → DB offset is stale (H1: TextBuilder bug at parse time)")
        else:
            print(f"  .txt ≠ rebuilt text  → .txt file regenerated after DB was populated (H2: stale .txt)")
        return False


import sqlite3


def run_query(conn, filename_stem: str | None, book: str | None,
              chapter: int | None, verse: int | None) -> list:
    parts = ["SELECT vr.*, m.filename FROM verse_refs vr JOIN manuscripts m ON m.id = vr.manuscript_id"]
    where = ["m.source_format = 'thml'"]
    params: list = []

    if filename_stem:
        where.append("m.filename LIKE ?")
        params.append(f"%/{filename_stem}.txt")

    if book:
        where.append("LOWER(vr.book) LIKE ?")
        params.append(f"%{book.lower()}%")

    if chapter is not None:
        where.append("vr.chapter = ?")
        params.append(chapter)

    if verse is not None:
        where.append("(vr.verse_start = ? OR (vr.verse_start <= ? AND (vr.verse_end IS NULL OR vr.verse_end >= ?)))")
        params.extend([verse, verse, verse])

    if where:
        parts.append("WHERE " + " AND ".join(where))

    parts.append("ORDER BY m.filename, vr.citation_offset")
    sql = " ".join(parts)
    return conn.execute(sql, params).fetchall()


def main() -> None:
    ap = argparse.ArgumentParser(description="Diagnose ThML citation offset mismatches")
    ap.add_argument("filename_stem", nargs="?", metavar="STEM",
                    help="Manuscript file stem, e.g. wp_matt")
    ap.add_argument("book", nargs="?", metavar="BOOK",
                    help="Bible book name (partial match), e.g. john")
    ap.add_argument("chapter", nargs="?", type=int, metavar="CHAPTER")
    ap.add_argument("verse", nargs="?", type=int, metavar="VERSE")
    ap.add_argument("--all-thml", action="store_true",
                    help="Check all ThML manuscripts (sample first N rows per file)")
    ap.add_argument("--limit", type=int, default=5, metavar="N",
                    help="Max rows to diagnose per manuscript when using --all-thml (default 5)")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = get_connection()

    if args.all_thml:
        # For each ThML manuscript, pick up to --limit rows
        manuscripts = conn.execute(
            "SELECT id, filename FROM manuscripts WHERE source_format='thml' ORDER BY filename"
        ).fetchall()
        if not manuscripts:
            print("No ThML manuscripts found in DB.")
            return
        total_bad = 0
        for ms in manuscripts:
            rows = conn.execute(
                "SELECT vr.*, m.filename FROM verse_refs vr JOIN manuscripts m ON m.id = vr.manuscript_id "
                "WHERE vr.manuscript_id = ? LIMIT ?",
                (ms["id"], args.limit)
            ).fetchall()
            for row in rows:
                ok = diagnose_row(row)
                if not ok:
                    total_bad += 1
        print(f"\n{'='*70}")
        print(f"Done. {total_bad} mismatch(es) found across sampled rows.")
    else:
        if not any([args.filename_stem, args.book, args.chapter, args.verse]):
            ap.print_help()
            sys.exit(1)

        rows = run_query(conn, args.filename_stem, args.book, args.chapter, args.verse)
        if not rows:
            print("No matching rows found in DB.")
            sys.exit(0)

        print(f"Found {len(rows)} matching row(s).")
        bad = 0
        for row in rows:
            ok = diagnose_row(row)
            if not ok:
                bad += 1

        print(f"\n{'='*70}")
        print(f"Summary: {len(rows)} row(s) checked, {bad} mismatch(es).")

    conn.close()


if __name__ == "__main__":
    main()
