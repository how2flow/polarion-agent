# doc-localize — Requirements

## Purpose
Fetch the reviews left on **any** Polarion document (the filled checklist 판정/
리뷰어 의견 + inspection-defect descriptions) and produce a **Korean** local copy
for reading. The reviews are authored in English by the review workflow; this
skill localizes them so a Korean reader can review them offline.

## Scope
- **Read-only.** No writes to Polarion; output is a local Markdown file only.
- Works on **any** document that has reviews — independent of a review run
  (fetch-based, not tied to a local findings JSON).
- **Already-Korean text is kept verbatim** (only non-Korean reviewer text is
  translated). Source checklist text (분류/항목/판정기준) is always kept as-is.

## Inputs
- A Polarion document URL, or `project / space / document` (prompt if missing).

## Behavior
1. Resolve the doc; via `read_document_parts` find the REVIEW work item id(s) and
   any `Ins_defect` ids.
2. Reuse `skills/doc-review/fetch_checklist.py` to fetch the checklist rows
   (filled 판정/의견) + `inspection_defects`.
3. Localize reviewer 의견 + defect descriptions to Korean (skip text already in
   Korean); keep 분류/항목/판정기준 verbatim.
4. Save a Korean report to `skills/doc-localize/out/` (gitignored).

## Tools
Read-only: `get_document`, `read_document_parts`, and `fetch_checklist.py`
(REST). No write tools, no MCP write.

## Relationship to other skills
- `doc-review` writes the (English) reviews; `doc-localize` reads them back and
  localizes to Korean. Opposite direction, both read-only w.r.t. review content
  (doc-localize never writes anywhere in Polarion).
