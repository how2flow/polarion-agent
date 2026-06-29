#!/bin/bash
# params.sh - Variable initialization only
# Sources functions.sh, then calls functions to populate variables
# All scripts source this file: source "$(dirname "$0")/../scripts/params.sh"

[ -n "$POLARION_PARAMS_LOADED" ] && return 0
POLARION_PARAMS_LOADED=1

# Paths
POLARION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")"; while [ ! -f .polarion-root ]; do cd ..; done; pwd)"
POLARION_SCRIPTS_DIR="$POLARION_ROOT/scripts"
POLARION_SKILLS_DIR="$POLARION_ROOT/skills"

# Load .env if exists (for cron environment)
if [ -f "$POLARION_ROOT/.env" ]; then
    set -a
    source "$POLARION_ROOT/.env"
    set +a
fi

# Ensure common paths (cron has minimal PATH; uvx lives in ~/.local/bin)
export PATH="$HOME/.local/bin:$HOME/.nvm/versions/node/$(ls $HOME/.nvm/versions/node/ 2>/dev/null | tail -1)/bin:$PATH:/usr/local/bin" 2>/dev/null

# Load functions
source "$POLARION_SCRIPTS_DIR/functions.sh"

# Detect CLI and set CLI_* variables
CLI_NAME="${POLARION_CLI_OVERRIDE:-$(detect_cli)}"
CLI_BIN=$(command -v "$CLI_NAME" 2>/dev/null || echo "")
get_cli_config "$CLI_NAME"

# Polarion defaults
POLARION_URL="${POLARION_URL:-}"

# Debug
if [ "${POLARION_DEBUG:-0}" = "1" ]; then
    echo "[params] CLI_NAME=$CLI_NAME"
    echo "[params] CLI_BIN=$CLI_BIN"
    echo "[params] CLI_HOME=$CLI_HOME"
    echo "[params] CLI_COMMANDS_DIR=$CLI_COMMANDS_DIR"
    echo "[params] POLARION_ROOT=$POLARION_ROOT"
    echo "[params] POLARION_URL=$POLARION_URL"
fi
