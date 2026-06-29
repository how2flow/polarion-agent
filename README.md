# Polarion Automation Workspace

AI-powered Siemens Polarion automation — read, write, and review Polarion
documents and work items. Works with Claude Code, OpenAI Codex CLI, and Google
Gemini CLI. Modeled on the `jira-cli` workspace.

Unlike Jira (which has a hosted OAuth connector), Polarion has no remote
connector, so this workspace registers a **local MCP server**
([`mcp-server-polarion`](https://pypi.org/project/mcp-server-polarion/), run via
`uvx`) that talks to the Polarion REST API.

## Quick Start

```bash
# 1. Set environment variable (instance ROOT, no /polarion path)
export POLARION_URL="https://your-polarion-host"

# 2. Run setup (interactive — prompts for a Polarion Personal Access Token)
./envsetup.sh

# 3. Reload your CLI session so the mcp__polarion__* tools load

# 4. Use a slash command in the CLI session
/<skill-name>
```

## Directory Structure

```
.
├── envsetup.sh                         # One-stop setup (MCP, skills, cron)
├── scripts/
│   ├── params.sh                       # CLI detection, variables
│   ├── functions.sh                    # All shared functions
│   └── mcp-polarion.sh                 # Shared MCP launcher (reads .env, runs the server)
├── skills/
│   └── <skill-name>/
│       ├── install.sh                  # Slash command installer
│       ├── <skill-name>.md             # Skill definition (interactive use)
│       ├── requirements.md             # Requirements documentation
│       ├── rules/                      # Per-rule prompts (multi-pass execution)
│       │   └── rule<N>-<name>.md
│       └── schedule/                   # Cron automation (optional)
│           ├── install.sh
│           ├── run.sh
│           └── logs/
└── projects/                           # Multi-skill composite workflows
    └── <project-name>/
        └── schedule/
```

## Supported CLIs

| CLI | Global Config | Where the server is registered | Registration mechanism |
|-----|--------------|--------------------------------|------------------------|
| Claude Code | `~/.claude/settings.json` | `~/.claude.json` (local scope) | `claude mcp add` |
| Codex CLI | `~/.codex/config.toml` | `.codex/config.toml` (`mcp_servers`) | config file |
| Gemini CLI | `~/.gemini/settings.json` | `.gemini/settings.json` (`mcpServers`) | config file |

Every CLI registers the **same** thing — the shared launcher
`scripts/mcp-polarion.sh` — with **no credentials in the CLI config**.

## MCP Server

Polarion has no hosted connector, so polarion-agent runs a local stdio MCP server,
[`mcp-server-polarion`](https://pypi.org/project/mcp-server-polarion/) (requires
`uv`/`uvx` and Python ≥3.12).

All three CLIs launch one shared wrapper, `scripts/mcp-polarion.sh`, which reads
the credentials from `.env` (or your exported environment) and then execs
`uvx mcp-server-polarion`:

| Var | Description | Example |
|-----|-------------|---------|
| `POLARION_URL` | Instance ROOT URL, **without** `/polarion` (the server appends `/polarion/rest/v1`) | `https://your-polarion-host` |
| `POLARION_TOKEN` | Polarion Personal Access Token (Bearer) | (in `.env`, gitignored) |

Because only the launcher is registered, the token never enters any CLI config
(`~/.claude.json`, `.codex/config.toml`, `.gemini/settings.json`) and is never
committed. The single source of truth is `.env` (gitignored) or an exported
`POLARION_TOKEN`.

## Environment Variables

Set via `.env` file or export in your shell profile (`~/.bashrc`, `~/.zshrc`).

| Variable | Description | Example |
|----------|-------------|---------|
| `POLARION_URL` | Polarion instance root URL (no `/polarion`) | `https://your-polarion-host` |
| `POLARION_TOKEN` | Polarion Personal Access Token (secret) | (entered at setup) |
| `POLARION_CLI_OVERRIDE` | Force specific CLI (optional) | `claude`, `codex`, `gemini` |
| `POLARION_DEBUG` | Enable debug output (optional) | `1` |

> `POLARION_TOKEN` is a secret. `./envsetup.sh` prompts for it (silent input)
> and writes it to `.env` (gitignored, `chmod 600`). Alternatively, leave it out
> of `.env` and `export POLARION_TOKEN=...` in your shell — `scripts/mcp-polarion.sh`
> reads it from either source at runtime. It is never committed and never written
> into any CLI's MCP config.

## Installation

```bash
# Full setup (interactive)
./envsetup.sh

# Individual skill
./skills/<skill-name>/install.sh
./skills/<skill-name>/schedule/install.sh
./skills/<skill-name>/schedule/install.sh "0 7 * * 1-5"  # Custom cron

# Remove
./envsetup.sh --remove
./skills/<skill-name>/install.sh --remove
./skills/<skill-name>/schedule/install.sh --remove
```
