"""
Database schema creation and shared utilities for patristics.db.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "patristics.db"


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_schema(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS manuscripts (
                id       INTEGER PRIMARY KEY,
                filename TEXT NOT NULL UNIQUE,
                author   TEXT,
                title    TEXT,
                year     INTEGER,
                ccel_url TEXT,
                category TEXT
            );

            CREATE TABLE IF NOT EXISTS verse_refs (
                id                   INTEGER PRIMARY KEY,
                manuscript_id        INTEGER NOT NULL REFERENCES manuscripts(id),
                book                 TEXT NOT NULL,
                book_slug            TEXT NOT NULL,
                chapter              INTEGER NOT NULL,
                verse_start          INTEGER,
                verse_end            INTEGER,
                citation_offset      INTEGER NOT NULL,
                passage_start_offset INTEGER NOT NULL,
                passage_end_offset   INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_refs_book_chapter
                ON verse_refs(book_slug, chapter);

            CREATE INDEX IF NOT EXISTS idx_refs_manuscript
                ON verse_refs(manuscript_id);
        """)
        # Migration: add source_format column if absent (existing rows default to 'txt')
        cols = {row[1] for row in conn.execute("PRAGMA table_info(manuscripts)")}
        if "source_format" not in cols:
            conn.execute(
                "ALTER TABLE manuscripts ADD COLUMN source_format TEXT NOT NULL DEFAULT 'txt'"
            )
    conn.close()
    print(f"Schema created at {db_path}")


def upsert_manuscript(conn: sqlite3.Connection, filename: str, author: str | None = None,
                       title: str | None = None, year: int | None = None,
                       ccel_url: str | None = None, category: str | None = None,
                       source_format: str = "txt") -> int:
    """Insert or update a manuscript record, returning its id."""
    cur = conn.execute(
        "SELECT id FROM manuscripts WHERE filename = ?", (filename,)
    )
    row = cur.fetchone()
    if row:
        conn.execute(
            """UPDATE manuscripts SET author=?, title=?, year=?, ccel_url=?, category=?,
               source_format=? WHERE id=?""",
            (author, title, year, ccel_url, category, source_format, row["id"])
        )
        return row["id"]
    cur = conn.execute(
        """INSERT INTO manuscripts (filename, author, title, year, ccel_url, category, source_format)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (filename, author, title, year, ccel_url, category, source_format)
    )
    return cur.lastrowid


def delete_refs_for_manuscript(conn: sqlite3.Connection, manuscript_id: int) -> None:
    """Remove all verse_refs for a manuscript so it can be re-parsed cleanly."""
    conn.execute("DELETE FROM verse_refs WHERE manuscript_id = ?", (manuscript_id,))


if __name__ == "__main__":
    create_schema()
