#!/usr/bin/env python3
"""PreCompact hook: export chat transcript before compaction.

Reads the Claude Code JSONL transcript and exports a formatted markdown
file to .claude/chat_history/ with:
  - Timestamps on every message
  - Collapsible sections for tool calls and results
  - Base64 / binary content filtered out
  - Subagent (Task) outputs preserved and shown expanded

Transcript format (Claude Code >=2.x):
  Each JSONL line is: {type, timestamp, message: {role, content}, uuid, ...}
  - type: "user" | "assistant" | "progress" | "system" | "queue-operation"
  - timestamp: ISO-8601 string (e.g. "2026-02-12T20:00:37.065Z")
  - message.role: "user" | "assistant"
  - message.content: string or list of content blocks
"""

import json
import sys
import os
import re
import glob as globmod
from datetime import datetime

# Base64: 100+ chars of base64 alphabet (catches embedded images, blobs)
_BASE64_RE = re.compile(
    r"(?:[A-Za-z0-9+/]{4}){25,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?"
)
# data:mime;base64,... URIs
_DATA_URI_RE = re.compile(r"data:[a-zA-Z0-9/+.\-]+;base64,[A-Za-z0-9+/=]+")
# ANSI escape sequences (colors, cursor, etc.)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07")
# <system-reminder>...</system-reminder> blocks (injected by Claude Code)
_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
# Compaction marker text
_COMPACTION_MARKER = "This session is being continued from a previous conversation"


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (colors, cursor moves, etc.)."""
    return _ANSI_RE.sub("", text)


def _filter_base64(text: str) -> str:
    """Replace base64 blobs and data URIs with size-annotated placeholders."""

    def _replace_data_uri(m: re.Match) -> str:
        raw = m.group(0)
        payload = raw.split(",", 1)[1] if "," in raw else ""
        mime = raw.split(";")[0].replace("data:", "")
        return f"[data URI filtered: {mime}, ~{len(payload) * 3 // 4} bytes]"

    def _replace_base64(m: re.Match) -> str:
        return f"[base64 data filtered, ~{len(m.group(0)) * 3 // 4} bytes]"

    text = _strip_ansi(text)
    text = _DATA_URI_RE.sub(_replace_data_uri, text)
    text = _BASE64_RE.sub(_replace_base64, text)
    return text


def _is_binary(text: str) -> bool:
    """Return True if text looks like binary (>10 % non-printable chars)."""
    if not text:
        return False
    sample = text[:2000]
    bad = sum(1 for c in sample if ord(c) < 32 and c not in "\n\r\t")
    return bad > len(sample) * 0.1


def _strip_system_reminders(text: str) -> str:
    """Replace <system-reminder>...</system-reminder> blocks with a short note."""
    return _SYSTEM_REMINDER_RE.sub("[system reminder collapsed]", text)


def _clean(text: str, limit: int = 3000) -> str:
    """Filter base64/binary/system-reminders and optionally truncate."""
    if _is_binary(text):
        return "*[binary content filtered]*"
    text = _filter_base64(text)
    text = _strip_system_reminders(text)
    if len(text) > limit:
        text = text[:limit] + f"\n\n... [{len(text) - limit} more chars truncated]"
    return text


# ---------------------------------------------------------------------------
# Timestamp formatting
# ---------------------------------------------------------------------------


def _format_ts(iso_str: str) -> str:
    """Convert ISO-8601 timestamp to a readable local-time string."""
    if not iso_str:
        return ""
    try:
        # Parse ISO-8601 (handles both Z and +00:00 suffix)
        ts = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return iso_str  # fall back to raw string


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------


def _text_from_content(content) -> str:
    """Pull plain text from a content field (string or block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, str):
                parts.append(blk)
            elif isinstance(blk, dict) and blk.get("type") == "text":
                parts.append(blk.get("text", ""))
        return "\n".join(parts)
    return str(content) if content else ""


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _tool_summary(name: str, inp: dict) -> str:
    """One-line summary for the <summary> tag."""
    if name == "Bash":
        desc = inp.get("description", "")
        cmd = inp.get("command", "")
        if desc:
            return f"Tool: Bash -- {desc}"
        short = cmd[:80] + ("..." if len(cmd) > 80 else "")
        return f"Tool: Bash -- `{short}`"
    if name == "Read":
        return f"Tool: Read -- `{os.path.basename(inp.get('file_path', ''))}`"
    if name in ("Write", "Edit"):
        return f"Tool: {name} -- `{os.path.basename(inp.get('file_path', ''))}`"
    if name == "Glob":
        return f"Tool: Glob -- `{inp.get('pattern', '')}`"
    if name == "Grep":
        return f"Tool: Grep -- `{inp.get('pattern', '')}`"
    if name == "Task":
        agent = inp.get("subagent_type", "unknown")
        desc = inp.get("description", "")
        return f"Subagent ({agent}): {desc}"
    if name == "WebFetch":
        url = inp.get("url", "")[:60]
        return f"Tool: WebFetch -- `{url}`"
    if name == "WebSearch":
        return f"Tool: WebSearch -- `{inp.get('query', '')}`"
    return f"Tool: {name}"


def _format_input(name: str, inp: dict) -> str:
    """Format tool input for display inside the details block."""
    if name == "Bash":
        cmd = inp.get("command", "")
        desc = inp.get("description", "")
        out = ""
        if desc:
            out += f"*{desc}*\n\n"
        out += f"```bash\n{cmd}\n```"
        return out
    if name == "Edit":
        fp = inp.get("file_path", "")
        old = inp.get("old_string", "")
        new = inp.get("new_string", "")
        return (
            f"**File:** `{fp}`\n\n"
            f"**Old:**\n```\n{old}\n```\n\n"
            f"**New:**\n```\n{new}\n```"
        )
    if name == "Write":
        fp = inp.get("file_path", "")
        body = inp.get("content", "")
        if len(body) > 500:
            body = body[:500] + f"\n... [{len(body) - 500} more chars]"
        return f"**File:** `{fp}`\n\n```\n{body}\n```"
    if name == "Task":
        agent = inp.get("subagent_type", "")
        desc = inp.get("description", "")
        prompt = inp.get("prompt", "")
        if len(prompt) > 1000:
            prompt = prompt[:1000] + f"\n... [{len(prompt) - 1000} more chars]"
        return f"**Agent:** `{agent}` | **Description:** {desc}\n\n{prompt}"
    # Fallback: compact JSON
    dumped = json.dumps(inp, indent=2)
    if len(dumped) > 500:
        dumped = dumped[:500] + "\n... [truncated]"
    return f"```json\n{dumped}\n```"


# ---------------------------------------------------------------------------
# Debug log parsing
# ---------------------------------------------------------------------------

# Matches a debug log line starting with ISO timestamp and [LEVEL]
_DEBUG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+\[(DEBUG|ERROR|WARN|INFO|TRACE)\]\s+(.*)"
)


def _session_time_range(entries: list[dict]) -> tuple[str, str]:
    """Extract the time range of a session from its JSONL entries.

    Returns (start_ts, end_ts) as ISO-8601 strings.
    Uses the timestamp of the first and last entry that has one.
    """
    start = ""
    end = ""
    for entry in entries:
        ts = entry.get("timestamp", "")
        if ts:
            if not start:
                start = ts
            end = ts
    return start, end


def _parse_debug_log(
    path: str,
    levels: set[str] | None = None,
    start_ts: str = "",
    end_ts: str = "",
) -> list[dict]:
    """Parse a Claude Code debug log file.

    Returns a list of dicts: {timestamp, level, text}.
    Only entries matching the given levels are included.
    Continuation lines (stack traces) are appended to the preceding entry.
    If levels is None, defaults to ERROR and WARN only.
    If start_ts/end_ts are provided, only entries within that time range
    are included (using lexicographic ISO-8601 comparison).
    """
    if levels is None:
        levels = {"ERROR", "WARN"}
    entries: list[dict] = []
    current: dict | None = None
    with open(path, "r") as fh:
        for line in fh:
            m = _DEBUG_LINE_RE.match(line)
            if m:
                # Flush previous entry
                if current is not None and current["level"] in levels:
                    ts = current["timestamp"]
                    if (not start_ts or ts >= start_ts) and (
                        not end_ts or ts <= end_ts
                    ):
                        entries.append(current)
                current = {
                    "timestamp": m.group(1),
                    "level": m.group(2),
                    "text": m.group(3),
                }
            elif current is not None:
                # Continuation line (stack trace, etc.)
                current["text"] += "\n" + line.rstrip()
    # Flush last entry
    if current is not None and current["level"] in levels:
        ts = current["timestamp"]
        if (not start_ts or ts >= start_ts) and (not end_ts or ts <= end_ts):
            entries.append(current)
    return entries


# ---------------------------------------------------------------------------
# JSONL reading
# ---------------------------------------------------------------------------


def _read_jsonl(path: str) -> list[dict]:
    """Read a JSONL file and return a list of parsed dicts."""
    entries: list[dict] = []
    with open(path, "r") as fh:
        for line in fh:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _extract_messages(
    entries: list[dict], session_id: str
) -> tuple[list[dict], list[dict]]:
    """Extract user/assistant messages from JSONL entries.

    Returns (main_messages, sidechain_messages).
    Filters to the given session_id.
    """
    messages: list[dict] = []
    sidechain_msgs: list[dict] = []
    for entry in entries:
        entry_type = entry.get("type", "")
        if entry_type not in ("user", "assistant"):
            continue
        if entry.get("sessionId") and entry["sessionId"] != session_id:
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        parsed = {
            "role": msg.get("role", entry_type),
            "content": msg.get("content", ""),
            "timestamp": entry.get("timestamp", ""),
        }
        if entry.get("isSidechain"):
            sidechain_msgs.append(parsed)
        else:
            messages.append(parsed)
    return messages, sidechain_msgs


def _extract_messages_unfiltered(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Extract user/assistant messages without session filtering.

    Used for subagent transcripts where sessionId may not match.
    """
    messages: list[dict] = []
    sidechain_msgs: list[dict] = []
    for entry in entries:
        entry_type = entry.get("type", "")
        if entry_type not in ("user", "assistant"):
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        parsed = {
            "role": msg.get("role", entry_type),
            "content": msg.get("content", ""),
            "timestamp": entry.get("timestamp", ""),
        }
        if entry.get("isSidechain"):
            sidechain_msgs.append(parsed)
        else:
            messages.append(parsed)
    return messages, sidechain_msgs


def _build_agent_info(entries: list[dict]) -> dict[str, dict[str, str]]:
    """Build a map from agentId to Task metadata (subagent_type, description).

    Scans assistant messages for Task tool_use blocks, then matches them
    to agentIds via the parentToolUseID field on progress entries that
    carry data.agentId.
    """
    # Step 1: collect Task tool_use_ids and their metadata
    task_meta: dict[str, dict[str, str]] = {}  # tool_use_id -> {type, desc}
    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", []) if isinstance(msg, dict) else []
        if not isinstance(content, list):
            continue
        for blk in content:
            if (
                isinstance(blk, dict)
                and blk.get("type") == "tool_use"
                and blk.get("name") == "Task"
            ):
                inp = blk.get("input", {})
                task_meta[blk.get("id", "")] = {
                    "subagent_type": inp.get("subagent_type", "unknown"),
                    "description": inp.get("description", ""),
                }

    # Step 2: match agentId to Task via parentToolUseID on progress entries.
    # In the main transcript, progress entries have:
    #   data.agentId = "<agent_id>"
    #   parentToolUseID = "<task_tool_use_id>"
    agent_info: dict[str, dict[str, str]] = {}
    for entry in entries:
        data = entry.get("data")
        if not isinstance(data, dict):
            continue
        agent_id = data.get("agentId", "")
        if not agent_id or agent_id in agent_info:
            continue
        parent_tuid = entry.get("parentToolUseID", "")
        if parent_tuid in task_meta:
            agent_info[agent_id] = task_meta[parent_tuid]

    return agent_info


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _merge_debug_entries(messages: list[dict], debug_entries: list[dict]) -> list[dict]:
    """Merge debug log entries into the message list, sorted by timestamp.

    Debug entries are converted to synthetic message dicts with
    role="debug" so the renderer can format them differently.
    """
    if not debug_entries:
        return messages
    synthetic = []
    for de in debug_entries:
        synthetic.append(
            {
                "role": "debug",
                "content": de["text"],
                "timestamp": de["timestamp"],
                "level": de["level"],
            }
        )
    merged = messages + synthetic
    # Sort by ISO timestamp string (both formats are ISO-8601, lexicographic sort works)
    merged.sort(key=lambda m: m.get("timestamp", ""))
    return merged


def _render_messages(messages: list[dict], out: list[str]) -> None:
    """Render a list of parsed messages into markdown lines.

    Each message dict has: role, content, timestamp.
    content is either a string or a list of content blocks.
    Debug entries have role="debug" and a "level" field.
    """
    # -- Build a lookup of tool results keyed by tool_use_id ---------------
    # User messages may contain tool_result blocks that pair with
    # tool_use blocks in the preceding assistant message.
    result_map: dict[str, str] = {}
    for msg in messages:
        if msg["role"] != "user":
            continue
        content = msg["content"]
        if not isinstance(content, list):
            continue
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "tool_result":
                tid = blk.get("tool_use_id", "")
                if not tid:
                    continue
                # Extract text from the tool_result content
                rc = blk.get("content", "")
                if isinstance(rc, list):
                    parts = []
                    for rb in rc:
                        if isinstance(rb, dict) and rb.get("type") == "text":
                            parts.append(rb.get("text", ""))
                        elif isinstance(rb, str):
                            parts.append(rb)
                    rc = "\n".join(parts)
                result_map[tid] = str(rc) if rc else ""

    # -- Render each message -----------------------------------------------
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        ts = _format_ts(msg.get("timestamp", ""))
        ts_label = f"  *[{ts}]*" if ts else ""

        if role == "user":
            # Skip messages that are purely tool_result blocks (no human text)
            if isinstance(content, list):
                has_text = any(
                    (isinstance(b, str) and b.strip())
                    or (
                        isinstance(b, dict)
                        and b.get("type") == "text"
                        and b.get("text", "").strip()
                    )
                    for b in content
                )
                if not has_text:
                    continue

            out.append(f"## USER{ts_label}\n")
            text = _text_from_content(content)
            if text.strip():
                out.append(_clean(text) + "\n")

        elif role == "assistant":
            out.append(f"## ASSISTANT{ts_label}\n")

            if isinstance(content, str):
                out.append(_clean(content) + "\n")
                continue

            if not isinstance(content, list):
                out.append(str(content) + "\n")
                continue

            for blk in content:
                if isinstance(blk, str):
                    if blk.strip():
                        out.append(_clean(blk) + "\n")
                    continue

                if not isinstance(blk, dict):
                    continue

                btype = blk.get("type", "")

                if btype == "text":
                    txt = blk.get("text", "")
                    if txt.strip():
                        out.append(_clean(txt) + "\n")

                elif btype == "tool_use":
                    tool_name = blk.get("name", "unknown")
                    tool_input = blk.get("input", {})
                    tool_id = blk.get("id", "")
                    is_task = tool_name == "Task"

                    summary = _tool_summary(tool_name, tool_input)
                    formatted_input = _format_input(tool_name, tool_input)
                    result_text = result_map.get(tool_id, "")

                    # Subagent (Task) calls: expanded by default
                    # Other tools: collapsed
                    if is_task:
                        out.append("<details open>")
                    else:
                        out.append("<details>")
                    out.append(f"<summary>{summary}</summary>\n")

                    # Tool input
                    out.append("**Input:**\n")
                    out.append(_clean(formatted_input, limit=2000) + "\n")

                    # Tool result (if we have it)
                    if result_text:
                        out.append("**Result:**\n")
                        out.append(_clean(result_text, limit=2000) + "\n")

                    out.append("</details>\n")

        elif role == "debug":
            level = msg.get("level", "DEBUG")
            badge = "ERROR" if level == "ERROR" else level
            out.append("<details>")
            out.append(
                f"<summary><strong>[{badge}]</strong>{ts_label} "
                f"{content.split(chr(10))[0][:120]}</summary>\n"
            )
            out.append(f"```\n{_clean(content, limit=2000)}\n```\n")
            out.append("</details>\n")

        out.append("")  # blank line between messages


# ---------------------------------------------------------------------------
# Compaction boundary detection
# ---------------------------------------------------------------------------


def _find_last_compaction_index(entries: list[dict]) -> int:
    """Find the index of the last compaction marker in the transcript.

    Compaction markers are user messages containing the text:
    "This session is being continued from a previous conversation"

    Returns the index of the last such entry, or 0 if no compaction
    has occurred (export the full transcript).
    """
    last_idx = 0
    for i, entry in enumerate(entries):
        if entry.get("type") != "user":
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", "")
        text = _text_from_content(content)
        if _COMPACTION_MARKER in text:
            last_idx = i
    return last_idx


def _filter_agent_files_by_time(agent_files: list[str], start_ts: str) -> list[str]:
    """Keep only subagent transcript files whose entries fall within the
    current session segment (after the last compaction).

    Checks the first entry's timestamp in each agent JSONL file.
    """
    if not start_ts:
        return agent_files
    filtered: list[str] = []
    for path in agent_files:
        try:
            with open(path, "r") as fh:
                for line in fh:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = entry.get("timestamp", "")
                    if ts:
                        # Keep agent if its first timestamp is within the
                        # current session segment
                        if ts >= start_ts:
                            filtered.append(path)
                        break
        except OSError:
            continue
    return filtered


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    hook_input = json.load(sys.stdin)

    transcript_path = hook_input.get("transcript_path")
    session_id = hook_input.get("session_id", "unknown")

    if transcript_path:
        transcript_path = os.path.expanduser(transcript_path)

    if not transcript_path or not os.path.exists(transcript_path):
        print("No transcript found", file=sys.stderr)
        sys.exit(1)

    # -- Read main transcript ---------------------------------------------
    all_entries = _read_jsonl(transcript_path)
    if not all_entries:
        print("Empty transcript", file=sys.stderr)
        sys.exit(1)

    # -- Slice to current session segment (after last compaction) ---------
    # The JSONL accumulates entries across compactions. Only export the
    # segment since the most recent compaction event.
    compact_idx = _find_last_compaction_index(all_entries)
    entries = all_entries[compact_idx:]

    # -- Extract messages from current segment ----------------------------
    messages, sidechain_msgs = _extract_messages(entries, session_id)

    if not messages and not sidechain_msgs:
        print("No messages in transcript", file=sys.stderr)
        sys.exit(1)

    # -- Build agent metadata from full transcript ------------------------
    # Need full transcript to map agentIds to Task metadata (agent may
    # have been spawned in an earlier turn within the current segment).
    agent_info = _build_agent_info(entries)

    # -- Discover subagent transcript files -------------------------------
    # Subagent transcripts live in <session_id>/subagents/agent-*.jsonl
    # next to the main transcript file.
    transcript_dir = os.path.dirname(transcript_path)
    subagents_dir = os.path.join(transcript_dir, session_id, "subagents")
    agent_files: list[str] = []
    if os.path.isdir(subagents_dir):
        agent_files = sorted(globmod.glob(os.path.join(subagents_dir, "agent-*.jsonl")))

    # -- Filter agent files to current session segment --------------------
    # Only include subagent transcripts spawned after the last compaction.
    session_start, session_end = _session_time_range(entries)
    if agent_files and session_start:
        agent_files = _filter_agent_files_by_time(agent_files, session_start)

    # -- Load debug log if present -----------------------------------------
    # Debug logs live at ~/.claude/debug/<session_id>.txt
    # Only include entries within the current session segment's time range.
    debug_dir = os.path.join(os.path.expanduser("~"), ".claude", "debug")
    debug_path = os.path.join(debug_dir, f"{session_id}.txt")
    debug_entries: list[dict] = []
    if os.path.isfile(debug_path):
        debug_entries = _parse_debug_log(
            debug_path, start_ts=session_start, end_ts=session_end
        )

    # Merge debug entries (ERROR/WARN) into main messages by timestamp
    merged_messages = _merge_debug_entries(messages, debug_entries)

    # -- Render markdown --------------------------------------------------
    out: list[str] = []
    out.append("# Claude Code Session Export\n")
    out.append(f"- **Session ID:** `{session_id}`")
    out.append(f"- **Exported:** {datetime.now().isoformat()}")
    out.append(f"- **Transcript:** `{transcript_path}`")
    if compact_idx > 0:
        out.append(
            "- **Note:** prior compactions detected; exporting only current segment"
        )
    if session_start:
        out.append(f"- **Segment start:** {_format_ts(session_start)}")
    if agent_files:
        out.append(f"- **Subagent transcripts:** {len(agent_files)}")
    if debug_entries:
        out.append(f"- **Debug log entries (ERROR/WARN):** {len(debug_entries)}")
    out.append("")
    out.append("---\n")

    # Main conversation (with interleaved debug entries)
    _render_messages(merged_messages, out)

    # Sidechains (abandoned branches) in a collapsed section
    if sidechain_msgs:
        out.append("")
        out.append("<details>")
        out.append(
            "<summary><strong>Sidechain messages "
            f"(abandoned branches) -- {len(sidechain_msgs)} "
            "entries</strong></summary>\n"
        )
        _render_messages(sidechain_msgs, out)
        out.append("</details>\n")

    # Subagent transcripts — each in its own collapsed section
    if agent_files:
        out.append("")
        out.append("---\n")
        out.append(f"# Subagent Transcripts ({len(agent_files)})\n")

        for agent_path in agent_files:
            # Extract agentId from filename: agent-<id>.jsonl
            fname = os.path.basename(agent_path)
            agent_id = fname.replace("agent-", "").replace(".jsonl", "")

            # Get metadata from the main transcript's Task tool calls
            info = agent_info.get(agent_id, {})
            agent_type = info.get("subagent_type", "unknown")
            agent_desc = info.get("description", "")

            # Read and parse agent transcript
            agent_entries = _read_jsonl(agent_path)
            # Agent entries share the parent sessionId; pass it through
            agent_msgs, agent_side = _extract_messages(agent_entries, session_id)
            # If session filter yields nothing, try without filter
            # (some agent entries may not have sessionId set)
            if not agent_msgs and not agent_side:
                agent_msgs, agent_side = _extract_messages_unfiltered(agent_entries)

            total = len(agent_msgs) + len(agent_side)
            if total == 0:
                continue

            # Build section label
            label = f"Agent `{agent_id}` ({agent_type})"
            if agent_desc:
                label += f" -- {agent_desc}"
            label += f" [{total} messages]"

            out.append("<details>")
            out.append(f"<summary><strong>{label}</strong></summary>\n")
            _render_messages(agent_msgs + agent_side, out)
            out.append("</details>\n")

    # -- Write export file ------------------------------------------------
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    export_dir = os.path.join(os.getcwd(), ".claude", "chat_history")
    os.makedirs(export_dir, exist_ok=True)

    output_path = os.path.join(export_dir, f"export-{timestamp}.md")
    with open(output_path, "w") as fh:
        fh.write("\n".join(out))

    print(f"Exported to {output_path}")


if __name__ == "__main__":
    main()
