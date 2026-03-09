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
import traceback
from datetime import datetime, timezone

# Allow importing ccx package when running as a standalone hook script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _log_error(project_dir: str, source: str, event: str | None,
               session_id: str | None, exc: Exception) -> None:
    """Append a hook error record to .ccx/logs/hook_errors.jsonl.

    Never raises — error logging must not break the hook.
    """
    try:
        log_dir = os.path.join(project_dir, ".ccx", "logs")
        os.makedirs(log_dir, exist_ok=True)

        record = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": source,
            "event_type": event,
            "session_id": session_id,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback": traceback.format_exception(exc),
        }

        log_path = os.path.join(log_dir, "hook_errors.jsonl")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _track_tokens(event: str, data: dict, project_dir: str, session_id: str,
                  transcript_path: str | None) -> None:
    """Aggregate token usage from transcript on Stop/SubagentStop events."""
    try:
        from ccx.token_tracker import parse_transcript, save_agent_usage

        if event == "Stop" and transcript_path:
            agent_usage = parse_transcript(transcript_path)
            save_agent_usage(project_dir, session_id, agent_usage)

        elif event == "SubagentStop":
            agent_transcript = data.get("agent_transcript_path")
            if agent_transcript:
                agent_usage = parse_transcript(agent_transcript)
                save_agent_usage(project_dir, session_id, agent_usage)
    except Exception as e:
        _log_error(project_dir, "_track_tokens", event, session_id, e)


def _track_context(event: str, data: dict, project_dir: str, session_id: str,
                   transcript_path: str | None) -> None:
    """Track context window usage from transcript on Stop/SubagentStop events."""
    try:
        from ccx.context_tracker import parse_context_usage, save_context_usage

        if event == "Stop" and transcript_path:
            context_usage = parse_context_usage(transcript_path)
            save_context_usage(project_dir, session_id, context_usage)

        elif event == "SubagentStop":
            agent_transcript = data.get("agent_transcript_path")
            if agent_transcript:
                context_usage = parse_context_usage(agent_transcript)
                save_context_usage(project_dir, session_id, context_usage)
    except Exception as e:
        _log_error(project_dir, "_track_context", event, session_id, e)


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

    # Capture transcript path before stripping
    transcript_path = data.get("transcript_path")

    # Strip fields that should not persist in the log
    cwd = data.pop("cwd", ".")
    data.pop("transcript_path", None)

    # Determine log directory
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    log_dir = os.path.join(project_dir, ".ccx", "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, f"{session_id}.jsonl")
    line = json.dumps(data, ensure_ascii=False)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

    # Track token usage on session/subagent completion
    _track_tokens(event, data, project_dir, session_id, transcript_path)

    # Track context window usage on session/subagent completion
    _track_context(event, data, project_dir, session_id, transcript_path)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
        _log_error(project_dir, "main", None, None, e)
    sys.exit(0)
