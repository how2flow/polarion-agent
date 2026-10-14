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
| Requirements / Architectural / Detailed Design | 7 | category ∥ item ∥ criteria ∥ applicable ∥ result ∥ comment ∥ defect-status |
| Functional Test Specification | 6 | item ∥ criteria ∥ applicable ∥ result ∥ comment ∥ defect-status |

The 6-column layout has **no category column**, shifting every role one to the
left — so defect-status is col 5 there and col 6 in the 7-column layout. Roles
are matched by a header keyword per role (configured in `profile.column_roles`;
`result` additionally forbids the criteria keyword, which shares a word with it);
each must resolve to exactly one column or the layout is rejected.

## Read methodology
For requirement-module LiveDocs, `read_document` is often empty; the content is
in `read_document_parts`. Treat **parts** as the primary content source. The
`workitem` parts also reveal the embedded review work item (type = review-type).

## Fetch decision tree (`fetch_checklist.py`, deterministic)
Emits a `status` flag so the AI branch is data-driven, not guesswork:
1. no review-type work item for the document → `status = "no-review-wi"`
2. review work item but empty/absent checklist field → `status = "review-wi-no-checklist"`
3. checklist present → `status = "checklist-present"` (+ `rows` = every row,
   `item_count` = all of them including headers and preamble,
   `data_row_count` = **the items**, `template_rows` = those rows verbatim,
   `header_row_count` / `preamble_row_count`, and `columns` = the first header
   row + resolved role→index map). **`template_rows` is the item list** --
   `rows` is the whole table and a checklist may be several tables.

## Checklist selection (AI) — three tiers, in order
1. **governed** (`status = checklist-present`) → use **as-is, unedited**;
   confirm the item count matches `data_row_count` (stop on mismatch — no silent
   drops). Strongly preferred.
2. **default-template** (otherwise, if the doctype has an official template) →
   use the fetched template's items as the checklist, unedited.
3. **made-by-agent** → only when `template_check.fallback` is
   `"made-by-agent"`: no governed template is available at all (none configured
   for the doctype, the document has no type, no profile for the project, or the
   check disabled). Build an ASPICE-conformant checklist, save to a
   `*-made-by-agent-*.json` file, review against that.

`template_check.fallback` is the single authority for this choice —
`"none"` (the document has its own checklist: tier 1), `"template"` (tier 2),
`"made-by-agent"` (tier 3), `"stop"` (the governed criteria could not be
established — unreachable template, unreadable document metadata, or a
half-configured profile: report it and stop, never improvise). Never infer the tier from error
text. The tier numbers here are the ones `doc-review.md` names in words; the
two files are concatenated into one prompt, so they must not disagree.

## Official templates (fetched at run time)
The organization's template documents live in `Guide / Template_Module`, mapped
per doctype in `config/polarion.json` (`profile.templates`). The doctype is read
from the document itself (not from a CLI flag a caller could forget), so the
check runs on **every** fetch. `fetch_checklist.py` then pulls the template's
REVIEW checklist and:

1. **compares** it to the document's own checklist, emitting `template_check`
   (`current` / `outdated` / `diverged`) with `missing_items`, `extra_items`,
   `changed_criteria` and `layout_diff` — rows matched by their item text, so a
   reorder is not reported as a change. **Advisory only**: review and write-back
   always use the document's own checklist.
2. serves as the **tier-2 fallback** checklist when a document has none.

The fetch result is cached under `.cache/templates/<profile>/<doctype>.json`
(gitignored) and used **only** when the live fetch fails, so the baseline can
never silently go stale. Nothing template-related is committed.

Document **ids** (what `profile.templates.doctypes` must contain — not titles;
Template4's id is a romanized string that does not match its title):

| doctype | template document id | title | items | cols |
|---|---|---|---|---|
| `ModuleRequirementsSpecification` | `Template1_Module Requirements Specification` | (same) | 35 | 7 |
| `ModuleArchitecturalDesignSpecification` | `Template2_Module Architectural Design` | (same) | 38 | 7 |
| `ModuleDetailedDesignSpecification` | `Template3_Module Detailed Design2` | Template3_Module Detailed Design | 13 | 7 |
| `Module_Function_Test_Specification` | `Template4_modyul gineungteseuteu myeongseseo` | Template4_Module Functional Test Specification | 16 | 6 |

## Syncing a stale checklist (`fetch_checklist.py --sync-template`)
A checklist that is behind the official template is **overwritten with the
template's own table before the review runs**, so the review is always made
against current criteria. The template work item's `checklist` field is copied
back verbatim -- the same thing a reviewer does by hand when the format changes.

Verdicts already in the checklist are **not carried over**. A changed template
means changed criteria, so a verdict recorded against a superseded one is stale
by definition, not something to preserve; the review that follows fills the
whole table again.

The human-filled defect-status column (Open/Closed) goes with them -- it tracks
defects raised against the criteria that were just replaced. Note that nothing
downstream refills it: the review writes verdicts, not defect statuses, so it
stays blank until a human fills it again, the same way it was filled in the
first place. `template_sync.discarded_defect_status` counts what the run
dropped, and the replaced table is kept verbatim in the fetch output as
`replaced_checklist_html` (preserved in `out/`), since the PATCH itself cannot
be undone from here. Each reviewer has their own REVIEW work item, so this only
ever rewrites the current user's copy.

| `template_check.status` | action |
|---|---|
| `outdated` / `diverged` | overwrite with the template |
| `no-document-checklist` | write the template in -- unless the field holds an image or a table skeleton, which `detag()` also reports as empty. The husk a rich-text editor leaves when a reviewer clears the field (`<p>&nbsp;</p>`, `<p></p>`, `<div><br/></div>`) **is** empty and is written into |
| `unknown` | overwrite -- a checklist the comparison could not read is the clearest mismatch there is, not something to protect. Three real documents are 6 columns with no defect-status column: their roles never resolve, so they are exactly the ones that most need the template |
| `current`, `unavailable` | leave alone |

Every gate is on the table being **written**, not the one being replaced: a
malformed live checklist is a reason to replace it, not to keep it.

Refused unless the template came from a **live** fetch (never from the cache or
a failed one), the target **is** the review type, and the PAT user is an
assigned reviewer/assignee -- the same gates as the other writers, plus
`--dry-run`. The type gate is not redundant: `--review-wi` bypasses the
type-filtered discovery query and is filled in by the Stage-1 agent, so until
it is checked the target is an AI choice.

A destructive overwrite needs four more that a read-only comparison did not:

| refused when | why |
|---|---|
| the template document holds more than one review work item | `sorted()[0]` is a stable baseline to diff against; it is not a safe choice of bytes to write, and another could be a reviewer's filled copy |
| the template does not parse as a checklist (no column roles, or no data rows) | nothing parses it on the `no-document-checklist` branch, so prose would be written in, reported as a governed 0-item checklist, and Stage 4 would die on the missing header row |
| the template's own table is unusable to the **writer** | the structural check has to cover the table being written, not only the one being destroyed, and the writer reads the same bytes by a different rule: every `<tr>` without a `<th>` is a data row, and column roles come from the *first* `<th>` row. Four ways it parses cleanly and still bricks Stage 4 — see below |

| template defect | what Stage 4 does, after the live checklist is already gone |
|---|---|
| no `<th>` row (a header of bold `<td>`s) | `_Table` cannot tell it from data, so the parse looks fine and `resolve_layout` exits with `checklist has no <th> header row` |
| `<tr>` count minus header rows ≠ data rows parsed (a `<tr>` in a comment, an unclosed tag, a nested table) | the two sides disagree about how many data rows exist, whatever the cause |
| blank spacer rows | dropped by the row filter, so `data_row_count` is short of the `<tr>`s walked: `more data rows than filled entries` |
| ragged rows (merged cells) | `layout not uniform` — and the same merge fabricated the diff that condemned the live checklist in the first place |

Row counts are measured the writer's way throughout: `<tr>` minus header rows
minus preamble rows must equal the data rows parsed, and "ragged" means a data
row whose width differs from the **header's** — not from row 0, which is the
one-cell note in the four checklists that have one.

### Checklists built from several tables
A checklist may be any number of tables. `write_checklist` already walks every
`<tr>` **without** a `<th>`, so the parse has to use the same rule rather than
"every row but the first": `_Table` records which rows carry a `<th>` and the
data rows are all the rest. `Template3_Module Detailed Design2` is two tables,
and 22 of 40 real detailed-design review items share that shape -- counting by
position reported one item too many, handed the second header to the reviewer
as a criterion, and killed Stage 4 with `row mismatch`.

Column roles come from the **first** `<th>` row, as in the writer, and every
other header row must resolve to the same map -- compared by **resolved role
index, not by header text**, because real copies of one template word the same
column differently (`리뷰 결함 상태` vs `리뷰 결함 처리 상태`). If they genuinely
disagree the layout is left unresolved, and `write_checklist` then **refuses**
rather than resolving from the live header: no single index describes the
table, and writing by one would fill the wrong column in whichever table lost.
(A findings file with no `columns` block at all is a pre-layout-aware run, not
a refusal.)

Rows **before the first header** are preamble, not items -- four real
checklists open with a colspan'd note above the table. The fetch excludes them
from `template_rows`, the writer leaves them untouched, and `validate()`
requires them to come back byte-identical rather than holding them to the
header's column count, which a colspan'd note cannot meet.

`inject`, `lost_update_check` and `validate` share one definition of a data row
(`write_checklist.data_row_indices`). Separate copies of that predicate are
what put the two sides out of step to begin with.
| the document's rows do not all have the header's cell count | `_Table` ignores `rowspan`/`colspan`, so a merged cell is read as a short row and every cell after it shifts one column left -- the comparison then reports a false missing + extra. A checklist that actually matches would be destroyed |

"Empty" is decided by parsing the field, not by scanning it: a cleared field
keeps the husk its editor left (`<p>&nbsp;</p>`, `<div><br/></div>`) and is
written into, while an image, a table skeleton or a Polarion macro `div` is
content `detag()` cannot see and is refused. An allowlist of content tags
cannot be complete, so the test is the other way round -- husk tags carrying
only presentational attributes, and no text.
Every refusal is reported in `template_sync.reason` rather than passing
silently. After a sync the output's `rows` / `template_rows` / `columns` are the
template's, and `template_check` still describes what was found **before** it.

`run.sh --no-sync` reviews the checklist as it stands instead.

## Configuration (`config/polarion.json`)
Every instance-specific name is data, selected by the target document's
`project_id` (`match.project_ids`), falling back to `default_profile`:
`work_item_types` (review / defect), `checklist_field`, `column_roles` (the
header keywords that identify each reviewer column), `template_columns`
(which template column is category / item / criteria), `defect_section_headings`, and
`templates`. Supporting another project — or a checklist in another language —
means adding a profile, not editing code. CLI flags (`--profile`,
`--review-type`, `--defect-type`, `--checklist-field`) still override it.

## Output (findings = filled checklist)
One entry per checklist item:
```json
{
  "document": { "project_id": "", "space_id": "", "document_name": "",
                "title": "", "doctype": "", "status": "" },
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
  "template_sync": { },     // copied VERBATIM when the fetch emitted one; absent otherwise.
                            // synced=true means the checklist was replaced with the
                            // official template BEFORE this review, so template_check
                            // describes the state before that, not an open mismatch.
  "results": [
    {
      "id": "C-001",
      "category": "<category>",   // "" when the layout has no category column (6-col)
      "item": "<item>",
      "criteria": "<criteria>",
      "applicable": "O | X",
      "applicable_reason": "why (esp. for conditional safety/security items)",
      // no ASIL grade stated anywhere -> result is Fail, never N/A (QM counts
      // as stated; nothing stated does not). See the Language/criteria notes.
      "result": "🟩Pass | 🟥Fail | N/A",   // emoji as the header spells them
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
Acting on it (filling an existing `Ins_defect`, or creating one) is a separate
**opt-in** step performed by `write_defects.py` — never by doc-review, and not
part of the default pipeline (`run.sh --defects` enables it).

## File naming (gitignored dotfiles)
- governed checklist: `.review-checklist-<doctype>-<YYYYMMDD-HHMMSS>.json`
- agent-built: `.review-checklist-<doctype>-made-by-agent-<ts>.json`
- Always pass the exact path between steps; never select "latest" by glob.
- Rotate (keep last N) to avoid dotfile accumulation.

## Language rule
**`comment` is Korean.** It is the only prose the review writes into Polarion
(the checklist's 리뷰어 의견 column), and Korean readers read it there. Every
other reviewer-produced text — `evidence`, `applicable_reason`, `verdict.note`,
inspection-defect `title` / `description`, the rendered report — stays
**English** and never leaves the local files.

The source checklist's `category` / `item` / `criteria` are kept **verbatim**,
never translated. Enumerated values are not prose and never translate either.
`result` is **`🟩Pass`** / **`🟥Fail`** — with the emoji, as the column header
`판정 결과 (🟩Pass/🟥Fail)` spells them and as the humans filling the same
column write them — or **`N/A`** for an item that does not apply (the header
names no token for that case). `applicable` is exactly `O` / `X`.

The **defect-status column is never written by the review**, with or without a
verdict: `checklist_columns.KEEP_ROLE` excludes it from `fill_map`, and
`validate()` asserts it comes back byte-identical. A person fills Open/Closed
after reading the review.

The **failed-item count** is not the AI's to report either. The writer counts
the `Fail` rows of the table it just wrote -- the whole table, including rows a
human owned and it skipped -- and sets the work item field named by the
profile's `fail_count_field` in the **same PATCH** as the checklist, so the
count can never describe a table that was not written. A profile that names no
field leaves it alone.

## Write-back (separate, deterministic, opt-in)
doc-review itself is **read-only** — it produces the filled findings JSON. A
separate script, `write_checklist.py`, pushes that back to Polarion: it
re-fetches the REVIEW work item and **surgically injects** the reviewer values
into the original `checklist` HTML, writing **only** applicable (O/X) / result
(Pass/Fail) / comment and leaving the template columns and defect-status
**byte-for-byte unchanged**. Which column is which is resolved from the header row, never
assumed. Safety:
- **type gate** — the target must be a `REVIEW` work item (`--review-type`,
  default `REVIEW`); any other type aborts. This writer touches nothing else.
- **layout gate** — roles re-resolved from the live header and compared to the
  roles recorded at fetch time; an unreadable header or a moved role aborts.
- **ownership gate** — write only if the REVIEW work item's assigned
  reviewer/assignee IS the current PAT user (token `sub` claim); aborts if there
  is no owner or a different owner.
- **lost-update** — live template columns must still match `template_rows`
  (verbatim text captured at fetch time), same row count.
- **human-reviewed guard** — a row whose result **or** defect-status is already filled is
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
- doc-review is read-only; write-back is the separate step above.
- **By default the pipeline writes exactly one work item: the `REVIEW` one**
  (its `checklist` field). Inspection defects are a different work item type
  (`Ins_defect`) and creating one adds an item to the document, so
  `write_defects.py` runs only with `run.sh --defects`. It enforces its own type
  gates: `source_wi` must be `REVIEW`, and every write/create target must be
  `--defect-type` (default `Ins_defect`).
- Traceability: out of scope for this version.
