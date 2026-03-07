---
shaping: true
---

# Database Audit & Cleanup — Shaping

## Audit Findings (2026-03-04)

Current state: **1,285 manuscripts**, **808,142 verse_refs**

| # | Finding | Count | Severity |
|---|---------|-------|----------|
| F1 | Authors stored as bare lowercase last-names (e.g. `calvin`, `luther`, `chesterton`) | 66 | Medium |
| F2 | Inverted/malformed author names (`of Clairvaux, Saint Bernard`, `M.A., D.D. George Adam Smith`) | ~15 | Low |
| F3 | Inverted verse ranges (`verse_start > verse_end`, e.g. `21-6` meaning `21-26`) | 386 total, 189 likely abbreviated | Medium |
| F4 | Exact duplicate verse_refs (same manuscript, offset, book, chapter, verse) | 94 | Low |
| F5 | Same citation offset, multiple different books assigned | 1,652 | Unknown |
| F6 | Zero verse_refs manuscripts | 401 | Low–High (depends on cause) |
| F7 | Bible/concordance/lexicon files in corpus (non-patristic) | ~6 known | High |
| F8 | Passage text windows very large (> 50,000 chars) | 4,039 | Medium |
| F9 | Inconsistent filename prefix: 28 manuscripts use plain names, 1,257 use `manuscripts/…` paths | 28 | Low |
| F10 | Duplicate ccel_url with same source_format | 1 | Low |

---

## Requirements (R)

| ID | Requirement | Status |
|----|-------------|--------|
| R0 | Remove manuscripts that don't belong in the corpus (Bibles, Greek NT, lexicons, concordances, dictionaries) | Core goal |
| R1 | Author names should be human-readable and consistently formatted via a hardcoded lookup map | Must-have |
| R2 | Exact duplicate verse_refs removed; inverted verse ranges fixed where detectable | Must-have |
| R3 | All changes require `--dry-run` mode; no silent mutations | Must-have |
| R4 | Zero-ref manuscripts are reported but not auto-deleted | Must-have |
| R5 | Cleanup is runnable repeatably (idempotent) | Nice-to-have |
| R6 | Passage sizes are out of scope (large passages are acceptable) | Out |
| R7 | Same-offset multi-book refs are valid and must not be touched | Out |

---

## S: Shapes

### A: Extend `cleanup.py` with new passes

Add new cleanup passes to the existing `cleanup.py` script, each guarded by a `--dry-run` flag.

| Part | Mechanism |
|------|-----------|
| A1 | Pass: remove known non-patristic works (Bibles, Greek NT, lexicons, dictionaries) by filename/title pattern |
| A2 | Pass: fix author names — map known bad names to correct ones via a lookup dict |
| A3 | Pass: delete exact duplicate verse_refs (same manuscript_id, citation_offset, book_slug, chapter, verse_start) |
| A4 | Pass: fix inverted verse ranges where verse_end < verse_start and both digits suggest abbreviated notation (e.g. 21→6 becomes 21→26) |
| A5 | Pass: report zero-ref manuscripts (no auto-delete; require explicit allowlist) |

### B: New audit script (`src/audit.py`) + separate fix script (`src/fix.py`)

Separate concerns: `audit.py` is read-only and emits a report; `fix.py` applies fixes with confirmation.

| Part | Mechanism |
|------|-----------|
| B1 | `audit.py` — queries DB and writes a structured report (JSON or text) listing all issues by category |
| B2 | `fix.py` — reads audit report or runs inline; applies fixes with `--dry-run` and explicit `--pass` flags |
| B3 | Author fix map in `fix.py` — maps lowercase slugs to canonical names |
| B4 | Non-patristic removal list — filenames/title patterns that identify Bible/concordance/lexicon entries |

---

## Fit Check: R × A (selected shape)

| Req | Requirement | Status | A |
|-----|-------------|--------|---|
| R0 | Remove non-corpus manuscripts (Bibles, lexicons, concordances, dictionaries) | Core goal | ✅ |
| R1 | Author names consistently formatted via hardcoded lookup map | Must-have | ✅ |
| R2 | Exact duplicate verse_refs removed; inverted verse ranges fixed where detectable | Must-have | ✅ |
| R3 | All changes require `--dry-run` mode; no silent mutations | Must-have | ✅ |
| R4 | Zero-ref manuscripts reported but not auto-deleted | Must-have | ✅ |
| R5 | Cleanup is runnable repeatably (idempotent) | Nice-to-have | ✅ |
| R6 | Passage sizes are out of scope | Out | — |
| R7 | Same-offset multi-book refs are valid and must not be touched | Out | — |

**Selected: Shape A** — extend `cleanup.py` with new passes.

---

## Shape A: Extend `cleanup.py` (selected)

| Part | Mechanism |
|------|-----------|
| A1 | Pass: hard-delete known non-corpus works (Bibles, Greek NTs, lexicons, concordances, dictionaries) by filename; add files + DB rows to `DELETE_ENTIRELY` |
| A2 | Pass: fix author names — `AUTHOR_FIXES` dict maps known bad values to canonical strings; `UPDATE manuscripts SET author=? WHERE author=?` |
| A3 | Pass: delete exact duplicate verse_refs — `GROUP BY manuscript_id, citation_offset, book_slug, chapter, verse_start HAVING COUNT(*) > 1`; keep lowest id, delete rest |
| A4 | Pass: fix abbreviated inverted verse ranges — where `verse_start >= 10` and `verse_end < 10` and `verse_end < verse_start`, reconstruct `verse_end` by prepending the leading digit(s) from `verse_start` |
| A5 | Pass: report zero-ref manuscripts (print list, no DB changes) |
