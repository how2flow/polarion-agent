#!/bin/bash
# reviewer project: doc-review -> doc-verify -> report  (read-only)
#
#   doc-review reads the Polarion document ONCE (~1 license seat) and writes a
#   content snapshot + findings. doc-verify and the report stage then run on the
#   snapshot with no Polarion access (no extra seats, consistent snapshot).
set -e
source "$(cd "$(dirname "$0")"; while [ ! -f .polarion-root ]; do cd ..; done; pwd)/scripts/params.sh"

require_cli || exit 1
require_polarion_config || exit 1

REVIEW_MD="$POLARION_SKILLS_DIR/doc-review/doc-review.md"
REVIEW_REQ="$POLARION_SKILLS_DIR/doc-review/requirements.md"
VERIFY_MD="$POLARION_SKILLS_DIR/doc-verify/doc-verify.md"

# Default: review -> verify -> report -> write checklist. The ONLY work item
# written by default is the REVIEW one (its checklist field); every write is
# guarded by the script's type + ownership + lost-update + layout gates.
# Inspection defects live in Ins_defect work items — a different type, and
# creating one adds a work item to the document — so that stage is OPT-IN.
#   --no-write   : skip write-back entirely (review + report only)
#   --defects    : also run the inspection-defect stage (writes/creates Ins_defect)
#   --no-sync    : review the checklist as it stands, even if it is behind the
#                  official template (default: overwrite it with the template)
#   --dry-run    : run the write stages but do not actually PATCH/create
WRITE=1; DEFECTS=0; SYNC=1; DRYRUN=0; POS=()
for a in "$@"; do
    case "$a" in
        --no-write) WRITE=0 ;;
        --dry-run)  DRYRUN=1 ;;
        --write)    WRITE=1 ;;   # explicit; default anyway
        --defects)  DEFECTS=1 ;;
        --no-sync)  SYNC=0 ;;
        *)          POS+=("$a") ;;
    esac
done
TARGET="$(resolve_target "${POS[0]}")" || {
    echo "Usage: run.sh '<doc-URL | project/space/document>' [report.md] [--no-write] [--defects] [--no-sync] [--dry-run]" >&2
    echo "  (run with no URL in an interactive shell to be prompted)" >&2
    exit 1
}

TS="$(date +%Y%m%d_%H%M%S)"
PROJ_DIR="$POLARION_ROOT/projects/reviewer"
OUT_DIR="$PROJ_DIR/out"; mkdir -p "$OUT_DIR"
REPORT="${POS[1]:-$OUT_DIR/review-$TS.md}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT     # checklist/verified are copied into out/ below
CONTENT="$WORK/content.md"
FINDINGS="$WORK/findings.json"
VERIFIED="$WORK/verified.json"
CHECKLIST="$WORK/checklist.json"
FETCH="$POLARION_SKILLS_DIR/doc-review/fetch_checklist.py"
# A checklist behind the official template is overwritten with the template's
# own table BEFORE the review, so the review runs on current criteria. Verdicts
# recorded against superseded criteria are not carried over -- a changed
# template means changed criteria, which is what makes them stale.
FETCH_FLAGS=""
[ "$SYNC" = "1" ] && FETCH_FLAGS="--sync-template"
[ "$DRYRUN" = "1" ] && FETCH_FLAGS="$FETCH_FLAGS --dry-run"
LOG_DIR="$PROJ_DIR/schedule/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$TS.log"

# Every stage pipes into tee, so the pipeline's status is tee's (0) and `set -e`
# never sees a failure. Stage failures are therefore collected explicitly from
# PIPESTATUS and re-raised as the script's exit code: a write that aborted must
# not look like a successful run (cron would never notice otherwise).
FAILED=()
run_stage() {  # $1 = stage label ; $2.. = command
    local label="$1"; shift
    "$@" 2>&1 | tee -a "$LOG"
    local rc=${PIPESTATUS[0]}
    if [ "$rc" -ne 0 ]; then
        FAILED+=("$label (exit $rc)")
        error "$label failed (exit $rc) — see $LOG"
    fi
    return 0
}

run_agent() {  # $1 = prompt ; $2.. = allowedTools (claude only)
    local prompt="$1"; shift
    if [ "$CLI_NAME" = "claude" ]; then
        "$CLI_BIN" -p "$prompt" --allowedTools "$@" --max-turns 40 2>&1 | tee -a "$LOG"
    else
        "$CLI_BIN" $CLI_PROMPT_FLAG "$prompt" 2>&1 | tee -a "$LOG"
    fi
}

echo "=== reviewer $TS | target: $TARGET ===" | tee "$LOG"

# wait for a free ALM seat before touching Polarion (best-effort pre-gate)
wait_for_seat

# --- Stage 1: doc-review (reads Polarion once; ~1 seat) ---
info "Stage 1: doc-review (read + review)"
run_agent "$(cat "$REVIEW_MD")

=== requirements (spec + finding schema) ===
$(cat "$REVIEW_REQ")

=== concrete paths for this run ===
TARGET: $TARGET
Fetch script (run with Bash, python3): $FETCH
Pass these flags to it verbatim: $FETCH_FLAGS
Write the fetched checklist JSON to: $CHECKLIST
Write the document content snapshot to: $CONTENT
Write the findings JSON to: $FINDINGS
Identify ALL review work item ids from the document parts and pass them
comma-separated as --review-wi (fetch_checklist deterministically picks the one
the current user owns as source_wi); likewise pass any Ins_defect ids as
--defect-wi (more robust than its discovery query).
You MUST write $FINDINGS (a filled checklist) before stopping. Do NOT stop to ask
questions: if there are multiple REVIEW work items or any other ambiguity, choose
a sensible default (prefer the REVIEW WI whose reviewer is the current user),
note the choice, and proceed. Writing $CONTENT/$CHECKLIST but not $FINDINGS is a
failure. Then stop." \
    Bash Read Write "mcp__polarion__*"
[ -s "$CONTENT" ]  || { error "no content snapshot produced. Aborting."; exit 1; }
[ -s "$FINDINGS" ] || { error "no findings produced. Aborting."; exit 1; }
# Preserve Stage 1's output BEFORE any later gate can exit: it cost an ALM seat,
# and verify/report/write can all be retried offline from these.
cp "$CONTENT"   "$OUT_DIR/.content-$TS.md"       2>/dev/null || true
cp "$FINDINGS"  "$OUT_DIR/.findings-$TS.json"    2>/dev/null || true
cp "$CHECKLIST" "$OUT_DIR/.checklist-$TS.json"   2>/dev/null || true
info "stage 1 output saved: $OUT_DIR/.{content,findings,checklist}-$TS.*"

# Nothing writable? Say so here, not three AI stages later. The deterministic
# fetch already knows, and template_write_problem is already exactly this
# predicate -- "ways the HTML parses cleanly and is still unusable, each
# ending with Stage 4 unable to write the verdicts back". It was only ever
# applied to the template; over the real corpus it fires on 32 doomed items
# and no healthy one. Re-implementing a weaker subset here is how the first
# version of this gate missed the very shape it was written for.
# --no-write is exempt: a report with no work item behind it is a real use.
# --dry-run only warns: a preview's product IS the report, and aborting here
# would be the one thing a dry run must not do -- destroy its own output.
if [ "$WRITE" = "1" ]; then
python3 - "$CHECKLIST" "$DRYRUN" "$POLARION_SKILLS_DIR" <<'EOF' || { error "Stage 1 produced nothing Stage 4 can write back; Stage 1 output kept in $OUT_DIR. Aborting."; exit 1; }
import json, os, sys
path, dry, skills = sys.argv[1], sys.argv[2] == "1", sys.argv[3]
try:
    c = json.load(open(path))
except (OSError, ValueError):
    sys.exit(0)          # no fetch output to judge; the later gates still apply
sys.path.insert(0, os.path.join(skills, "doc-review"))
try:
    import fetch_checklist as F
except ImportError:
    F = None             # advisory only; never fail the run on an import

cols = c.get("columns") or {}
preview_only = False
sync = c.get("template_sync") or {}
st = c.get("status")
why = None
if st == "no-review-wi":
    why = ("the document has no review work item -- there is nowhere to write "
           "the verdicts (use --no-write for a report-only run)")
elif c.get("source_wi") and c.get("source_wi_reviewer_matched") is False:
    why = ("the current user is not an assigned reviewer/assignee on %s -- "
           "Stage 4 refuses to write a review work item it does not own "
           "(use --no-write for a report-only run)" % c["source_wi"])
elif st == "review-wi-no-checklist":
    if sync.get("dry_run"):
        # sync_to_template clears every real gate before its dry-run return,
        # so this is the one shape a real run would NOT stop on: it would
        # have filled the checklist from the template and reviewed that.
        why = ("this preview reviewed the official template; a real run "
               "would have written it into the checklist first")
        preview_only = True
    else:
        why = "the review work item has no checklist to fill"
        if sync.get("reason"):      # say what actually stopped the sync
            why += ", and the template sync did not fill one: " + sync["reason"]
        why += " (use --no-write for a report-only run)"
elif not cols.get("roles"):
    why = ("the checklist's column layout could not be resolved: "
           + (cols.get("error") or "unknown"))
elif F is not None:
    p = F.template_write_problem(c)
    if p:
        why = "this checklist cannot be written back -- " + p
    else:
        # Not a re-implementation: run Stage 4's own layout gate, on Stage 4's
        # own input. The two sides can read different headers out of the same
        # HTML -- a note nested inside the header row leaves the writer with
        # '※ ... 분류' as cell 0 -- and only the writer's parser can say so.
        try:
            import io, contextlib
            import write_checklist as Wr
            import polarion_config as pcfg
            prof = pcfg.profile_for(
                project_id=(c.get("document") or {}).get("project_id"),
                name=c.get("profile") if c.get("profile_matched") else None,
                quiet=True)
            with contextlib.redirect_stdout(io.StringIO()):
                Wr.resolve_layout(c.get("checklist_html") or "",
                                  {"columns": cols}, pcfg.column_rules(prof))
        except SystemExit as e:
            why = "Stage 4 would refuse this checklist: %s" % e
        except ImportError:
            pass

if not why:
    sys.exit(0)
if dry:
    print("  [warn] " + (why if preview_only
                         else "a real run would stop here: " + why))
    sys.exit(0)
sys.exit("  " + why)
EOF
fi

# results[] IS the review. Stage 4 refuses without it anyway, but only after
# verify and report have each spent minutes on an empty review. This runs
# AFTER the copies above: aborting here deletes $WORK via the EXIT trap, and a
# partial review is exactly what a human may want to retry from.
python3 - "$FINDINGS" "$CHECKLIST" <<'EOF' || { error "Stage 1 findings unusable; Stage 1 output kept in $OUT_DIR. Aborting."; exit 1; }
import json, sys
try:
    f = json.load(open(sys.argv[1]))
except ValueError as e:
    sys.exit(f"  findings JSON is malformed: {e}")
if not isinstance(f, dict):
    sys.exit(f"  findings JSON is a {type(f).__name__}, not an object")
n = len(f.get("results") or f.get("rows") or [])
if not n:
    sys.exit("  findings JSON has no results[] -- the review was not recorded")
try:
    want = json.load(open(sys.argv[2])).get("data_row_count")
except (OSError, ValueError):
    want = None   # no checklist to count against; the emptiness check stands alone
if want and n != want:
    sys.exit(f"  findings JSON has {n} results for {want} checklist rows")
print(f"  findings carry {n} verdicts")
EOF

# --- Stage 2: doc-verify (no Polarion) ---
info "Stage 2: doc-verify (independent re-review)"
run_agent "$(cat "$VERIFY_MD")

Content snapshot file: $CONTENT
Findings JSON file: $FINDINGS
Write the verified findings JSON to: $VERIFIED
Then stop." \
    Read Write
[ -s "$VERIFIED" ] || { error "verify produced no output. Aborting."; exit 1; }
# persist verified findings (gitignored) so a write can be retried if a seat is lost
cp "$VERIFIED" "$OUT_DIR/.verified-$TS.json" 2>/dev/null && info "verified saved: $OUT_DIR/.verified-$TS.json" || true

# --- Stage 3: report (no Polarion) ---
info "Stage 3: report"
run_agent "Render a Markdown review report from the verified findings JSON at
$VERIFIED (document under review: $TARGET).

The JSON is a filled review checklist: top-level document metadata,
checklist_source (governed | default-template | made-by-agent), source_wi, and
results[] where each item has: category (the checklist's own category), item,
criteria, applicable (O/X), result (🟩Pass/🟥Fail/N/A), evidence, comment, and
verdict {status: confirmed|adjusted|rejected, confidence, note}.

- Header: document metadata, checklist_source + source_wi, and counts computed
  FROM results[] — by verdict (confirmed/adjusted/rejected) and by result
  (Fail/Pass/N/A). Do not trust any embedded summary; compute from the items.
- Group confirmed+adjusted items by their 'category'; within a category order
  Fail > Pass > N/A. Some checklist layouts have no category column — when every
  'category' is empty, skip the grouping and list the items in checklist order. For each show: result + verdict(status, confidence),
  applicable, criteria, evidence, comment, verifier note, and any work-item ids
  cited in the text. There is no severity field — do not invent one.
- A separate '## Rejected (filtered by verify)' section with reasons.
- If template_sync says synced=true, the checklist WAS behind the official
  template and the run already replaced it with the template's own table before
  reviewing; template_check describes the state BEFORE that. Add a one-paragraph
  '## Checklist template check' section saying so — name the template and the
  previous status (template_sync.was) — and report it as resolved. Do not list
  missing_items: those items are in the checklist now and were reviewed. Then
  skip the rest of this bullet.
- Otherwise, if the JSON has template_check with status 'outdated' or 'diverged',
  add a '## Checklist template check' section: state that this document's checklist
  does not match the organization's official template (name it and its work item),
  then list missing_items (item + criteria) verbatim, plus any changed_criteria
  / extra_items / layout_diff.
  State the baseline this was measured against: template_check.template.source
  ('live' = fetched this run, 'cache' = a stored copy because the live fetch
  failed) and template.fetched. If it is not 'live', say so in the first line
  of the section — the mismatch may already have been fixed.
  Say plainly that this is a process note, not a review finding, and do not
  prescribe what anyone should do about it. Skip the section entirely when
  status is 'current'. When status is 'unknown', report template_check.reason
  and any layout_diff instead of a missing-item list.
- A '## Inspection defects' section: list existing ones (top-level
  inspection_defects: id/status/title) and any newly proposed defect content
  (results[].inspection_defect) with the finding it came from.
- Be faithful to the JSON; do not invent.
- Write the report prose in ENGLISH; quote the checklist category/item/criteria
  verbatim (these may be Korean) — do not translate the source checklist.
Write the report to: $REPORT  Then stop." \
    Read Write

ok "Review report: $REPORT"
echo "Report: $REPORT"

# --- Stage 4: write the filled checklist back to Polarion (default on) ---
if [ "$WRITE" = "1" ]; then
    DRY=""; [ "$DRYRUN" = "1" ] && DRY="--dry-run"
    # The writers must use the SAME profile the fetch resolved — otherwise a
    # --profile passed in Stage 1 would silently not apply to the write, giving
    # it a different checklist_field / review_type / column_roles. Read it from
    # the deterministic fetch output, not from the AI's findings.
    # Array, not a string: a profile key may legally contain whitespace, and an
    # unquoted expansion would split it into extra argv entries.
    # Passed ONLY when the fetch actually matched a profile — forcing --profile
    # would make the writers report a defaulted profile as authoritative and
    # swallow their own "no profile matches" warning.
    PROFILE_ARG=()
    if [ -s "$CHECKLIST" ]; then
        P=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1]));
print(d.get("profile") or "" if d.get("profile_matched") else "")' "$CHECKLIST" 2>/dev/null || true)
        if [ -n "$P" ]; then
            PROFILE_ARG=(--profile "$P"); info "writers use profile: $P"
        else
            info "writers resolve the profile themselves (fetch matched none)"
        fi
    fi
    info "Stage 4: write checklist back to Polarion ${DRY:+(dry-run)}"
    run_stage "Stage 4 (write checklist)" \
        python3 "$POLARION_SKILLS_DIR/doc-review/write_checklist.py" --filled "$VERIFIED" "${PROFILE_ARG[@]}" $DRY

    # --- Stage 5: inspection defects (OPT-IN: writes/creates Ins_defect work items) ---
    if [ "$DEFECTS" = "1" ]; then
        info "Stage 5: write inspection defects ${DRY:+(dry-run)}"
        run_stage "Stage 5 (write defects)" \
            python3 "$POLARION_SKILLS_DIR/doc-review/write_defects.py" --filled "$VERIFIED" "${PROFILE_ARG[@]}" $DRY
    else
        info "Stage 5 (inspection defects) skipped — REVIEW work item only. Use --defects to include it."
    fi

else
    info "Stages 4-5 (write-back) skipped (--no-write)"
fi

# Rotate out/: each run leaves a report plus the dotfiles kept for retries, and
# nothing else prunes them (functions.sh rotates skill logs, not this project's
# output). Keep the most recent REVIEW_KEEP runs of each kind.
rotate() {  # $1 = glob
    ls -t $1 2>/dev/null | tail -n +$((${POLARION_REVIEW_KEEP:-20} + 1)) | xargs rm -f 2>/dev/null || true
}
rotate "$OUT_DIR/review-*.md"
rotate "$OUT_DIR/.content-*.md"
rotate "$OUT_DIR/.findings-*.json"
rotate "$OUT_DIR/.checklist-*.json"
rotate "$OUT_DIR/.verified-*.json"
rotate "$LOG_DIR/*.log"

if [ ${#FAILED[@]} -gt 0 ]; then
    error "run finished with failures: ${FAILED[*]}"
    exit 1
fi
ok "run complete"
