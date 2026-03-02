# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Patristics (https://patristics-site.vercel.app/) correlates Bible verses with citations in patristic and theological texts. It is a multi-stage data pipeline with a static web frontend.

## Commands

### Go Builder
```bash
go run ./cmd/builder              # Build all static JSON files from the database
go run ./cmd/builder --book romans  # Build only one book's data
go run ./cmd/builder --clean       # Delete and rebuild viewer/data/static/
```

### Python Pipeline
```bash
python src/scraper.py              # Download works from CCEL
python src/parser.py               # Parse all manuscripts for Bible citations
python src/parser.py --dry-run     # Test parsing without writing to DB
python src/categorize.py           # Categorize manuscripts by subject
python src/cleanup.py              # Data cleanup and normalization
```

### Viewer (local dev)
```bash
python -m http.server 8000 --directory .
# Then open http://localhost:8000/viewer/
```

### Deploy
```bash
./deploy.sh   # Copy viewer files to the patristics-site deployment repo
```

## Architecture

Three-stage pipeline feeding a static SPA:

1. **Python Scraper** (`src/scraper.py`, `src/fetch_thml.py`) — Downloads manuscripts from CCEL in `.txt` or ThML (XML) format into `manuscripts/`
2. **Python Parser** (`src/parser.py`, `src/parse_thml.py`) — Regex-based Bible citation detection; stores manuscript metadata and verse references with Unicode code point offsets into `data/patristics.db` (SQLite, WAL mode). Idempotent: re-parsing deletes old refs for that manuscript first.
3. **Go Builder** (`cmd/builder/`) — Reads SQLite, extracts passage text via `[]rune` slicing (O(1) access matching Python's code point offsets), deduplicates passages via intern pattern, and writes zstd-compressed JSON to `viewer/data/static/`
4. **Viewer** (`viewer/`) — Vanilla JS SPA; three modes (Scripture, Works, Visualizations); lazily fetches and decompresses zstd JSON files client-side via `fzstd`

### Key Data Flow

```
manuscripts/ (text files)
    + data/patristics.db (SQLite)
         ↓ Go builder
viewer/data/static/
    index.json.zst           — book list, chapter ref counts, works metadata
    bible/{slug}.json.zst    — all refs for a book (by chapter)
    manuscripts/{id}.json.zst — all refs from a single work
    passages.json.zst        — global passage text dictionary (deduplicated)
```

Passages are stored once in `passages.json.zst` and referenced by key (`{filename}_{start}_{end}`) in book and manuscript files.

### Database Schema

**`manuscripts`**: `id, filename, author, title, year, ccel_url, category, source_format`
**`verse_refs`**: `id, manuscript_id, book, book_slug, chapter, verse_start, verse_end, citation_offset, passage_start_offset, passage_end_offset`
Indexed on `(book_slug, chapter)` and `(manuscript_id)`.

### Go Builder Internals (`cmd/builder/`)

- `main.go` — Entry point; opens DB, selectively loads only referenced manuscript files as `[]rune` slices, calls build stages in order, explicitly frees cache and runs `runtime.GC()` before parallel phase
- `build.go` — Core logic: `buildPassages()`, `buildBook()`, `buildAll()` (parallel, semaphore-bounded to CPU count), `buildWorks()`, `buildIndex()`
- `bible_data.go` — Canonical 82-book Bible metadata (OT + Deuterocanon + NT) with slugs, chapter counts, and order

### Python Modules (`src/`)

- `bible_data.py` — Authoritative Bible book list with abbreviation patterns for regex matching
- `db.py` — SQLite schema creation, `upsert_manuscript()`, `delete_refs_for_manuscript()`
- `parser.py` — Main citation parser; regex patterns handle Roman numerals, abbreviations, verse ranges
- `parse_thml.py` — Parses `<scripRef>` elements in ThML XML manuscripts

## Important Notes

- The SQLite database (`data/`) and manuscript files (`manuscripts/`) are gitignored and must exist locally to run the builder
- Python offsets are Unicode code points; Go uses `[]rune` to match this indexing
- Compression is zstd level 20; old gzip/uncompressed files are cleaned up by the builder automatically
- The pure Go SQLite driver (`modernc.org/sqlite`) is used — no CGo required
