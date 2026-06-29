#!/bin/bash
# functions.sh - All function declarations (no executable statements)
# Sourced via params.sh

[ -n "$POLARION_FUNCTIONS_LOADED" ] && return 0
POLARION_FUNCTIONS_LOADED=1

# ============================================================
# Output helpers
# ============================================================
info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*" >&2; }
error() { echo "[ERROR] $*" >&2; }
ok()    { echo "[OK]    $*"; }

# ============================================================
# CLI detection
# ============================================================
detect_cli() {
    local parent
    parent=$(ps -o comm= -p $PPID 2>/dev/null)
    case "$parent" in
        *claude*) echo "claude"; return ;;
        *codex*)  echo "codex";  return ;;
        *gemini*) echo "gemini"; return ;;
    esac

    if command -v claude &>/dev/null; then echo "claude"; return; fi
    if command -v codex &>/dev/null;  then echo "codex";  return; fi
    if command -v gemini &>/dev/null; then echo "gemini"; return; fi

    echo ""
}

get_cli_config() {
    local cli="$1"
    case "$cli" in
        claude)
            CLI_HOME="$HOME/.claude"
            CLI_COMMANDS_DIR="$CLI_HOME/commands"
            CLI_GLOBAL_MCP="$CLI_HOME/settings.json"
            CLI_PROJECT_MCP="$POLARION_ROOT/.mcp.json"
            CLI_MCP_FORMAT="json"
            CLI_MCP_KEY="mcpServers"
            CLI_PROMPT_FLAG="-p"
            CLI_TOOLS_FLAGS=(--allowedTools "Bash" "Read" "Write" "Edit" "Glob" "Grep"
                "mcp__polarion__*"
            )
            ;;
        codex)
            CLI_HOME="$HOME/.codex"
            CLI_COMMANDS_DIR="$CLI_HOME/commands"
            CLI_GLOBAL_MCP="$CLI_HOME/config.toml"
            CLI_PROJECT_MCP="$POLARION_ROOT/.codex/config.toml"
            CLI_MCP_FORMAT="toml"
            CLI_MCP_KEY="mcp_servers"
            CLI_PROMPT_FLAG="-q"
            CLI_TOOLS_FLAGS=()
            ;;
        gemini)
            CLI_HOME="$HOME/.gemini"
            CLI_COMMANDS_DIR="$CLI_HOME/commands"
            CLI_GLOBAL_MCP="$CLI_HOME/settings.json"
            CLI_PROJECT_MCP="$POLARION_ROOT/.gemini/settings.json"
            CLI_MCP_FORMAT="json"
            CLI_MCP_KEY="mcpServers"
            CLI_PROMPT_FLAG="-p"
            CLI_TOOLS_FLAGS=()
            ;;
        *)
            CLI_HOME=""
            CLI_COMMANDS_DIR=""
            CLI_GLOBAL_MCP=""
            CLI_PROJECT_MCP=""
            CLI_MCP_FORMAT=""
            CLI_MCP_KEY=""
            CLI_PROMPT_FLAG=""
            CLI_TOOLS_FLAGS=()
            ;;
    esac
}

# ============================================================
# Polarion config validation
# ============================================================
normalize_polarion_url() {
    # Strip trailing slash and a trailing /polarion context path:
    # mcp-server-polarion appends /polarion/rest/v1 itself, so POLARION_URL
    # must be the bare instance root (e.g. https://host).
    local url="$1"
    url="${url%/}"
    url="${url%/polarion}"
    echo "$url"
}

require_polarion_config() {
    if [ -z "$POLARION_URL" ]; then
        error "POLARION_URL is not set"
        echo "" >&2
        echo "  Set it before running (instance ROOT, no /polarion path):" >&2
        echo "    export POLARION_URL=\"https://your-polarion-host\"" >&2
        echo "" >&2
        echo "  Or add it to .env / your shell profile (~/.bashrc, ~/.zshrc)" >&2
        return 1
    fi
    POLARION_URL="$(normalize_polarion_url "$POLARION_URL")"
    ok "Polarion: $POLARION_URL"
}

# ============================================================
# CLI validation
# ============================================================
require_cli() {
    if [ -z "$CLI_NAME" ]; then
        error "No supported CLI detected (claude/codex/gemini)"
        error "Install one or set POLARION_CLI_OVERRIDE=<cli-name>"
        return 1
    fi
    if [ -z "$CLI_BIN" ]; then
        error "$CLI_NAME is detected but binary not found in PATH"
        return 1
    fi
    ok "CLI: $CLI_NAME ($CLI_BIN)"
}

# ============================================================
# MCP / Polarion access
#
# Unlike Jira (hosted OAuth connector), Polarion has no remote connector, so
# polarion-agent must register a LOCAL stdio MCP server (mcp-server-polarion via uvx).
#
# Secrets are handled the SAME way for every CLI: no token is ever placed in a
# CLI's MCP config. Instead all CLIs launch one shared wrapper
# (scripts/mcp-polarion.sh) that reads POLARION_URL / POLARION_TOKEN from .env
# (or the exported environment) at start. The single source of truth for
# credentials is .env (gitignored) or your shell environment.
# ============================================================
MCP_SERVER_NAME="polarion"
MCP_LAUNCHER="$POLARION_ROOT/scripts/mcp-polarion.sh"

# Persist credentials to .env (gitignored). This is what the launcher reads.
persist_env() {
    local url="$1" token="$2" envf="$POLARION_ROOT/.env"
    {
        echo "POLARION_URL=$url"
        [ -n "$token" ] && echo "POLARION_TOKEN=$token"
    } > "$envf"
    chmod 600 "$envf" 2>/dev/null || true
    ok "Credentials written to $envf (gitignored)"
}

check_polarion_mcp() {
    # Returns 0 if the polarion MCP server is already configured for this CLI.
    case "$CLI_NAME" in
        claude)
            "$CLI_BIN" mcp get "$MCP_SERVER_NAME" >/dev/null 2>&1 && return 0
            ;;
        *)
            [ -f "$CLI_PROJECT_MCP" ] && grep -q "$MCP_SERVER_NAME" "$CLI_PROJECT_MCP" 2>/dev/null && return 0
            ;;
    esac
    return 1
}

# Register the shared launcher with the CLI. No secret is passed here — the
# launcher sources .env / the exported environment itself.
setup_mcp_polarion() {
    local url="$1"
    local token="$2"

    persist_env "$url" "$token"
    chmod +x "$MCP_LAUNCHER" 2>/dev/null || true

    case "$CLI_NAME" in
        claude)
            "$CLI_BIN" mcp remove "$MCP_SERVER_NAME" --scope local >/dev/null 2>&1 || true
            "$CLI_BIN" mcp add "$MCP_SERVER_NAME" --scope local -- "$MCP_LAUNCHER"
            ok "MCP server '$MCP_SERVER_NAME' registered (claude, local scope, no secret in config)"
            ;;
        codex)
            _setup_mcp_toml "$CLI_PROJECT_MCP"
            ok "MCP config written to $CLI_PROJECT_MCP (project-local, codex, no secret in config)"
            ;;
        gemini)
            _setup_mcp_json "$CLI_PROJECT_MCP"
            ok "MCP config written to $CLI_PROJECT_MCP (project-local, gemini, no secret in config)"
            ;;
        *)
            error "CLI not detected, cannot configure MCP"
            return 1
            ;;
    esac
}

remove_mcp_polarion() {
    case "$CLI_NAME" in
        claude)
            "$CLI_BIN" mcp remove "$MCP_SERVER_NAME" --scope local >/dev/null 2>&1 \
                && ok "MCP server '$MCP_SERVER_NAME' removed (claude)" \
                || info "MCP server '$MCP_SERVER_NAME' not found (already removed)"
            ;;
        *)
            info "Remove '$MCP_SERVER_NAME' from $CLI_PROJECT_MCP manually if present"
            ;;
    esac
}

_setup_mcp_json() {
    local mcp_file="$1"
    mkdir -p "$(dirname "$mcp_file")"

    if [ -f "$mcp_file" ]; then
        python3 -c "
import json
with open('$mcp_file', 'r') as f:
    data = json.load(f)
servers = data.setdefault('$CLI_MCP_KEY', {})
servers['$MCP_SERVER_NAME'] = {
    'command': '$MCP_LAUNCHER',
    'args': []
}
with open('$mcp_file', 'w') as f:
    json.dump(data, f, indent=2)
"
    else
        cat > "$mcp_file" <<MCPEOF
{
  "$CLI_MCP_KEY": {
    "$MCP_SERVER_NAME": {
      "command": "$MCP_LAUNCHER",
      "args": []
    }
  }
}
MCPEOF
    fi
}

_setup_mcp_toml() {
    local mcp_file="$1"
    mkdir -p "$(dirname "$mcp_file")"

    if [ -f "$mcp_file" ] && grep -q "mcp_servers.$MCP_SERVER_NAME" "$mcp_file" 2>/dev/null; then
        info "$MCP_SERVER_NAME already in $mcp_file, skipping"
        return 0
    fi

    cat >> "$mcp_file" <<TOMLEOF

[mcp_servers.$MCP_SERVER_NAME]
command = "$MCP_LAUNCHER"
args = []
TOMLEOF
}

# ============================================================
# Symlink helpers
# ============================================================
install_symlink() {
    local source="$1"
    local target="$2"

    if [ ! -f "$source" ]; then
        error "Source not found: $source"
        return 1
    fi

    mkdir -p "$(dirname "$target")"

    if [ -e "$target" ] || [ -L "$target" ]; then
        rm "$target"
    fi

    ln -s "$source" "$target"
    ok "Linked: $target -> $source"
}

remove_symlink() {
    local target="$1"

    if [ -e "$target" ] || [ -L "$target" ]; then
        rm "$target"
        ok "Removed: $target"
    else
        info "Not found (already removed): $target"
    fi
}

# ============================================================
# Cron helpers
# ============================================================
install_cron() {
    local cron_expr="$1"
    local command="$2"
    local tag="$3"

    local existing
    existing=$(crontab -l 2>/dev/null | grep -v "$tag" || true)

    local cron_line="$cron_expr $command $tag"

    if [ -n "$existing" ]; then
        printf '%s\n%s\n' "$existing" "$cron_line" | crontab -
    else
        echo "$cron_line" | crontab -
    fi

    ok "Cron installed: $cron_expr ($tag)"
}

remove_cron() {
    local tag="$1"
    local existing
    existing=$(crontab -l 2>/dev/null | grep -v "$tag" || true)

    if [ -n "$existing" ]; then
        echo "$existing" | crontab -
    else
        crontab -r 2>/dev/null || true
    fi

    ok "Cron removed: $tag"
}

# ============================================================
# Skill management
# ============================================================
install_skill() {
    local skill_name="$1"
    local source_file="$POLARION_SKILLS_DIR/$skill_name/$skill_name.md"
    local target_file="$CLI_COMMANDS_DIR/$skill_name.md"

    require_cli || return 1
    require_polarion_config || return 1
    install_symlink "$source_file" "$target_file"
    info "Usage: Type /$skill_name in $CLI_NAME session"
}

uninstall_skill() {
    local skill_name="$1"
    local target_file="$CLI_COMMANDS_DIR/$skill_name.md"

    remove_symlink "$target_file"
}

# List skill names that have a <skill>/<skill>.md definition.
list_skills() {
    local d name
    for d in "$POLARION_SKILLS_DIR"/*/; do
        [ -d "$d" ] || continue
        name="$(basename "$d")"
        [ -f "$d/$name.md" ] && echo "$name"
    done
}

# ============================================================
# Schedule management
# ============================================================
install_schedule() {
    local skill_name="$1"
    local cron_expr="${2:-0 7 * * 1}"
    local skill_schedule_dir="$POLARION_SKILLS_DIR/$skill_name/schedule"
    local run_script="$skill_schedule_dir/run.sh"
    local cron_tag="# ${skill_name}-scheduled"

    require_cli || return 1
    require_polarion_config || return 1
    chmod +x "$run_script"
    install_cron "$cron_expr" "$run_script" "$cron_tag"

    info "Run manually:  $run_script"
    info "View logs:     ls $skill_schedule_dir/logs/"
    info "Remove:        Use --remove"
}

uninstall_schedule() {
    local skill_name="$1"
    local cron_tag="# ${skill_name}-scheduled"

    remove_cron "$cron_tag"
}

# ============================================================
# Skill runner
# ============================================================
run_skill() {
    local skill_name="$1"
    local rules_dir="$POLARION_SKILLS_DIR/$skill_name/rules"
    local log_dir="$POLARION_SKILLS_DIR/$skill_name/schedule/logs"
    local timestamp=$(date +%Y-%m-%d_%H%M%S)
    local log_file="$log_dir/${timestamp}.log"

    require_cli || return 1
    require_polarion_config || return 1
    mkdir -p "$log_dir"

    echo "=== $skill_name started at $(date '+%Y-%m-%d %H:%M:%S %Z') ===" | tee "$log_file"
    echo "=== CLI: $CLI_NAME ($CLI_BIN) ===" | tee -a "$log_file"

    if [ -d "$rules_dir" ]; then
        # multi-pass: run each rule file separately
        for rule_file in "$rules_dir"/rule*.md; do
            [ ! -f "$rule_file" ] && continue
            local rule_name=$(basename "$rule_file" .md)
            local rule_prompt
            rule_prompt=$(render_template "$rule_file")

            echo "" | tee -a "$log_file"
            echo "--- $rule_name started at $(date '+%H:%M:%S') ---" | tee -a "$log_file"

            $CLI_BIN $CLI_PROMPT_FLAG "$rule_prompt" \
                "${CLI_TOOLS_FLAGS[@]}" \
                --max-turns 50 \
                2>&1 | tee -a "$log_file"

            echo "--- $rule_name finished at $(date '+%H:%M:%S') ---" | tee -a "$log_file"
        done
    else
        # fallback: single-pass with main skill file
        local skill_file="$POLARION_SKILLS_DIR/$skill_name/$skill_name.md"
        if [ ! -f "$skill_file" ]; then
            error "No rules dir or skill file found for $skill_name"
            return 1
        fi

        $CLI_BIN $CLI_PROMPT_FLAG "$(render_template "$skill_file") Execute all rules now." \
            "${CLI_TOOLS_FLAGS[@]}" \
            --max-turns 50 \
            2>&1 | tee -a "$log_file"
    fi

    echo "" | tee -a "$log_file"
    echo "=== $skill_name finished at $(date '+%Y-%m-%d %H:%M:%S %Z') ===" | tee -a "$log_file"

    # Keep only last 30 logs
    ls -t "$log_dir"/*.log 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null || true
}

# ============================================================
# Environment setup (interactive)
# ============================================================
setup_environment() {
    echo "========================================="
    echo "  Polarion Workspace Environment Setup"
    echo "========================================="
    echo ""

    info "Detecting CLI..."
    require_cli || return 1
    echo ""

    # Polarion URL
    info "Checking Polarion config..."
    if [ -z "$POLARION_URL" ]; then
        read -p "  Polarion URL (instance ROOT, no /polarion) [https://]: " input_url
        POLARION_URL="${input_url}"
    fi
    require_polarion_config || return 1
    echo ""

    # MCP server (local mcp-server-polarion)
    info "Checking Polarion MCP server..."
    if check_polarion_mcp; then
        ok "MCP server '$MCP_SERVER_NAME' already configured for $CLI_NAME"
    else
        echo ""
        echo "  A local Polarion MCP server (mcp-server-polarion via uvx) is required."
        echo "  Create a Personal Access Token in Polarion:"
        echo "    My Account -> Personal Access Tokens -> Create Token"
        echo ""
        read -sp "  Polarion Personal Access Token: " input_token
        echo ""
        if [ -z "$input_token" ]; then
            error "Token is required"
            return 1
        fi
        setup_mcp_polarion "$POLARION_URL" "$input_token"
    fi
    echo ""

    # Skills
    info "Installing skills..."
    local skill any=0
    while read -r skill; do
        [ -z "$skill" ] && continue
        install_skill "$skill"
        any=1
    done < <(list_skills)
    [ "$any" = "0" ] && info "No skills defined yet under $POLARION_SKILLS_DIR/"
    echo ""

    echo "========================================="
    echo "  Setup Complete!"
    echo "========================================="
    echo ""
    echo "  CLI:       $CLI_NAME"
    echo "  Polarion:  $POLARION_URL"
    echo "  MCP:       $MCP_SERVER_NAME (launcher: $MCP_LAUNCHER)"
    echo "  Skills:    $(list_skills | paste -sd' ' - 2>/dev/null)"
    echo ""
    echo "  Reload your CLI session to load the MCP tools (mcp__${MCP_SERVER_NAME}__*)."
    echo ""
}

teardown_environment() {
    info "Removing Polarion workspace setup..."
    local skill
    while read -r skill; do
        [ -z "$skill" ] && continue
        uninstall_skill "$skill" 2>/dev/null || true
        uninstall_schedule "$skill" 2>/dev/null || true
    done < <(list_skills)
    remove_mcp_polarion 2>/dev/null || true
    ok "All removed."
}

# ============================================================
# Template helpers
# ============================================================
render_template() {
    local file="$1"
    local host="${POLARION_URL#*://}"
    sed \
        -e "s|{{POLARION_URL}}|${POLARION_URL}|g" \
        -e "s|{{POLARION_HOST}}|${host}|g" \
        "$file"
}

# ============================================================
# Date helpers
# ============================================================
next_friday() {
    python3 -c "
from datetime import date, timedelta
today = date.today()
days_ahead = 4 - today.weekday()
if days_ahead <= 0:
    days_ahead += 7
print((today + timedelta(days=days_ahead)).isoformat())
"
}

subtract_business_days() {
    local date_str="$1"
    local days="$2"
    python3 -c "
from datetime import datetime, timedelta
dt = datetime.strptime('$date_str', '%Y-%m-%d')
days = $days
while days > 0:
    dt -= timedelta(days=1)
    if dt.weekday() < 5:
        days -= 1
print(dt.strftime('%Y-%m-%d'))
"
}
