#!/usr/bin/env python3
"""
Claude Code hook handler: log structured events to JSONL.

Reads JSON from stdin, extracts fields per event type, and appends
a single JSONL line to .ccx/logs/{session_id}.jsonl.

Always exits 0 to never block Claude Code.
"""

import json
import os
import sys
from datetime import datetime, timezone


def _truncate(value, limit):
    """Convert value to string (json.dumps if not str) and truncate."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    if len(value) > limit:
        return value[:limit] + "...[truncated]"
    return value


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_record(data):
    event = data.get("hook_event_name")
    session_id = data.get("session_id")

    if not event or not session_id:
        return None, None

    ts = _now_iso()
    base = {"timestamp": ts, "session_id": session_id, "event": event}

    if event == "PreToolUse":
        base["tool_name"] = data.get("tool_name")
        base["tool_input"] = _truncate(data.get("tool_input"), 2000)

    elif event == "PostToolUse":
        base["tool_name"] = data.get("tool_name")
        base["tool_input"] = _truncate(data.get("tool_input"), 2000)
        base["tool_response"] = _truncate(data.get("tool_response"), 2000)
        base["duration_ms"] = data.get("duration_ms")

    elif event == "PostToolUseFailure":
        base["tool_name"] = data.get("tool_name")
        base["tool_input"] = _truncate(data.get("tool_input"), 2000)
        base["error"] = data.get("error")
        base["duration_ms"] = data.get("duration_ms")

    elif event == "UserPromptSubmit":
        base["prompt"] = data.get("prompt")

    elif event == "SubagentStart":
        if "subagent_id" in data:
            base["subagent_id"] = data["subagent_id"]
        if "subagent_type" in data:
            base["subagent_type"] = data["subagent_type"]
        base["prompt"] = _truncate(data.get("prompt"), 4000)

    elif event == "SubagentStop":
        if "subagent_id" in data:
            base["subagent_id"] = data["subagent_id"]
        if "subagent_type" in data:
            base["subagent_type"] = data["subagent_type"]
        base["response"] = _truncate(data.get("response"), 4000)
        if "duration_ms" in data:
            base["duration_ms"] = data["duration_ms"]

    elif event == "Stop":
        if "stop_reason" in data:
            base["stop_reason"] = data["stop_reason"]

    else:
        # Unknown event: dump all other keys
        skip = {"hook_event_name", "session_id"}
        extra = {k: v for k, v in data.items() if k not in skip}
        extra_str = _truncate(extra, 2000)
        if extra_str:
            base["extra"] = extra_str

    return session_id, base


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return

    data = json.loads(raw)

    session_id, record = _build_record(data)
    if record is None:
        return

    # Determine log directory
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd", ".")
    log_dir = os.path.join(project_dir, ".ccx", "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, f"{session_id}.jsonl")
    line = json.dumps(record, ensure_ascii=False)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
