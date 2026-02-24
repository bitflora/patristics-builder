# Data Format Design Decisions

## Pipeline

```
manuscripts/*.txt  →  parser.py  →  data/patristics.db  →  builder.py  →  data/static/**  →  viewer/
```

## SQLite Schema

The database is a **pure index** — no passage text is stored in it. All text lives only in the source manuscript files and is extracted at build time using character offsets.

### `manuscripts` table
| Column   | Type    | Notes                                      |
|----------|---------|--------------------------------------------|
| id       | INTEGER | PK                                         |
| filename | TEXT    | Relative path under `manuscripts/`         |
| author   | TEXT    | e.g. "John Owen"                           |
| title    | TEXT    | e.g. "Of the Mortification of Sin"         |
| year     | INTEGER | Publication year (NULL if unknown)         |
| ccel_url | TEXT    | Source URL on ccel.org                     |

### `verse_refs` table
| Column               | Type    | Notes                                                |
|----------------------|---------|------------------------------------------------------|
| id                   | INTEGER | PK                                                   |
| manuscript_id        | INTEGER | FK → manuscripts.id                                  |
| book                 | TEXT    | Canonical name, e.g. "Romans"                       |
| book_slug            | TEXT    | URL/path-safe name, e.g. "romans"                   |
| chapter              | INTEGER | 1-based                                              |
| verse_start          | INTEGER | NULL = whole-chapter reference                       |
| verse_end            | INTEGER | NULL = single verse (when verse_start is set)        |
| citation_offset      | INTEGER | Char offset of the citation text in the manuscript   |
| passage_start_offset | INTEGER | Char offset of the passage window start              |
| passage_end_offset   | INTEGER | Char offset of the passage window end                |

**Indexes:** `(book_slug, chapter)` and `(manuscript_id)`

## JSON Output

### Per-chapter: `data/static/{book-slug}/{chapter}.json`
```json
{
  "book": "Romans",
  "chapter": 8,
  "works": [
    {"id": 1, "author": "John Owen", "title": "Mortification of Sin", "year": 1656, "filename": "mort.txt"}
  ],
  "refs": [
    {"v": "13",    "w": 1, "text": "...passage (≤10 sentences)..."},
    {"v": "13-17", "w": 1, "text": "..."},
    {"v": null,    "w": 2, "text": "..."}
  ]
}
```
- `v: null` → whole-chapter reference
- `v: "13"` → single verse
- `v: "13-17"` → verse range
- `w` → 0-based index into the `works` array (deduplicates author/title metadata)

### Index: `data/static/index.json`
```json
{
  "books": [
    {
      "name": "Genesis", "slug": "genesis", "order": 1,
      "chapters": [{"ch": 1, "count": 12}, {"ch": 2, "count": 8}]
    }
  ],
  "works": [
    {"id": 1, "author": "John Owen", "title": "Mortification of Sin", "year": 1656}
  ]
}
```
Chapter counts in the index allow the viewer to display reference heatmaps without loading all chapter files.

## Passage Window

- A "passage" is the paragraph surrounding the citation, capped at **10 sentences**.
- Paragraph boundaries = blank lines in the source text.
- If a paragraph has ≤10 sentences → take the whole paragraph.
- If >10 sentences → take a window of 10 centered on the citation sentence.
- The `passage_start_offset` / `passage_end_offset` in `verse_refs` record the precomputed window.
- To change the window size: re-run `builder.py` (no need to re-parse).

## Why Offset-Based Storage

1. **No text duplication** — the DB is small (~KB range per manuscript)
2. **Flexible rebuild** — change window size, re-run builder only
3. **Auditability** — `citation_offset` lets you verify exactly where in the source file a reference was found
4. **One passage, many refs** — when "Rom. viii. 13 and Gal. v. 16" appear in the same sentence, both refs share the same passage offsets; builder emits the text twice (once per chapter file)

## Citation Format Support

The parser handles:
- `Rom. viii. 13` — abbreviated book, Roman numeral chapter
- `Romans 8:13` — full book name, Arabic numerals
- `St John vi. 44` / `St. John 6:44` — "Saint" prefix
- `1 Cor. 15:10` — numbered books
- `Ps. xxvii.` — chapter-only reference (no verse)
- `Rom. viii. 13-17` — verse range
- `Rom. x. 3, 4` — multiple verses, same chapter
- All Deuterocanonical books: Sirach, Tobit, Judith, Wisdom, Baruch, 1-2 Maccabees, etc.

## Book Slugs

Slugs are lowercase, spaces replaced with hyphens, numbers kept:
- "1 Corinthians" → "1-corinthians"
- "Song of Solomon" → "song-of-solomon"
- "Sirach" → "sirach"
