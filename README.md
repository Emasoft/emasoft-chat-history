# Emasoft Chat History Plugin

Automatically exports the full chat transcript before every compaction event, preserving conversation history that would otherwise be summarized and lost.

## Features

The export captures four layers of conversation data:

1. **Main conversation** -- User and assistant messages with timestamps, collapsible tool call/result sections, and expanded subagent (Task) outputs. Base64 blobs and binary content are filtered out.
2. **Sidechains** -- Abandoned conversation branches, collected in a single collapsed section.
3. **Subagent transcripts** -- Each spawned agent's full transcript in its own collapsed section, labeled with the agent type and description from the parent Task call.
4. **Debug log entries** -- ERROR and WARN entries from `~/.claude/debug/<session_id>.txt` (if debug mode was active), interleaved into the main conversation by timestamp.

## How It Works

A `PreCompact` hook fires before every compaction (manual via `/compact` or automatic when the context window fills). The hook runs a Python script that:

1. Reads the JSONL transcript from the path provided by Claude Code
2. Extracts and pairs tool calls with their results
3. Discovers subagent transcript files in `<session_id>/subagents/`
4. Loads debug log entries filtered to the session's time range
5. Renders everything as a structured markdown file with collapsible `<details>` sections

The export is saved to `.claude/chat_history/export-YYYYMMDD-HHMMSS.md` in the current working directory.

## Hook Configuration

The hook is defined in `hooks/hooks.json`:

```json
{
  "hooks": {
    "PreCompact": [
      {
        "matcher": "auto|manual",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/pre_compact_export.py"
          }
        ]
      }
    ]
  }
}
```

The script receives hook input via stdin (JSON with `session_id`, `transcript_path`, etc.) and writes the export file.

## Output Format

Files are saved as:

```
.claude/chat_history/export-20260212-193045.md
.claude/chat_history/export-20260213-104512.md
```

Each export contains:

- Header with session ID, export timestamp, transcript path, subagent count, and debug entry count
- Main conversation with `## USER` / `## ASSISTANT` headings and timestamps
- Tool calls in collapsible `<details>` sections (subagent Tasks are expanded by default)
- Sidechain messages in a single collapsed section
- Each subagent transcript in its own collapsed section
- Debug ERROR/WARN entries as collapsed sections with level badges

## Installation (Production)

Install from the Emasoft marketplace. Use `--scope user` to install for all Claude Code instances, or `--scope global` for all projects.

```bash
# Add Emasoft marketplace (first time only)
claude plugin marketplace add emasoft-plugins --url https://github.com/Emasoft/emasoft-plugins

# Install plugin (--scope user = all Claude Code instances, recommended for utility plugins)
claude plugin install emasoft-chat-history@emasoft-plugins --scope user

# RESTART Claude Code after installing (required!)
```

Utility plugins are installed once with `--scope user` and become available to all Claude Code instances.

This is a utility plugin — it provides pre-compaction chat export hooks. No `--agent` flag needed; just start Claude Code normally and chat history will be automatically exported before context compaction.

## Development Only (--plugin-dir)

`--plugin-dir` loads a plugin directly from a local directory without marketplace installation. Use only during plugin development.

```bash
claude --plugin-dir ./OUTPUT_SKILLS/emasoft-chat-history
```

## Requirements

- Claude Code with hook support (v2.x+)
- Python 3.10+ (uses `match`-free syntax, compatible with 3.10+)
- No external Python dependencies (stdlib only)

## Directory Structure

```
emasoft-chat-history/
  .claude-plugin/
    plugin.json          # Plugin manifest
  hooks/
    hooks.json           # PreCompact hook configuration
  scripts/
    pre_compact_export.py  # Main export script (674 lines)
  LICENSE
  README.md
```
