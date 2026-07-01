#!/bin/bash
# Standalone doc-review: read + review one document against its governed
# checklist → findings (no verify/report/write — that's the reviewer project).
# Run with a URL, or with no arg to be prompted interactively.
set -e
source "$(cd "$(dirname "$0")"; while [ ! -f .polarion-root ]; do cd ..; done; pwd)/scripts/params.sh"

require_cli || exit 1
require_polarion_config || exit 1

TARGET="$(resolve_target "$1")" || {
    echo "Usage: run.sh '<doc-URL | project/space/document>'  (or run with no arg to be prompted)" >&2
    exit 1
}

SKILL_DIR="$POLARION_SKILLS_DIR/doc-review"
TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$SKILL_DIR/schedule/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$TS.log"
CONTENT="$LOG_DIR/.content-$TS.md"
FINDINGS="$LOG_DIR/.findings-$TS.json"

wait_for_seat   # best-effort: wait for a free ALM seat before reading Polarion
echo "=== doc-review $TS | target: $TARGET ===" | tee "$LOG"
if [ "$CLI_NAME" = "claude" ]; then
    "$CLI_BIN" -p "$(cat "$SKILL_DIR/doc-review.md")

=== requirements (rubric + finding schema) ===
$(cat "$SKILL_DIR/requirements.md")

TARGET: $TARGET
Fetch script (run with Bash, python3): $SKILL_DIR/fetch_checklist.py
Write the document content snapshot to: $CONTENT
Write the findings JSON to: $FINDINGS
Then stop." \
        --allowedTools Bash Read Write "mcp__polarion__*" --max-turns 40 2>&1 | tee -a "$LOG"
else
    "$CLI_BIN" $CLI_PROMPT_FLAG "$(cat "$SKILL_DIR/doc-review.md") TARGET: $TARGET" 2>&1 | tee -a "$LOG"
fi

ok "findings: $FINDINGS"
echo "Findings: $FINDINGS"
