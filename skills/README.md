# Skills

Each skill is an AI-powered automation unit with its own rules, slash command, and optional schedule.

## Structure

```
skills/<skill-name>/
├── install.sh              # Slash command installer (--remove to uninstall)
├── <skill-name>.md         # Full skill document (used for interactive /slash-command)
├── requirements.md         # Requirements documentation for this skill
├── rules/                  # Per-rule prompts (used for multi-pass scheduled execution)
│   └── rule<N>-<name>.md
└── schedule/               # Optional cron automation
    ├── install.sh          # Cron installer (--remove to uninstall)
    ├── run.sh              # Entry point for cron or manual execution
    └── logs/               # Execution logs (auto-rotated, last 30 kept)
```

## How It Works

- **Interactive**: `/<skill-name>` slash command runs `<skill-name>.md` as a single prompt
- **Scheduled**: `run.sh` executes each `rules/rule*.md` as a separate AI session (multi-pass)
- **Template variables**: `{{POLARION_URL}}`, `{{POLARION_HOST}}` are replaced at runtime from environment variables

## Adding a New Skill

1. Create `skills/<skill-name>/` directory
2. Write `requirements.md` defining what the skill should do
3. Write `<skill-name>.md` with full rule definitions
4. Optionally split into `rules/rule<N>-<name>.md` for scheduled multi-pass execution
5. Copy `install.sh` from an existing skill (only the skill name changes)
6. Optionally add `schedule/` with `install.sh` and `run.sh`
7. Run `./install.sh` to register the slash command

## Polarion MCP Tools

Skills drive Polarion through the `mcp__polarion__*` tools, including:

- **Read**: `list_projects`, `list_documents`, `get_document`, `read_document`, `read_document_parts`, `search_workitems_in_document`
- **Write**: `update_document`, and work-item create/update/move operations
