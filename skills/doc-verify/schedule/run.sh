#!/bin/bash
# Standalone doc-verify: independently re-check an existing findings JSON against
# its content snapshot → verified findings. No Polarion access.
# NOTE: doc-verify is normally a stage of the `reviewer` project; running it alone
# needs a prior doc-review's outputs (a findings JSON + its content snapshot).
set -e
source "$(cd "$(dirname "$0")"; while [ ! -f .polarion-root ]; do cd ..; done; pwd)/scripts/params.sh"

require_cli || exit 1

FINDINGS="$1"
if [ -z "$FINDINGS" ] && [ -t 0 ]; then
    printf 'Path to findings JSON (from doc-review): ' >&2; read -r FINDINGS
fi
CONTENT="$2"
if [ -z "$CONTENT" ] && [ -t 0 ]; then
    printf 'Path to content snapshot (.md): ' >&2; read -r CONTENT
fi
if [ ! -f "$FINDINGS" ] || [ ! -f "$CONTENT" ]; then
    echo "Usage: run.sh <findings.json> <content.md>  (or run with no args to be prompted)" >&2
    exit 1
fi

SKILL_DIR="$POLARION_SKILLS_DIR/doc-verify"
TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$SKILL_DIR/schedule/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$TS.log"
VERIFIED="$LOG_DIR/.verified-$TS.json"

echo "=== doc-verify $TS | findings: $FINDINGS ===" | tee "$LOG"
if [ "$CLI_NAME" = "claude" ]; then
    "$CLI_BIN" -p "$(cat "$SKILL_DIR/doc-verify.md")

Content snapshot file: $CONTENT
Findings JSON file: $FINDINGS
Write the verified findings JSON to: $VERIFIED
Then stop." \
        --allowedTools Read Write --max-turns 30 2>&1 | tee -a "$LOG"
else
    "$CLI_BIN" $CLI_PROMPT_FLAG "$(cat "$SKILL_DIR/doc-verify.md") findings: $FINDINGS content: $CONTENT" 2>&1 | tee -a "$LOG"
fi

ok "verified: $VERIFIED"
echo "Verified: $VERIFIED"
