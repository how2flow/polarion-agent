# reviewer (project)

Orchestrates a full review of a Polarion document from just its URL:

```
doc-review ──> doc-verify ──> report ──> write checklist ─┬─> template comment
 (read+review)  (independent    (Markdown)  (into REVIEW WI) │   (on the REVIEW WI)
                 re-check)                                   └─> write defects
                                                                 opt-in: --defects
```

`./schedule/run.sh '<document-URL>'` runs everything end-to-end and writes the
filled checklist back. **The only work item it writes is the `REVIEW` one** —
inspection defects are a separate work item type and are opt-in (`--defects`).
Every write is guarded by the write scripts' **type gate** (target must be the
REVIEW type), **ownership gate** (your PAT user must be the document's assigned
reviewer) and **lost-update** check, so a wrong-typed, un-owned or drifted target
aborts instead of writing.

- **doc-review** (skill) — reads the document **once** (Polarion; ~1 license
  seat), fetches the governed checklist + existing inspection defects, and emits
  a content snapshot + findings.
- **doc-verify** (skill) — an independent instance re-checks the findings
  (rejects false positives, adjusts, confirms, catches major misses; ASPICE
  lens). No Polarion access.
- **report** — renders the verified findings into a Markdown report.
- **write checklist** (`write_checklist.py`) — surgically injects the
  applicable / result / comment values into the REVIEW work item's `checklist`
  field (template columns + defect-status preserved). Column positions are
  resolved from the checklist's own header row, so both the 7-column (with a
  category) and 6-column (functional test spec) layouts are handled; an
  unrecognized header aborts the write rather than guessing.
- **write defects** (`write_defects.py`) — **opt-in (`--defects`)**; fills /
  appends / creates `Ins_defect` work items per the reviewer's decision. Off by
  default because `Ins_defect` is a different work item type and creating one
  adds an item to the document.

## Why a project

The skills are the single-purpose actors ("read+review", "re-check"); this
project is the conductor that sequences them and owns the schedule. Reading
happens once (in doc-review); the snapshot is passed downstream, so only one
Polarion seat is used and every stage sees the same content.

## Layout

```
projects/reviewer/
└── schedule/
    ├── install.sh   # cron for a fixed target document (--remove to uninstall)
    ├── run.sh       # the pipeline (run manually or via cron)
    └── logs/        # run logs (gitignored)
out/                 # generated reports, review-<timestamp>.md (created on first run)
```

## Run

```bash
# no arguments → prompts for the document URL interactively, then full auto
./projects/reviewer/schedule/run.sh

# full auto from just the URL: review -> verify -> report -> write-back
./projects/reviewer/schedule/run.sh \
  'https://<host>/polarion/#/project/<proj>/wiki/<space>/<document>'

# review + report only, no write-back
./projects/reviewer/schedule/run.sh '<URL>' --no-write

# also write inspection defects (Ins_defect work items) — opt-in
./projects/reviewer/schedule/run.sh '<URL>' --defects

# review the checklist as it stands, even if it is behind the official template
./projects/reviewer/schedule/run.sh '<URL>' --no-sync

# report only: no work item to write to, or you just want the report
./projects/reviewer/schedule/run.sh '<URL>' --no-write

# run the write stages but don't actually PATCH (preview)
./projects/reviewer/schedule/run.sh '<URL>' --dry-run

# schedule a weekly review of a fixed document (Mon 07:00 by default)
./projects/reviewer/schedule/install.sh '<target>' '0 7 * * 1'
./projects/reviewer/schedule/install.sh --remove
```

## Notes

- **Write-back is ON by default, but only to the `REVIEW` work item.** The
  pipeline writes the filled checklist back to that one item, guarded by a type
  gate (target must be `REVIEW`), a layout gate
  (column roles re-resolved from the live header and compared to the review-time
  roles), an ownership gate (your PAT user must be the document's assigned
  reviewer), a lost-update check, and a human-reviewed guard (a row whose result
  or defect-status is already filled is never touched). Use `--no-write` for
  review-only,
  `--dry-run` to preview writes.
- **Seat-efficient**: only Stage 1 (and the write stages) touch Polarion; verify
  and report do not. If seats are full (HTTP 401 "limit of 15 concurrent users"),
  retry later.
- **Inspection defects are opt-in**: `--defects` adds the stage that writes and
  creates `Ins_defect` work items. Without it the run touches no work item other
  than the `REVIEW` one.
- **Checklist-driven**: criteria come from the document's governed Polarion
  review checklist (or, if none, the organization's official template document,
  fetched at run time) — not from hand-authored rules. The review is authored in
  **English**; the source checklist text is kept verbatim.
- **Template currency**: every run fetches the official template for the doctype
  and diffs it against the document's checklist. If the checklist is behind, it
  is **overwritten with the template's own table before the review runs**, so
  the review is always made against current criteria — the same thing a reviewer
  does by hand when the format changes. Verdicts already there are not carried
  over: a changed template means changed criteria, so they are stale by
  definition. Each reviewer has their own REVIEW work item, so only the current
  user's copy is ever rewritten. Refused unless the template came from a live
  fetch, and gated on type + ownership like every other write. `--no-sync`
  reviews the checklist as it stands.
- **Stops early when nothing can be written**: right after Stage 1 the run
  checks the deterministic fetch output against the writer's own rules -- no
  review work item, an unresolvable header, a checklist the writer parses
  differently -- and aborts there instead of spending verify and report and
  then dying in Stage 4 naming the wrong cause. It runs Stage 4's own layout
  gate rather than re-deriving the answer. `--no-write` is exempt, and
  `--dry-run` only warns, since a preview's product is the report.
- **Configuration**: instance-specific names (work item types, checklist field,
  the header keywords identifying each column, template locations) live in
  `config/polarion.json`, selected by the document's project. Another project or
  checklist dialect = another profile, no code change.
