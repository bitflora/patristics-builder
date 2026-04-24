---
shaping: true
---

# ANF Author Attribution — Shaping

## Frame

**Source:** "The manuscripts/ccel_thml/schaff/anf*.xml works show Philip Schaff as the author, but really they are compilations of works by many different authors. I would like these chapters attributed to the correct author."

**Problem:** The 10 ANF volumes (anf01.xml–anf10.xml) are parsed as single manuscripts attributed to Philip Schaff. Each volume is actually a compilation of distinct works by different church fathers (Clement of Rome, Ignatius, Polycarp, Justin Martyr, Irenaeus, etc.). Every citation currently appears under "Philip Schaff" in the viewer — hiding the real authors and making author filtering useless for ANF material.

**Outcome:** Citations from ANF volumes display the correct patristic author. Each author's work appears as its own entry in the works list, with correct year and title.

---

## Requirements (R)

| ID | Requirement | Status |
|----|-------------|--------|
| R0 | Citations from ANF volumes show the actual patristic author, not Philip Schaff | Core goal |
| R1 | Each distinct author's work within an ANF volume is a separate entry in the works list | Must-have |
| R2 | Re-parsing ANF files remains idempotent (re-run deletes old refs, inserts fresh ones) | Must-have |
| R3 | Author display name is human-readable (e.g., "Clement of Rome", not "clement_rome") | Must-have |
| R4 | Sub-work year reflects the patristic author's date, not the ANF publication date | Must-have |

---

## CURRENT: How ANF is Parsed Today

**Key facts:**
- `anf01.xml` contains ~6 works by different authors, separated by nested `<ThML.head>` blocks
- Each nested header has `<authorID>`, `<workID>`, `<DC.Title>` (e.g., `clement_rome`, `first_epistle_to_the_corinthians`)
- `parse_thml.py` extracts only the **top-level** metadata → gets "Philip Schaff" as author
- DB: `manuscripts` has `UNIQUE (filename)`; `upsert_manuscript` looks up by filename alone
- Go builder: `SELECT DISTINCT m.filename` to load files — naturally handles multiple records per filename
- Verse offsets reference positions in the full file; all sub-works share the same file

**ANF XML structure (relevant excerpt):**
```xml
<!-- Top-level: Schaff as editor -->
<DC.Creator sub="Editor">Philip Schaff</DC.Creator>

<!-- Per-work nested header, appears ~6x per volume -->
<ThML.head>
  <electronicEdInfo>
    <authorID>clement_rome</authorID>
    <workID>first_epistle_to_the_corinthians</workID>
    <DC><DC.Title>First Epistle to the Corinthians</DC.Title></DC>
  </electronicEdInfo>
</ThML.head>
<div1 ...>  <!-- work content begins here, offsets valid in this file -->
```

---

## Shape D: Multiple manuscripts per file, composite (filename, work_key) unique key

Multiple manuscript records share the same `filename` (the real file path). A new `work_key` column distinguishes sub-works within a compilation. For normal manuscripts `work_key = ''`; for ANF sub-works `work_key = authorID`.

**Why the builder needs no changes:** It already uses `SELECT DISTINCT m.filename` to load files, so N records sharing a filename still load the file once. Passage extraction by offset works identically. Each sub-manuscript gets its own `manuscripts/{id}.json.zst` output (correct author, title, year).

| Part | Mechanism |
|------|-----------|
| D1 | `db.py`: drop `UNIQUE (filename)` constraint; add `work_key TEXT NOT NULL DEFAULT ''`; add `UNIQUE (filename, work_key)` |
| D2 | `db.py`: update `upsert_manuscript` to accept `work_key=''`; SELECT/INSERT on `(filename, work_key)` |
| D3 | `db.py`: add `delete_manuscripts_for_file(conn, filename)` — deletes all manuscript records (and refs via FK) sharing the same filename |
| D4 | `parse_thml.py`: detect ANF files (presence of nested `<ThML.head>` blocks); call `delete_manuscripts_for_file` before re-parsing |
| D5 | `parse_thml.py`: walk nested `<ThML.head>` blocks; emit one `upsert_manuscript` per section with `work_key=authorID` |
| D6 | `parse_thml.py`: hardcoded `AUTHOR_MAP` dict: `authorID → (display_name, year)` covering all ANF authors |
| D7 | CCEL URL: use the ANF volume URL (e.g., `https://ccel.org/ccel/schaff/anf01`) for all sub-works; individual work URLs are uncertain |

**Idempotency flow for ANF:**
1. `delete_manuscripts_for_file(conn, 'anf01.xml')` — deletes all sub-manuscripts + their verse_refs
2. Walk nested `<ThML.head>` blocks, upsert each, parse refs as before

**FK cascade note:** `verse_refs` has `REFERENCES manuscripts(id)` but no `ON DELETE CASCADE`. D3 must delete refs before manuscripts, or add `ON DELETE CASCADE` to the FK (requires recreating the table — simpler to just delete refs first in the function).

---

## Fit Check

| Req | Requirement | Status | D |
|-----|-------------|--------|---|
| R0 | Citations show actual patristic author | Core goal | ✅ |
| R1 | Each author's work is a separate entry in the works list | Must-have | ✅ |
| R2 | Re-parsing ANF files remains idempotent | Must-have | ✅ |
| R3 | Display name is human-readable | Must-have | ✅ |
| R4 | Sub-work year reflects patristic author's date | Must-have | ✅ |

---

## Open Questions

1. **CCEL URLs:** Should sub-works get individual CCEL URLs (e.g., `https://ccel.org/ccel/clement_rome/first_epistle_to_the_corinthians`) if the `<bkgID>` element provides the path? Or default to the volume URL?
2. **ANF scope:** Do all 10 ANF volumes need parsing updates, or just a subset? Some volumes (e.g., anf10) may have different structure or minimal content.
3. **Author years:** What years should be used for authors where the date is uncertain? (e.g., Clement of Rome ~96 CE, Polycarp ~155 CE) — these go in the AUTHOR_MAP.
