#!/bin/bash
# Install a cron job that runs a standalone doc-review on a fixed document.
set -e
source "$(cd "$(dirname "$0")"; while [ ! -f .polarion-root ]; do cd ..; done; pwd)/scripts/params.sh"

TAG="# doc-review-scheduled"
RUN="$POLARION_ROOT/skills/doc-review/schedule/run.sh"

if [ "$1" = "--remove" ]; then
    remove_cron "$TAG"
    exit 0
fi

TARGET="$1"
CRON="${2:-0 7 * * 1}"
if [ -z "$TARGET" ]; then
    echo "Usage: install.sh '<doc-URL | project/space/document>' ['cron expr']  |  install.sh --remove" >&2
    exit 1
fi

require_cli || exit 1
require_polarion_config || exit 1
chmod +x "$RUN"
install_cron "$CRON" "$RUN '$TARGET'" "$TAG"
info "Run manually: $RUN '$TARGET'   (or $RUN  → prompts for URL)"
info "Logs:         $POLARION_ROOT/skills/doc-review/schedule/logs/"
