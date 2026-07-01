#!/bin/bash
# Standalone doc-localize: fetch a document's reviews and save a Korean copy.
# Read-only. Run with a URL, or with no arg to be prompted interactively.
set -e
source "$(cd "$(dirname "$0")"; while [ ! -f .polarion-root ]; do cd ..; done; pwd)/scripts/params.sh"

require_cli || exit 1
require_polarion_config || exit 1

TARGET="$(resolve_target "$1")" || {
    echo "Usage: run.sh '<doc-URL | project/space/document>'  (or run with no arg to be prompted)" >&2
    exit 1
}

SKILL_DIR="$POLARION_SKILLS_DIR/doc-localize"
FETCH="$POLARION_SKILLS_DIR/doc-review/fetch_checklist.py"
TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$SKILL_DIR/schedule/logs"; mkdir -p "$LOG_DIR"
OUT_DIR="$SKILL_DIR/out"; mkdir -p "$OUT_DIR"
LOG="$LOG_DIR/$TS.log"

wait_for_seat   # best-effort: wait for a free ALM seat before reading Polarion
echo "=== doc-localize $TS | target: $TARGET ===" | tee "$LOG"
if [ "$CLI_NAME" = "claude" ]; then
    "$CLI_BIN" -p "$(cat "$SKILL_DIR/doc-localize.md")

TARGET: $TARGET
Fetch script (run with Bash, python3): $FETCH
Save the Korean report into the directory: $OUT_DIR
Name it: review-ko-<SAFE_TITLE>-$TS.md  (SAFE_TITLE = document title, whitespace and '/' -> '_', <=60 chars)
Then stop." \
        --allowedTools Bash Read Write "mcp__polarion__*" --max-turns 40 2>&1 | tee -a "$LOG"
else
    "$CLI_BIN" $CLI_PROMPT_FLAG "$(cat "$SKILL_DIR/doc-localize.md") TARGET: $TARGET" 2>&1 | tee -a "$LOG"
fi

# the agent names the file by title; resolve it via the shared timestamp
REPORT="$(ls -1t "$OUT_DIR"/*-"$TS".md 2>/dev/null | head -1)"
if [ -n "$REPORT" ]; then
    ok "Korean review: $REPORT"
    echo "Report(ko): $REPORT"
else
    warn "Korean report not found (name should be review-ko-<title>-$TS.md); see log $LOG"
fi
