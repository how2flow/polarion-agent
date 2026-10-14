# Polarion Review Localizer (doc-localize)

Fetch the reviews left on a Siemens Polarion document and produce a **Korean**
local copy for reading. **READ-ONLY — never write to Polarion.** Uses
`mcp__polarion__*` read tools + the bundled `fetch_checklist.py`.

The reviews live in the document's REVIEW work item `checklist` field (the filled
filled **result** and **comment** columns) and in its `Ins_defect` work items
(descriptions). Reviewer-authored text is often **English** (the review workflow
authors it in English); this skill localizes it to **Korean**. Text that is
**already Korean is kept verbatim — do NOT re-translate or paraphrase it.**

## Input
A Polarion document URL, or `project / space / document`. If none is given, ask
for the URL.

## Step 1 — Resolve + find the review work items
- Parse the URL → `project_id` / `space_id` / `document_name`. (Do not pass
  `--doctype`: the script reads the document's own type.)
- `read_document_parts` (all pages) → note the REVIEW-type work item id(s) and any
  `Ins_defect` work item ids (same discovery as doc-review).

## Step 2 — Fetch the reviews (reuse the bundled script)
```
python3 skills/doc-review/fetch_checklist.py \
  --project <p> --space <s> --document <d> --no-template-check \
  --review-wi <id from Step 1> --defect-wi <Ins_defect ids from Step 1> \
  --out <skills/doc-localize/out/.fetch-<YYYYMMDD-HHMMSS>.json>
```
(`--no-template-check`: this skill never uses the template comparison, and
skipping it avoids three extra Polarion reads per run.)

The JSON has the checklist `rows` plus **`columns.roles`**, which maps each
reviewer column to its index: `result`, `comment`, `applicable`,
`defect_status`, and `template` (the
unfilled columns). **Read every cell through that map — never by a fixed column
number.** The layout is doctype-specific: 7 columns for requirement/design docs,
6 for functional test specs (no category column, so every role shifts one left). If
`columns.roles` is `null` the header was not understood — say so and stop rather
than guessing. `inspection_defects` (id / title / status / description) is also
in the JSON.

## Step 3 — Localize to Korean
For each reviewed item (rows whose `result` or `comment` cell is non-empty — look
them up via `columns.roles`) and each inspection defect:
- Keep the template columns (category / item / criteria — the indices in
  `columns.roles.template`, already Korean) **verbatim**.
- Reviewer text — the **`comment`** cell and the defect **description**:
  - **already Korean → keep as-is (no translation).**
  - English / other language → translate to natural, faithful Korean (no added
    meaning, no omission).
- The **`result`** cell (🟩Pass / 🟥Fail / N/A) → keep the token as-is, emoji included.
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
... (판정/의견이 채워진 항목만; 분류 열이 없는 레이아웃이면 그 열은 빼고 쓴다)

## Inspection Defects (결함)
- <id> (<status>): <description 한글>
```

## Guardrails
- **Read-only**; never call a write tool; never modify Polarion.
- **License 401** "limit of 15 concurrent users" = transient seat limit — report
  and retry later; no tight loop.
- **Do not translate already-Korean text**; do not invent — localize only what is
  actually there.
