#!/bin/bash
# Install a cron job for doc-verify on fixed findings/content files.
# NOTE: doc-verify is normally driven by the `reviewer` project; a standalone
# cron only makes sense for a fixed pair of input files.
set -e
source "$(cd "$(dirname "$0")"; while [ ! -f .polarion-root ]; do cd ..; done; pwd)/scripts/params.sh"

TAG="# doc-verify-scheduled"
RUN="$POLARION_ROOT/skills/doc-verify/schedule/run.sh"

if [ "$1" = "--remove" ]; then
    remove_cron "$TAG"
    exit 0
fi

FINDINGS="$1"
CONTENT="$2"
CRON="${3:-0 7 * * 1}"
if [ -z "$FINDINGS" ] || [ -z "$CONTENT" ]; then
    echo "Usage: install.sh <findings.json> <content.md> ['cron expr']  |  install.sh --remove" >&2
    exit 1
fi

require_cli || exit 1
chmod +x "$RUN"
install_cron "$CRON" "$RUN '$FINDINGS' '$CONTENT'" "$TAG"
info "Run manually: $RUN '$FINDINGS' '$CONTENT'   (or $RUN → prompts)"
info "Logs:         $POLARION_ROOT/skills/doc-verify/schedule/logs/"
