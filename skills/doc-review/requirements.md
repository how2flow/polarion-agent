# doc-review — Requirements

## Purpose
Read a Polarion document and review it against the **governed review checklist**
for that document (fetched from Polarion), producing a filled-in checklist
(findings). The criteria are **not** authored here — they come from Polarion.
`doc-verify` re-checks the result; the `reviewer` project glues the two.

## Philosophy: generic tool, vendor-specific data
- **Reading + raw fetch = deterministic & vendor-neutral.** `fetch_checklist.py`
  pulls raw work-item data and does only generic extraction (HTML table → rows);
  it never decides what the criteria mean.
- **Judging the checklist = AI (this skill).** Confirming applicability and
  judging the document are the AI's job.
- **Column roles = deterministic, from the header row.** Resolved by
  `scripts/checklist_columns.py` at fetch time and again at write time. Column
  positions are never hardcoded and never inferred by the AI, because a write to
  the wrong column is silent corruption. An unresolvable header aborts the write.
- Vendor-specific names are **config**, not code: `--review-type` (default
  `REVIEW`), `--checklist-field` (default `checklist`).

## Checklist layout (varies by doctype)
The number and order of columns is **not** fixed. Observed in `bsp_module`:

| doctype | cols | layout |
|---|---|---|
| Requirements / Architectural / Detailed Design | 7 | 분류 ∥ 리뷰 항목 ∥ 판정 기준 ∥ 리뷰 대상 ∥ 판정 결과 ∥ 의견 ∥ 결함 상태 |
| Functional Test Specification | 6 | 리뷰 항목 ∥ 판정 기준 ∥ 리뷰 대상 ∥ 판정 결과 ∥ 의견 ∥ 결함 상태 |

The 6-column layout has **no 분류 column**, shifting every role one to the left —
so `결함상태` is col 5 there and col 6 in the 7-column layout. Roles are matched
by header keyword (`대상`, `판정` without `기준`, `의견`, `결함`); each must
resolve to exactly one column or the layout is rejected.

## Read methodology
For requirement-module LiveDocs, `read_document` is often empty; the content is
in `read_document_parts`. Treat **parts** as the primary content source. The
`workitem` parts also reveal the embedded review work item (type = review-type).

## Fetch decision tree (`fetch_checklist.py`, deterministic)
Emits a `status` flag so the AI branch is data-driven, not guesswork:
1. no review-type work item for the document → `status = "no-review-wi"`
2. review work item but empty/absent checklist field → `status = "review-wi-no-checklist"`
3. checklist present → `status = "checklist-present"` (+ `rows`, `item_count`
   = rows incl. header, `data_row_count` = actual items, `template_rows`,
   and `columns` = the header row + resolved role→index map)

## Checklist selection (AI) — three tiers, in order
1. **governed** (`status = checklist-present`) → use **as-is, unedited**;
   confirm the item count matches `data_row_count` (stop on mismatch — no silent
   drops). Strongly preferred.
2. **default-template** (otherwise, if a template exists) → use
   `skills/doc-review/schema/<doctype>.json` as the checklist, unedited.
3. **made-by-agent** (only if no governed checklist AND no template for the
   doctype) → build an ASPICE-conformant checklist, save to a
   `*-made-by-agent-*.json` file, review against that.
Never improvise (tier 3) when tier 1 or 2 is available.

## Default templates (`schema/`)
`skills/doc-review/schema/<doctype>.json` holds a per-document-type default
checklist, keyed by the Polarion document `type` id. Shipped templates:
`ModuleRequirementsSpecification`, `ModuleArchitecturalDesignSpecification`,
`ModuleDetailedDesignSpecification` — each extracted from a reference document's
governed REVIEW-work-item checklist (the `checklist` field). Shape:
`{ doctype, source, columns, item_count, items:[{id, category, item, criteria}] }`.
Regenerate when the org's checklist changes; these are the fallback, the live
governed checklist (tier 1) always wins.

## Output (findings = filled checklist)
One entry per checklist item:
```json
{
  "document": { "project_id": "", "space_id": "", "document_name": "",
                "title": "", "type": "", "status": "" },
  "checklist_source": "governed | default-template | made-by-agent",
  "source_wi": "<review work-item id> or null",
  "columns": {        // copied VERBATIM from the fetched JSON (never retyped)
    "header": ["<header cells>"],
    "roles": { "applicable": 3, "result": 4, "comment": 5, "defect_status": 6,
               "template": [0,1,2], "ncols": 7 }   // null if header unrecognized
  },
  "template_rows": [  // copied VERBATIM: the data rows as fetched (lost-update basis)
    ["<cell>", "..."]
  ],
  "inspection_defects": [   // existing Ins_defect work items in the doc (from fetch); [] when none
    { "id": "<inspection-defect id>", "title": "", "status": "", "severity": "", "outline": "", "description": "" }
  ],
  "results": [
    {
      "id": "C-001",
      "category": "<분류>",   // "" when the layout has no 분류 column (6-col)
      "item": "<리뷰 항목>",
      "criteria": "<리뷰 방법 및 판정 기준>",
      "applicable": "O | X",
      "applicable_reason": "why (esp. for conditional safety/security items)",
      "result": "Pass | Fail | N/A",
      "evidence": "exact quote / concrete observation (required for Fail)",
      "comment": "reviewer note",
      "existing_defect": { "id": "<inspection-defect id>", "status": "" },  // present if an existing Ins_defect already covers this item
      "inspection_defect": {   // present ONLY for a confirmed Fail that warrants a defect
        "title": "", "description": "English: what/where/why it fails the criteria",
        // only title + description are written/created; severity/assignee/due/safety-relevance
        // are left to the Ins_defect type's Polarion defaults / the human.
        "write_action": "fill | append | skip | needs-new",  // reviewer's decision (dup judged by AI)
        "target_defect_wi": "<inspection-defect id> or null" // existing Ins_defect to write into (null for needs-new)
      },
      "verdict": { }   // left empty by doc-review; filled by doc-verify
    }
  ]
}
```
`inspection_defects` (top level) = existing defects fetched from Polarion (read,
for cross-reference). `inspection_defect` (per result) = **new** defect content
the reviewer produced for a genuine, not-yet-covered Fail — absent otherwise.
Creating the actual `Ins_defect` work item from this content is a separate
opt-in write step (not done by doc-review).

## File naming (gitignored dotfiles)
- governed checklist: `.review-checklist-<doctype>-<YYYYMMDD-HHMMSS>.json`
- agent-built: `.review-checklist-<doctype>-made-by-agent-<ts>.json`
- Always pass the exact path between steps; never select "latest" by glob.
- Rotate (keep last N) to avoid dotfile accumulation.

## Language rule
The review is **authored in English**. All reviewer-produced text (`comment`,
`evidence` explanation, `applicable_reason`, verdict notes, the rendered report)
is English. The source checklist's `category` / `item` / `criteria` are kept
**verbatim** in their original language (Korean) — never translated.

## Write-back (separate, deterministic, opt-in)
doc-review itself is **read-only** — it produces the filled findings JSON. A
separate script, `write_checklist.py`, pushes that back to Polarion: it
re-fetches the REVIEW work item and **surgically injects** the reviewer values
into the original `checklist` HTML, writing **only** 리뷰대상(O/X) / 판정(Pass/Fail)
/ 리뷰어의견 and leaving the template columns and 결함상태 **byte-for-byte
unchanged**. Which column is which is resolved from the header row, never
assumed. Safety:
- **layout gate** — roles re-resolved from the live header and compared to the
  roles recorded at fetch time; an unreadable header or a moved role aborts.
- **ownership gate** — write only if the REVIEW work item's assigned
  reviewer/assignee IS the current PAT user (token `sub` claim); aborts if there
  is no owner or a different owner.
- **lost-update** — live template columns must still match `template_rows`
  (verbatim text captured at fetch time), same row count.
- **human-reviewed guard** — a row whose 판정 **or** 결함상태 is already filled is
  left entirely untouched; to re-review it a human must clear those cells.
- **structural validation** — row/col count unchanged and every template +
  defect-status cell byte-equal after injection.
- **`--dry-run`**.

Triggered via the reviewer project's `run.sh … --write [--dry-run]`.

Only a **governed** checklist (tier 1) can be written back — tiers 2/3 have no
live table to inject into, so those reviews are report-only.

For write-back to map values back to the checklist rows, **`results[]` must
contain exactly one entry per checklist item, in the checklist's row order**
(same count as `data_row_count`) — including rows a human already reviewed, which
the writer skips but still counts. Each entry also carries
`document.project_id` / `source_wi` at top level so the writer knows the target
work item.

## Scope
- doc-review is read-only; write-back is the separate opt-in step above.
- Traceability/defect-creation: out of scope for this version.
