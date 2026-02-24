"""
JSON builder: reads the SQLite database and generates static JSON files
for the viewer.

Outputs:
  data/static/index.json.gz              — book list with per-chapter ref counts
  data/static/{book-slug}/{ch}.json.gz   — all references for a chapter
  data/static/works/{id}.json.gz         — all references from a single work

Usage:
  python src/builder.py               # build everything
  python src/builder.py --book romans # build only one book
  python src/builder.py --clean       # delete data/static/ before building
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bible_data import BOOKS, BY_SLUG
from db import get_connection, DB_PATH

MANUSCRIPTS_DIR = Path(__file__).parent.parent / "manuscripts"
STATIC_DIR = Path(__file__).parent.parent / "data" / "static"

MAX_PASSAGE_CHARS = 8000  # safety cap — ~1500 words


def _read_passage(filename: str, start: int, end: int) -> str:
    """Read a passage slice from a manuscript file."""
    path = MANUSCRIPTS_DIR / filename
    if not path.exists():
        return f"[source file not found: {filename}]"
    text = path.read_text(encoding="utf-8", errors="replace")
    snippet = text[start:end].strip()
    # Normalise whitespace (collapse multiple blank lines)
    snippet = re.sub(r'\n{3,}', '\n\n', snippet)
    return snippet[:MAX_PASSAGE_CHARS]


def _verse_label(verse_start: int | None, verse_end: int | None) -> str | None:
    """Return the verse label string, or None for a chapter-level reference."""
    if verse_start is None:
        return None
    if verse_end is None or verse_end == verse_start:
        return str(verse_start)
    return f"{verse_start}-{verse_end}"


def build_chapter(
    conn,
    book_slug: str,
    chapter: int,
    out_dir: Path,
) -> int:
    """Build one chapter JSON file. Returns number of references written."""
    book_info = BY_SLUG.get(book_slug)
    if not book_info:
        return 0

    rows = conn.execute(
        """
        SELECT
            vr.verse_start, vr.verse_end,
            vr.passage_start_offset, vr.passage_end_offset,
            m.id AS manuscript_id,
            m.filename, m.author, m.title, m.year, m.ccel_url
        FROM verse_refs vr
        JOIN manuscripts m ON m.id = vr.manuscript_id
        WHERE vr.book_slug = ? AND vr.chapter = ?
        ORDER BY vr.verse_start NULLS LAST, m.author, m.title
        """,
        (book_slug, chapter),
    ).fetchall()

    if not rows:
        return 0

    # Deduplicate works — map manuscript_id → local index in the works array
    works_seen: dict[int, int] = {}
    works_list: list[dict] = []

    refs: list[dict] = []
    for row in rows:
        mid = row["manuscript_id"]
        if mid not in works_seen:
            works_seen[mid] = len(works_list)
            works_list.append({
                "id": len(works_list),
                "author": row["author"] or "Unknown",
                "title": row["title"] or row["filename"],
                "year": row["year"],
                "filename": row["filename"],
            })
        work_idx = works_seen[mid]

        passage_text = _read_passage(
            row["filename"],
            row["passage_start_offset"],
            row["passage_end_offset"],
        )

        ref = {
            "v": _verse_label(row["verse_start"], row["verse_end"]),
            "w": work_idx,
            "text": passage_text,
        }
        refs.append(ref)

    payload = {
        "book": book_info["name"],
        "chapter": chapter,
        "works": works_list,
        "refs": refs,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{chapter}.json.gz"
    with gzip.open(out_file, "wt", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return len(refs)


def build_index(conn, only_book: str | None = None) -> None:
    """Write data/static/index.json.gz."""

    # All (book_slug, chapter, count) tuples with at least one reference
    rows = conn.execute(
        """
        SELECT book_slug, chapter, COUNT(*) AS n
        FROM verse_refs
        GROUP BY book_slug, chapter
        ORDER BY book_slug, chapter
        """
    ).fetchall()

    # Build a map: book_slug → {chapter → count}
    chapter_counts: dict[str, dict[int, int]] = {}
    for r in rows:
        chapter_counts.setdefault(r["book_slug"], {})[r["chapter"]] = r["n"]

    # All manuscript metadata for the global works list, with ref counts
    works_rows = conn.execute(
        """
        SELECT m.id, m.author, m.title, m.year, m.filename,
               COUNT(vr.id) AS ref_count
        FROM manuscripts m
        LEFT JOIN verse_refs vr ON vr.manuscript_id = m.id
        GROUP BY m.id
        ORDER BY m.author, m.title
        """
    ).fetchall()
    global_works = [
        {
            "id": r["id"],
            "author": r["author"] or "Unknown",
            "title": r["title"] or r["filename"],
            "year": r["year"],
            "ref_count": r["ref_count"],
        }
        for r in works_rows
    ]

    books_out = []
    for book in BOOKS:
        slug = book["slug"]
        if only_book and slug != only_book:
            continue
        counts = chapter_counts.get(slug, {})
        if not counts:
            continue
        chapters_out = [
            {"ch": ch, "count": counts[ch]}
            for ch in range(1, book["chapters"] + 1)
            if ch in counts
        ]
        if chapters_out:
            books_out.append({
                "name": book["name"],
                "slug": slug,
                "order": book["order"],
                "chapters": chapters_out,
            })

    payload = {"books": books_out, "works": global_works}
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    index_path = STATIC_DIR / "index.json.gz"
    with gzip.open(index_path, "wt", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"Wrote {index_path}  ({len(books_out)} books with references)")


def build_all(conn, only_book: str | None = None) -> None:
    """Build all chapter JSON files."""
    rows = conn.execute(
        """
        SELECT DISTINCT book_slug, chapter
        FROM verse_refs
        ORDER BY book_slug, chapter
        """
    ).fetchall()

    total_refs = 0
    total_files = 0
    for row in rows:
        slug = row["book_slug"]
        if only_book and slug != only_book:
            continue
        ch = row["chapter"]
        out_dir = STATIC_DIR / slug
        n = build_chapter(conn, slug, ch, out_dir)
        if n:
            total_refs += n
            total_files += 1
            print(f"  {slug}/{ch}.json.gz  ({n} refs)")

    print(f"\nBuilt {total_files} chapter files, {total_refs} total references.")


def build_works(conn) -> None:
    """Build per-work JSON files in data/static/works/{id}.json.gz"""
    works_dir = STATIC_DIR / "works"
    works_dir.mkdir(parents=True, exist_ok=True)

    manuscripts = conn.execute(
        "SELECT id, author, title, year, filename FROM manuscripts ORDER BY id"
    ).fetchall()

    total_files = 0
    for m in manuscripts:
        rows = conn.execute(
            """
            SELECT vr.book, vr.book_slug, vr.chapter,
                   vr.verse_start, vr.verse_end,
                   vr.passage_start_offset, vr.passage_end_offset
            FROM verse_refs vr
            WHERE vr.manuscript_id = ?
            ORDER BY vr.book_slug, vr.chapter, vr.verse_start NULLS LAST
            """,
            (m["id"],),
        ).fetchall()

        if not rows:
            continue

        # Read the manuscript file once, then slice offsets
        path = MANUSCRIPTS_DIR / m["filename"]
        file_text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else None

        refs = []
        for row in rows:
            if file_text is not None:
                snippet = file_text[row["passage_start_offset"]:row["passage_end_offset"]].strip()
                snippet = re.sub(r'\n{3,}', '\n\n', snippet)
                passage_text = snippet[:MAX_PASSAGE_CHARS]
            else:
                passage_text = f"[source file not found: {m['filename']}]"

            refs.append({
                "book": row["book"],
                "book_slug": row["book_slug"],
                "chapter": row["chapter"],
                "v": _verse_label(row["verse_start"], row["verse_end"]),
                "text": passage_text,
            })

        payload = {
            "id": m["id"],
            "author": m["author"] or "Unknown",
            "title": m["title"] or m["filename"],
            "year": m["year"],
            "filename": m["filename"],
            "refs": refs,
        }

        out_file = works_dir / f"{m['id']}.json.gz"
        with gzip.open(out_file, "wt", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

        total_files += 1
        print(f"  works/{m['id']}.json.gz  ({len(refs)} refs)")

    print(f"\nBuilt {total_files} work files.")


def cleanup_uncompressed() -> None:
    """Delete any plain *.json files (not *.json.gz) under STATIC_DIR."""
    if not STATIC_DIR.exists():
        return
    removed = 0
    for p in STATIC_DIR.rglob("*.json"):
        if p.suffix == ".json":   # .json.gz files have suffix ".gz"
            p.unlink()
            removed += 1
    if removed:
        print(f"Cleaned up {removed} uncompressed .json file(s).")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate static JSON from patristics.db")
    ap.add_argument("--book", metavar="SLUG", help="Only build files for this book slug")
    ap.add_argument("--clean", action="store_true", help="Delete data/static/ before building")
    args = ap.parse_args()

    if args.clean and STATIC_DIR.exists():
        shutil.rmtree(STATIC_DIR)
        print(f"Removed {STATIC_DIR}")

    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}. Run parser.py first.", file=sys.stderr)
        sys.exit(1)

    conn = get_connection()
    build_all(conn, only_book=args.book)
    build_index(conn, only_book=args.book)
    if not args.book:          # skip per-work rebuild on partial builds
        build_works(conn)
    cleanup_uncompressed()     # always clean up uncompressed files
    conn.close()


if __name__ == "__main__":
    main()
