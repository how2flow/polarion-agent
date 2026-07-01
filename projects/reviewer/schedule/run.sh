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

# Full auto by default: review -> verify -> report -> write checklist -> write defects.
# Writes are guarded by each script's ownership gate + lost-update check.
#   --no-write : skip the write-back stages (review + report only)
#   --dry-run  : run the write stages but do not actually PATCH
WRITE=1; DRYRUN=0; POS=()
for a in "$@"; do
    case "$a" in
        --no-write) WRITE=0 ;;
        --dry-run)  DRYRUN=1 ;;
        --write)    WRITE=1 ;;   # explicit; default anyway
        *)          POS+=("$a") ;;
    esac
done
TARGET="$(resolve_target "${POS[0]}")" || {
    echo "Usage: run.sh '<doc-URL | project/space/document>' [report.md] [--no-write] [--dry-run]" >&2
    echo "  (run with no URL in an interactive shell to be prompted)" >&2
    exit 1
}

TS="$(date +%Y%m%d_%H%M%S)"
PROJ_DIR="$POLARION_ROOT/projects/reviewer"
OUT_DIR="$PROJ_DIR/out"; mkdir -p "$OUT_DIR"
REPORT="${POS[1]:-$OUT_DIR/review-$TS.md}"
WORK="$(mktemp -d)"
CONTENT="$WORK/content.md"
FINDINGS="$WORK/findings.json"
VERIFIED="$WORK/verified.json"
CHECKLIST="$WORK/checklist.json"
FETCH="$POLARION_SKILLS_DIR/doc-review/fetch_checklist.py"
LOG_DIR="$PROJ_DIR/schedule/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$TS.log"

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
criteria, applicable (O/X), result (Pass/Fail/N/A), evidence, comment, and
verdict {status: confirmed|adjusted|rejected, confidence, note}.

- Header: document metadata, checklist_source + source_wi, and counts computed
  FROM results[] — by verdict (confirmed/adjusted/rejected) and by result
  (Fail/Pass/N/A). Do not trust any embedded summary; compute from the items.
- Group confirmed+adjusted items by their 'category'; within a category order
  Fail > Pass > N/A. For each show: result + verdict(status, confidence),
  applicable, criteria, evidence, comment, verifier note, and any work-item ids
  cited in the text. There is no severity field — do not invent one.
- A separate '## Rejected (filtered by verify)' section with reasons.
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
    info "Stage 4: write checklist back to Polarion ${DRY:+(dry-run)}"
    python3 "$POLARION_SKILLS_DIR/doc-review/write_checklist.py" --filled "$VERIFIED" $DRY 2>&1 | tee -a "$LOG"

    # --- Stage 5: write inspection-defect content (fill/append/skip per reviewer) ---
    info "Stage 5: write inspection defects ${DRY:+(dry-run)}"
    python3 "$POLARION_SKILLS_DIR/doc-review/write_defects.py" --filled "$VERIFIED" $DRY 2>&1 | tee -a "$LOG"
else
    info "Stages 4-5 (write-back) skipped (--no-write)"
fi
