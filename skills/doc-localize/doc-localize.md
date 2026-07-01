# Polarion Review Localizer (doc-localize)

Fetch the reviews left on a Siemens Polarion document and produce a **Korean**
local copy for reading. **READ-ONLY — never write to Polarion.** Uses
`mcp__polarion__*` read tools + the bundled `fetch_checklist.py`.

The reviews live in the document's REVIEW work item `checklist` field (the filled
**판정** and **리뷰어 의견** columns) and in its `Ins_defect` work items
(descriptions). Reviewer-authored text is often **English** (the review workflow
authors it in English); this skill localizes it to **Korean**. Text that is
**already Korean is kept verbatim — do NOT re-translate or paraphrase it.**

## Input
A Polarion document URL, or `project / space / document`. If none is given, ask
for the URL.

## Step 1 — Resolve + find the review work items
- Parse the URL → `project_id` / `space_id` / `document_name`; `get_document` → `type`.
- `read_document_parts` (all pages) → note the REVIEW-type work item id(s) and any
  `Ins_defect` work item ids (same discovery as doc-review).

## Step 2 — Fetch the reviews (reuse the bundled script)
```
python3 <skills/doc-review>/fetch_checklist.py \
  --project <p> --space <s> --document <d> --doctype <type> \
  --review-wi <id from Step 1> --defect-wi <Ins_defect ids from Step 1> \
  --out <skills/doc-localize/out/.fetch-<YYYYMMDD-HHMMSS>.json>
```
The JSON has the checklist `rows` — each row's cells include the filled **판정**
(col 4) and **리뷰어 의견** (col 5) when present — and `inspection_defects`
(id / title / status / description).

## Step 3 — Localize to Korean
For each reviewed item (rows with a non-empty 판정 or 의견) and each inspection
defect:
- Keep **분류 / 리뷰 항목 / 판정 기준** (already Korean) **verbatim**.
- Reviewer text — **리뷰어 의견** (col 5) and defect **description**:
  - **already Korean → keep as-is (no translation).**
  - English / other language → translate to natural, faithful Korean (no added
    meaning, no omission).
- **판정** (Pass / Fail / N/A) → keep the token as-is.
- If there are no filled reviews and no defects → the report says "리뷰 내용 없음".

## Step 4 — Save locally (read-only)
Write a Korean report. The **filename must include the document title** — default
`skills/doc-localize/out/review-ko-<SAFE_TITLE>-<YYYYMMDD-HHMMSS>.md`, where
`SAFE_TITLE` is the document `title` (from `get_document`) with whitespace and `/`
replaced by `_` and other filesystem-unsafe characters removed (readable, ≤ 60
chars). If the caller passes an explicit output directory + timestamp, save into
that directory using this `review-ko-<SAFE_TITLE>-<ts>.md` name.
```
# 리뷰 (한글) — <문서 제목>
- 프로젝트/스페이스/타입/상태, source REVIEW work item

## 체크리스트 리뷰
| 분류 | 리뷰 항목 | 판정 | 의견(한글) |
... (판정/의견이 채워진 항목만)

## Inspection Defects (결함)
- <id> (<status>): <description 한글>
```

## Guardrails
- **Read-only**; never call a write tool; never modify Polarion.
- **License 401** "limit of 15 concurrent users" = transient seat limit — report
  and retry later; no tight loop.
- **Do not translate already-Korean text**; do not invent — localize only what is
  actually there.
