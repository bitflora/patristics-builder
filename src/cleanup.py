"""
One-shot cleanup script: removes unwanted manuscripts from the database and
moves their source files to manuscripts/archive/.

Removals:
  1. bible_asv.txt    — full Bible translation, not a patristic work (hard delete)
  2. chesterton_queertrades.txt — false-positive Sirach refs purged; then archived
  3. Any manuscript left with zero verse_refs — archived

Usage:
  python src/cleanup.py [--dry-run]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db import get_connection, DB_PATH

MANUSCRIPTS_DIR = Path(__file__).parent.parent / "manuscripts"
ARCHIVE_DIR = MANUSCRIPTS_DIR / "archive"

# Manuscripts to delete entirely (file deleted, not archived).
DELETE_ENTIRELY = {"bible_asv.txt"}

# Manuscripts whose verse_refs are known false positives and should be purged
# before the zero-ref sweep runs.
PURGE_REFS = {"chesterton_queertrades.txt"}


def remove_manuscript(conn, manuscript_id: int, filename: str, dry_run: bool) -> None:
    """Delete verse_refs and the manuscripts row for the given id."""
    if not dry_run:
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
    # Step 1: hard-delete the ASV (and anything else in DELETE_ENTIRELY)  #
    # ------------------------------------------------------------------ #
    print("=== Step 1: Hard-delete manuscripts ===")
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
            remove_manuscript(conn, row["id"], filename, dry_run=False)
            if src.exists():
                src.unlink()
                print(f"  {prefix}  -> deleted file {src}")
            else:
                print(f"  {prefix}  -> file not found on disk: {src}")

    # ------------------------------------------------------------------ #
    # Step 2: purge false-positive refs                                   #
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
    # Step 3: archive all manuscripts with zero verse_refs                #
    # ------------------------------------------------------------------ #
    print("\n=== Step 3: Archive zero-ref manuscripts ===")
    zero_ref_rows = conn.execute(
        """
        SELECT m.id, m.filename, m.author, m.title
        FROM manuscripts m
        LEFT JOIN verse_refs vr ON vr.manuscript_id = m.id
        GROUP BY m.id
        HAVING COUNT(vr.id) = 0
        ORDER BY m.author, m.filename
        """
    ).fetchall()

    if not zero_ref_rows:
        print("  Nothing to archive.")
    else:
        if not dry:
            ARCHIVE_DIR.mkdir(exist_ok=True)

        for row in zero_ref_rows:
            src = MANUSCRIPTS_DIR / row["filename"]
            dst = ARCHIVE_DIR / row["filename"]
            print(f"  {prefix}ARCHIVE  {row['filename']}  ({row['author']}, \"{row['title']}\")")
            if not dry:
                if src.exists():
                    shutil.move(str(src), str(dst))
                    print(f"  {prefix}  -> moved to archive/")
                else:
                    print(f"  {prefix}  -> file not found on disk: {src}")
                remove_manuscript(conn, row["id"], row["filename"], dry_run=False)

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
