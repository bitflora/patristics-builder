"""
Patch year fields in pre-built static JSON.zst files from the database,
avoiding the need to do a full builder rebuild.

Reads manuscript years from data/patristics.db and patches:
  - viewer/data/static/index.json.zst  (works list)
  - viewer/data/static/manuscripts/{id}.json.zst  (per-work files)
"""
import json
import sqlite3
from pathlib import Path

import zstandard

REPO = Path(__file__).parent.parent
DB_PATH = REPO / "data" / "patristics.db"
STATIC_DIR = REPO / "viewer" / "data" / "static"
MANUSCRIPTS_DIR = STATIC_DIR / "manuscripts"


def load_years_from_db() -> dict[int, int | None]:
    """Return {manuscript_id: year} for all manuscripts with year set."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id, year FROM manuscripts WHERE year IS NOT NULL").fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}


def read_zst(path: Path) -> bytes:
    dctx = zstandard.ZstdDecompressor()
    with path.open("rb") as f:
        return dctx.stream_reader(f).read()


def write_zst(path: Path, data: bytes) -> None:
    cctx = zstandard.ZstdCompressor(level=20)
    with path.open("wb") as f:
        f.write(cctx.compress(data))


def patch_index(years: dict[int, int | None]) -> int:
    path = STATIC_DIR / "index.json.zst"
    index = json.loads(read_zst(path))
    changed = 0
    for work in index.get("works", []):
        wid = work.get("id")
        if wid in years and work.get("year") != years[wid]:
            work["year"] = years[wid]
            changed += 1
    if changed:
        write_zst(path, json.dumps(index, separators=(",", ":")).encode())
    return changed


def patch_manuscripts(years: dict[int, int | None]) -> int:
    changed = 0
    for mid, year in years.items():
        path = MANUSCRIPTS_DIR / f"{mid}.json.zst"
        if not path.exists():
            continue
        ms = json.loads(read_zst(path))
        if ms.get("year") != year:
            ms["year"] = year
            write_zst(path, json.dumps(ms, separators=(",", ":")).encode())
            changed += 1
    return changed


def main():
    years = load_years_from_db()
    print(f"Loaded {len(years)} year entries from DB")

    idx_changed = patch_index(years)
    print(f"index.json.zst: updated {idx_changed} work entries")

    ms_changed = patch_manuscripts(years)
    print(f"manuscripts/*.json.zst: updated {ms_changed} files")


if __name__ == "__main__":
    main()
