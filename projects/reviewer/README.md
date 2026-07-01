# reviewer (project)

Orchestrates a full review of a Polarion document from just its URL:

```
doc-review ──> doc-verify ──> report ──> write checklist ──> write defects
 (read+review)  (independent    (Markdown)  (into REVIEW WI)  (into Ins_defect WIs)
                 re-check)
```

`./schedule/run.sh '<document-URL>'` runs everything end-to-end, including
writing the filled checklist and inspection-defect content back to Polarion.
Every write is guarded by the write scripts' **ownership gate** (your PAT user
must be the document's assigned reviewer) + **lost-update** check, so an
un-owned or drifted target aborts instead of writing.

- **doc-review** (skill) — reads the document **once** (Polarion; ~1 license
  seat), fetches the governed checklist + existing inspection defects, and emits
  a content snapshot + findings.
- **doc-verify** (skill) — an independent instance re-checks the findings
  (rejects false positives, adjusts, confirms, catches major misses; ASPICE
  lens). No Polarion access.
- **report** — renders the verified findings into a Markdown report.
- **write checklist** (`write_checklist.py`) — surgically injects 리뷰대상/판정/
  의견 into the REVIEW work item's `checklist` field (template + 결함상태 preserved).
- **write defects** (`write_defects.py`) — fills/append/skips inspection-defect
  content into existing `Ins_defect` work items per the reviewer's decision.

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

# run the write stages but don't actually PATCH (preview)
./projects/reviewer/schedule/run.sh '<URL>' --dry-run

# schedule a weekly review of a fixed document (Mon 07:00 by default)
./projects/reviewer/schedule/install.sh '<target>' '0 7 * * 1'
./projects/reviewer/schedule/install.sh --remove
```

## Notes

- **Write-back is ON by default.** The pipeline writes the filled checklist and
  inspection-defect content back to Polarion, each guarded by an ownership gate
  (your PAT user must be the document's assigned reviewer) + lost-update check.
  Use `--no-write` for review-only, `--dry-run` to preview writes.
- **Seat-efficient**: only Stage 1 (and the write stages) touch Polarion; verify
  and report do not. If seats are full (HTTP 401 "limit of 15 concurrent users"),
  retry later.
- **Checklist-driven**: criteria come from the document's governed Polarion
  review checklist (or, if none, a per-doctype default template under
  `skills/doc-review/schema/`) — not from hand-authored rules. The review is
  authored in **English**; the source checklist text is kept verbatim. Creating
  **new** `Ins_defect` work items is still out of scope (write-back only fills
  existing checklist / defect fields).
