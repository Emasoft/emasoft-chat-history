#!/usr/bin/env bash
# PreCompact hook: export chat transcript before compaction.
# Uses Claude Code's native /export command to save the conversation
# to .claude/chat_history/ with an auto-incrementing index prefix and datestamp.

set -euo pipefail

# Determine output directory (project-local .claude/chat_history/)
CHAT_DIR=".claude/chat_history"
mkdir -p "$CHAT_DIR"

# Compute next 3-digit index from existing files
# shellcheck disable=SC2012
LAST_INDEX=$(ls -1 "$CHAT_DIR"/*.md 2>/dev/null | sed 's|.*/||' | grep -oE '^[0-9]+' | sort -n | tail -1 || echo "0")
NEXT_INDEX=$(printf "%03d" $(( ${LAST_INDEX:-0} + 1 )))

# Build filename with index prefix and datestamp suffix
DATESTAMP=$(date +%Y%m%d-%H%M%S)
FILENAME="${NEXT_INDEX}-chat-${DATESTAMP}.md"
EXPORT_PATH="${CHAT_DIR}/${FILENAME}"

# Use /export to save the transcript
/export "$EXPORT_PATH"
