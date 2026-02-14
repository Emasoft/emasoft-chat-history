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

## Installation

```bash
claude --plugin-dir /path/to/emasoft-chat-history
```

Or install from the Emasoft marketplace.

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
