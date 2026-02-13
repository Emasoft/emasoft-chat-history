# Chat History Export Plugin

Automatically exports the full chat transcript before every compaction event, preserving conversation history that would otherwise be summarized.

## How It Works

A `PreCompact` hook fires before every compaction (manual via `/compact` or automatic when the context window fills). The hook calls Claude Code's native `/export` command inline to save the complete conversation transcript to a local `.claude/chat_history/` directory.

No wrapper script is needed. The command runs directly in hooks.json:

```json
"command": "mkdir -p .claude/chat_history && /export .claude/chat_history/chat-$(date +%Y%m%d-%H%M%S).md"
```

## Output

Files are saved as:

```
.claude/chat_history/chat-20260212-193045.md
.claude/chat_history/chat-20260213-104512.md
.claude/chat_history/chat-20260213-163201.md
```

- **Datestamp** (`YYYYMMDD-HHMMSS`) - human-readable timestamp, provides chronological ordering and uniqueness

## Installation

```bash
claude --plugin-dir /path/to/chat-history-export
```

Or add to a marketplace for persistent installation.

## Advanced: Wrapper Script

The `scripts/pre_compact_export.sh` script provides an enhanced version with auto-incrementing 3-digit index prefixes (`001-chat-...`, `002-chat-...`). To use it, edit `hooks/hooks.json` and replace the inline command with:

```json
"command": "${CLAUDE_PLUGIN_ROOT}/scripts/pre_compact_export.sh"
```

## Requirements

- Claude Code with hook support
- The `/export` command must be available
