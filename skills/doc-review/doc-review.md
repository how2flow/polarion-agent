# Polarion Document Reviewer (doc-review)

Read a Siemens Polarion document and review it **against the governed review
checklist** for that document — never against criteria you invent (except the
explicit fallback below). Uses `mcp__polarion__*` to read and the bundled
`fetch_checklist.py` to obtain the checklist.

**READ-ONLY.** Never call a Polarion write tool (`create_*`, `update_*`,
`move_*`). Do not fill the `verdict` field — that is `doc-verify`'s job.

**AUTONOMOUS COMPLETION (this skill usually runs headless — no human to answer
mid-run).** Never stop to ask a question. Resolve every ambiguity with a
documented default and **continue through to the findings JSON**. Producing the
content snapshot / checklist but **not** the findings JSON is a failure. If you
must make a choice, make it, note it in the output, and proceed.

**Multiple REVIEW work items is normal** (peer review has 2+ reviewers). Pass
**all** of their ids to `fetch_checklist.py` (comma-separated `--review-wi`); it
**deterministically** selects the one the **current user owns**
(reviewer/assignee) as `source_wi`, so the write-back target matches the
ownership gate. Do not pause to ask which.

## Input (target)
- a Polarion document URL (`%20` → space when decoding the name),
- `project_id / space_id / document_name`, or
- a pre-read content file (path) + a checklist JSON (path) — then skip reading.

## Step 1 — Read the document (reading comes first)
1. `get_document(project_id, space_id, document_name)` → title, **type** (the
   doctype), status, custom fields.
2. `read_document_parts(...)` → all pages (paginate until `has_more` false).
   Render in order by part `type` (heading/normal/wikiblock/toc/workitem). For
   requirement-module docs the content lives here (`read_document` is often
   empty — normal). The `workitem` parts reveal embedded work items and their
   `work_item_type`; note any whose type is the review type (default `REVIEW`)
   and its `work_item_id`. **Also note any existing inspection-defect items**
   (default type `Ins_defect`) and their `work_item_id`s — there may be none.
Write the rendered content to the caller's content-snapshot path (so verify/the
pipeline reuse it without re-reading Polarion).

## Step 2 — Fetch the checklist (always, before reviewing)
Run the bundled script (it pulls raw data and emits a status; it does **not**
decide criteria):
```
python3 skills/doc-review/fetch_checklist.py \
  --project <p> --space <s> --document <d> --doctype <type> \
  [--review-wi <ALL review WI ids from Step 1, comma-separated>] \
  [--defect-wi <comma-separated Ins_defect ids you saw in Step 1>] \
  --out .review-checklist-<doctype>-<YYYYMMDD-HHMMSS>.json
```
(Use `Bash date` for the timestamp. Pass `--review-wi` / `--defect-wi` when
Step 1 already found those work items — more robust than the discovery query.)
The output JSON also carries **`inspection_defects`** — the existing
inspection-defect work items in the document (id / title / status / severity /
description), an empty list when there are none.

## Step 3 — Confirm & choose the checklist (driven by `status` in the JSON)
- **`status = "checklist-present"`** → use the checklist **as-is; do not edit or
  "improve" it.** The first row is the column header (분류 / 리뷰 항목 / 판정
  기준 / 리뷰 대상 / 판정 결과 / 의견 / 결함 상태) — use it to map columns.
  **Confirm**: your item list count must match `item_count`; if it doesn't,
  stop and report a mismatch (do not silently drop items).
- **otherwise (`review-wi-no-checklist` / `no-review-wi`)** → there is no
  governed checklist; fall back **in this order**:
  1. **Default template** — look for `skills/doc-review/schema/<doctype>.json`
     (doctype = the document `type`, e.g. `ModuleRequirementsSpecification`,
     `ModuleArchitecturalDesignSpecification`, `ModuleDetailedDesignSpecification`).
     If it exists, use its `items` as the checklist (`checklist_source =
     default-template`). **Do not edit it.**
  2. **Made-by-agent** — only if there is **no template for this doctype**,
     build an ASPICE-conformant checklist yourself, write it to
     `.review-checklist-<doctype>-made-by-agent-<ts>.json`, and use that
     (`checklist_source = made-by-agent`).
  Never improvise when a governed checklist **or a default template** exists.

## Step 4 — Review
For every checklist item, judge the document and fill:
`리뷰 대상 (O/X)` (applicable?), `판정 (Pass/Fail)`, `의견` (comment), and
`evidence` (an exact quote / concrete observation). Keep **all** items including
conditional ones ("(기능안전 적용 시)…", "(사이버보안 적용 시)…"); decide
applicability explicitly with a rationale. Ground every Fail in evidence; never
invent.

**Human-reviewed rows are off-limits.** If a data row's 결함상태 (defect-status,
the last column: open/closed) is **already filled**, a human already reviewed
that row — do **not** re-review or propose overwriting it (leave its 판정/의견 as
found). This is enforced deterministically by `write_checklist.py` regardless, but
respect it here too. An empty 결함상태 means the row is yours to review.

**Language rule — write the review in English.** All text **you author**
(`comment`, `evidence`, `applicable_reason`, and any note) must be in **English**,
even though the document and checklist are Korean. Keep the checklist's
`category` / `item` / `criteria` **verbatim** in their original language (do not
translate the source checklist); quoted evidence may stay in the original
language but your explanation around it is English.

## Step 5 — Inspection defects
Use the `inspection_defects` list from the fetched JSON (existing defects; may be
empty):
- **Cross-reference, don't duplicate** — if an existing inspection defect already
  covers a checklist item (match by item text / topic), reference it in that
  finding (`existing_defect`: its id + status) instead of raising a new one.
- **Write defect content only when a real defect exists** — for each **confirmed
  Fail** that genuinely warrants an inspection defect, populate the finding's
  `inspection_defect` object with just **`title`** (concise, English — typically
  the failed checklist item) and **`description`** (English: what is wrong, where
  — §/work-item ref, and why it fails the `criteria`). **Only these two.** Do NOT
  set severity/assignee/due/safety-relevance — those standard fields are left to
  the work-item type's Polarion defaults and the human. Never invent a defect for
  a Pass/N-A item.
- **Decide the write action** (you have both our content and the existing
  defect's text, so YOU judge duplication — a deterministic string compare can't,
  since ours is English and the existing may be Korean). Set
  `inspection_defect.write_action` and `target_defect_wi`:
  - existing defect for this item whose description is **blank** → `fill`
    (`target_defect_wi` = that defect id)
  - existing defect **with content that does NOT already cover ours** → `append`
  - existing defect whose content **already conveys the same point** → `skip`
  - **no existing defect** for this warranted Fail → `needs-new`
    (`write_defects.py` creates a new `Ins_defect` work item with **only
    title + description** and moves it into the document's §7.4 defect section;
    idempotent — a defect whose title already exists is not recreated)
- The actual write/create is done by `write_defects.py` (deterministic); you only
  decide the action + content (title + description).

## Output
- the **content snapshot** (from Step 1), and
- **findings JSON** = the filled checklist (one entry per item; no `verdict`).
- State which checklist path was used (`checklist_source`: **governed** /
  **default-template** / **made-by-agent**) — in the output and, downstream,
  the report header.

## Guardrails
- Read-only. **License 401** mentioning "limit of 15 concurrent users" =
  transient seat limit, not an error — report and retry later; no tight loop.
- Strong bias to the **governed checklist**; only improvise when `status`
  clearly says none exists. Never silently change a present checklist.
- Precision over volume; a Fail without evidence is not allowed.
