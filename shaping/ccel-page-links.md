---
shaping: true
---

# CCEL Page Links — Shaping

## Frame

**Problem:** Each passage in the viewer links to the CCEL work homepage, not the specific location where the passage appears. Users who want to read more context must manually find the right page.

**Outcome:** Every passage displayed in the viewer has a "View on CCEL" link that opens to the exact citation location in the CCEL text.

---

## Requirements (R)

| ID | Requirement | Status |
|----|-------------|--------|
| R0 | Each passage links to its exact citation location in CCEL, not just the work root | Core goal |
| R1 | ThML-sourced manuscripts (~1,266 works) get citation-specific links | Must-have |
| R2 | TXT-sourced manuscripts (~21 works) gracefully fall back to work-level link | Must-have |
| R3 | The constructed URL navigates to the correct citation in a browser | Must-have |
| R4 | Re-running the pipeline (parse → build) populates the new data | Must-have |

---

## CCEL URL Structure (Confirmed)

### Chapter page URL
```
https://ccel.org/ccel/{authorID}/{bookID}/{bookID}.{div_id}.html
```
- `bookID` = last segment of stored `ccel_url` (e.g. `expositoreznehes` from `.../adeney/expositoreznehes`)
- `div_id` = the ThML `div1`/`div2` id containing the citation (e.g. `ii`, `iii.iv`)

### Citation anchor
```
#fnf_{scripRef_id}
```
- `scripRef_id` = the `id` attribute of the `<scripRef>` element in ThML (e.g. `ii-p6.1`)
- Scrolls to the inline footnote marker `[n]` in the running text — exactly where the citation appears
- `div_id` is derivable from `scripRef_id` by taking the part before `-p` (e.g. `ii-p6.1` → `ii`)

### Full example
```
https://ccel.org/ccel/adeney/expositoreznehes/expositoreznehes.ii.html#fnf_ii-p6.6
```
✅ **Verified** — page loads, scrolls to the footnote marker in running text.

### Known edge case
One work (`adeney/expositoreznehes`) has a `v2.` URL variant in its catalog page. The standard `{bookID}.{div_id}.html` pattern also resolves for it (JS-rendered). All ~1,200 other works use only the standard pattern.

---

## Shape A: Store scripRef id, construct URL in viewer

### Parts

| Part | Mechanism |
|------|-----------|
| **A1** | **Capture `scripRef id` in ThML parser** |
| A1.1 | In `parse_thml.py` `_TextBuilder`, when appending `(scripRef_element, offset)` to the citation list, also capture `scripRef_element.attrib.get('id', '')` |
| A1.2 | Pass the captured id through `_process_refs()` into the DB insert call |
| **A2** | **Store in DB** |
| A2.1 | Add `ccel_anchor TEXT` (nullable) to `verse_refs` schema in `db.py` |
| A2.2 | Update `insert_refs()` to accept and write `ccel_anchor` |
| A2.3 | Re-run `python src/parser.py` to repopulate (idempotent — deletes old refs first) |
| **A3** | **Propagate through Go builder** |
| A3.1 | Add `Ca *string \`json:"ca,omitempty"\`` to `bookRef` and `workRef` structs in `build.go` |
| A3.2 | Add `ccel_anchor` to the `verse_refs` SELECT query |
| A3.3 | Populate `Ca` from DB value (nil when NULL) |
| **A4** | **Construct URL in viewer** |
| A4.1 | Extract `bookID` from `ccel_url` by splitting on `/` and taking the last segment |
| A4.2 | Extract `divID` from `ca` by splitting on `-p` and taking the first part (e.g. `ii-p6.1` → `ii`) |
| A4.3 | When `ca` is present: link to `{ccel_url}/{bookID}.{divID}.html#fnf_{ca}` |
| A4.4 | When `ca` is absent (TXT source): link to `ccel_url` as today |

### Fit Check

| Req | Requirement | Status | A |
|-----|-------------|--------|---|
| R0 | Each passage links to its exact citation location in CCEL | Core goal | ✅ |
| R1 | ThML manuscripts get citation-specific links | Must-have | ✅ |
| R2 | TXT manuscripts fall back to work-level link | Must-have | ✅ |
| R3 | Constructed URL navigates to correct citation | Must-have | ✅ |
| R4 | Re-running pipeline populates new data | Must-have | ✅ |

---

## Files to Modify

| File | Change |
|------|--------|
| `src/parse_thml.py` | `_TextBuilder`: capture `scripRef.attrib.get('id', '')` alongside offset. Pass through to `insert_refs()`. |
| `src/db.py` | Add `ccel_anchor TEXT` column to `verse_refs`; update `insert_refs()` signature |
| `cmd/builder/build.go` | Add `Ca *string` to `bookRef`/`workRef`; add `ccel_anchor` to SELECT; populate `Ca` |
| `viewer/app.js` | Construct `{ccel_url}/{bookID}.{divID}.html#fnf_{ca}` when `ca` is present on a ref |

---

## Verification

1. `python src/parser.py` on 1-2 ThML manuscripts (e.g. adeney, abelard); query DB to verify `ccel_anchor` values like `ii-p6.1` are populated
2. `go run ./cmd/builder --book romans` — inspect output JSON for `ca` field on refs (should be a string like `ii-p6.1`)
3. Open viewer locally (`python -m http.server 8000 --directory .`), navigate to a ThML-sourced passage, click "View on CCEL", verify it loads the correct chapter and scrolls to the citation
4. Verify a TXT-sourced work still shows a plain `ccel_url` link
5. Spot-check the `adeney` edge case — the standard URL pattern should still work
