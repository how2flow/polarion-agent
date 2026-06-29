#!/bin/bash
# Polarion MCP server launcher (CLI-agnostic).
#
# Loads POLARION_URL / POLARION_TOKEN from .env (or inherits them if already
# exported in your shell), then execs the server. No secret is ever stored in
# any CLI's MCP config — the single source of truth is .env (gitignored) or
# your exported environment. claude / codex / gemini all launch THIS script.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")"; while [ ! -f .polarion-root ]; do cd ..; done; pwd)"

# .env values do not clobber variables already exported in the environment.
if [ -f "$ROOT/.env" ]; then
    set -a
    source "$ROOT/.env"
    set +a
fi

# uvx typically lives in ~/.local/bin; CLIs may spawn us with a minimal PATH.
export PATH="$HOME/.local/bin:$PATH"

exec uvx mcp-server-polarion
