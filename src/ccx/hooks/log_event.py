#!/usr/bin/env python3
"""
Claude Code hook handler: log all events to JSONL.

Reads JSON from stdin, adds a timestamp, and appends the full payload
as a single JSONL line to .ccx/logs/{session_id}.jsonl.

Always exits 0 to never block Claude Code.
"""

import json
import os
import sys
from datetime import datetime, timezone


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return

    data = json.loads(raw)

    session_id = data.get("session_id")
    event = data.get("hook_event_name")
    if not session_id or not event:
        return

    # Add timestamp, keep all original fields
    data["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Determine log directory
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd", ".")
    log_dir = os.path.join(project_dir, ".ccx", "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, f"{session_id}.jsonl")
    line = json.dumps(data, ensure_ascii=False)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
