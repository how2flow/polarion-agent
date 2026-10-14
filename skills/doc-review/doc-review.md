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
  --project <p> --space <s> --document <d> \
  [--review-wi <ALL review WI ids from Step 1, comma-separated>] \
  [--defect-wi <comma-separated Ins_defect ids you saw in Step 1>] \
  --out .review-checklist-<doctype>-<YYYYMMDD-HHMMSS>.json
```
(Use `Bash date` for the timestamp. Pass `--review-wi` / `--defect-wi` when
Step 1 already found those work items — more robust than the discovery query.
Do **not** pass `--doctype`: the script reads the document's own type, which is
what keys the template-currency check. Passing a different one is reported and
ignored.)
The output JSON also carries **`inspection_defects`** — the existing
inspection-defect work items in the document (id / title / status / severity /
description), an empty list when there are none.

## Step 3 — Confirm & choose the checklist (driven by `status` in the JSON)
- **`status = "checklist-present"`** → use the checklist **as-is; do not edit or
  "improve" it.** **`template_rows` is the item list — one entry per row, and
  exactly `data_row_count` of them.** Use it, not `rows`: `rows` is the whole
  table, and a checklist may be several tables (so several header rows) and may
  open with a note row above the first header. Neither is an item, and both are
  already excluded from `template_rows`. `columns.header` is the header row.
  **The layout is doctype-specific — never assume column positions.** The fetch
  script already resolved them for you in `columns.roles`
  (`applicable` / `result` / `comment` / `defect_status` → column index, plus
  `template`). Two layouts seen so far:
  - 7 columns: category / item / criteria / applicable / result / comment /
    defect-status
  - 6 columns (e.g. functional test specs): **no category column** — leave each
    finding's `category` empty rather than inventing one.
  If `columns.roles` is `null` (header not understood), say so in your output
  and continue review-only — write-back will refuse this checklist.
  **Confirm**: your item list count must match `data_row_count`; if it doesn't,
  stop and report a mismatch (do not silently drop items).
  **Copy `columns` and `template_rows` from the fetched JSON into your findings
  JSON verbatim** — the writer uses them to verify the layout and to detect
  edits made since the review. Do not re-type or summarize them.
- **otherwise (`review-wi-no-checklist` / `no-review-wi`)** → there is no
  governed checklist; fall back **in this order**:
  **`template_check.fallback` says which of these applies — read it, do not
  infer from error text.** `"none"` → the document has its own checklist, so
  none of this applies; `"template"` → **default template** below;
  `"made-by-agent"` → **made-by-agent** below; `"stop"` → stop (see below).

  1. **Default template** (`fallback = "template"`) — the fetch output carries
     the official template's items in
     **`template_check.template_checklist`** (`columns` + `rows` +
     `template_rows` + `data_row_count`, same shape as a governed checklist;
     `template_check.template` names its source document).
     Use its **`template_rows`** as the checklist (`checklist_source =
     default-template`) -- same rule as above, `rows` includes the headers.
     **Do not edit them.** A template may have no `category` (the
     functional-test layout has no category column) — leave it empty rather than
     inventing one.
     `template_checklist` is absent when the doctype has no official template,
     **or when the document's project has no profile of its own** —
     `template_check.error` says which (falling back to
     `template_check.template.error` if that key is absent). Say so in your
     output: the reader needs to know the review was measured against no
     governed checklist at all.
  2. **Made-by-agent** (`fallback = "made-by-agent"`) — no governed template is
     available for this document: none is configured for the doctype, the
     document has no type, the project has no profile, or the check was
     disabled. `template_check.error` says which. Build an
     ASPICE-conformant checklist yourself, write it to
     `.review-checklist-<doctype>-made-by-agent-<ts>.json`, and use that
     (`checklist_source = made-by-agent`).

  **Stop instead of improvising when `fallback = "stop"`.** The governed
  criteria could not be established: a template that exists was unreachable,
  the document metadata could not be read, or the template configuration is
  broken. The governed criteria are temporarily out of reach, not
  absent, and a made-up checklist would be indistinguishable from a governed
  review. Report `template_check.error` and stop; a later run will get the
  template. Never improvise when a governed checklist or an official template
  exists.

## Step 4 — Review
For every checklist item, judge the document and fill:
`applicable` (O/X), `result` (🟩Pass/🟥Fail/N/A), `comment`, and
`evidence` (an exact quote / concrete observation). Keep **all** items including
conditional ones — those whose item text is gated on a condition, which on this
 instance reads `(기능안전 적용 시)…` / `(사이버보안 적용 시)…`; decide
applicability explicitly with a rationale. Ground every Fail in evidence; never
invent.

**A missing ASIL grade is a Fail, not an N/A.** Stating the grade is the
baseline: **QM counts as stated, nothing stated does not.** So for a
functional-safety item (`(기능안전 적용 시)…`, and any item whose criteria
turns on an ASIL level), when the document and its linked requirements state no
grade at all, the result is **Fail** — "ASIL 등급이 기재되어 있지 않음" is the
finding. Do not mark it `N/A` on the grounds that no safety context was given;
the absence of that context is itself what fails.
Once a grade **is** stated, judge the item against it as normal — a stated `QM`
may legitimately make a requirement not apply, and then `N/A` with that
rationale is right. This rule is about ASIL only; cybersecurity items keep their
own gate.

**Human-reviewed rows are off-limits.** If a data row already has a **result**
or a **defect-status** value, a human reviewed that row — do **not** re-review
or propose overwriting it (leave its result/comment as found).
This is enforced deterministically by `write_checklist.py` regardless (it skips
such rows entirely), but respect it here too: still emit an entry for the row so
the row count matches, carrying the values as found. A row with both cells empty
is yours to review.

**Language rule.** **`comment` is written in Korean** — it is the one piece of
prose that lands in Polarion, in the checklist's 리뷰어 의견 column, where
Korean readers see it. Everything else you author (`evidence`,
`applicable_reason`, any note, inspection-defect text) stays in **English**;
it never leaves the local files.

Keep the checklist's `category` / `item` / `criteria` **verbatim** (do not
translate the source checklist), and quote evidence in the language it is
written in.

**Verdict tokens are fixed, and the header names them.** Write `result` as
**`🟩Pass`** or **`🟥Fail`**, with the emoji, exactly as the column header
`판정 결과 (🟩Pass/🟥Fail)` spells them — that is what the people who read and
fill this column by hand use. An item that does not apply is **`N/A`** (no
emoji; the header names no token for it). `applicable` stays exactly `O` / `X`.
Never translate any of them.

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

## Step 6 — Template currency
The fetched JSON carries `template_check`: this document's checklist diffed
against the organization's official template. **Always copy the whole
`template_check` block into your findings JSON**, whatever its status — the
report renders several of them.

When `template_sync` is present, read its **`synced`** field — it alone says
whether anything was written:
- **`synced: true`** — the fetch has already overwritten the checklist with the
  template's table. The rows you were handed ARE the template's, and
  `template_check` describes the state *before* the overwrite: report it as
  resolved, not outstanding.
- **`synced: false`** — nothing was written (`reason` says why; `--dry-run` is
  one). The rows you were handed are the document's own, and any mismatch in
  `template_check` is still outstanding. Never describe it as resolved.

**Copy `template_sync` into your findings either way.**

Otherwise call it out when `status` is `outdated` / `diverged` or `unknown`
(the comparison could not run; `reason` says why).
**Never review against the template directly, never add its items to your
results by hand, and never treat a mismatch as a Fail.** Whatever table is in
the fetched JSON is the yardstick.

## Output
- the **content snapshot** (from Step 1), and
- **findings JSON** = the filled checklist (one entry per item; no `verdict`),
  carrying `columns`, `template_rows`, `template_check` and `template_sync`
  (when the fetch emitted one) copied verbatim from the fetched JSON.
- State which checklist path was used (`checklist_source`: **governed** /
  **default-template** / **made-by-agent**) — in the output and, downstream,
  the report header.

## Guardrails
- Read-only. **License 401** mentioning "limit of 15 concurrent users" =
  transient seat limit, not an error — report and retry later; no tight loop.
- Strong bias to the **governed checklist**; only improvise when `status`
  clearly says none exists. Never silently change a present checklist.
- Precision over volume; a Fail without evidence is not allowed.
