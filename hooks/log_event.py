#!/usr/bin/env python3
"""
Claude Code hook handler: log all events to JSONL.

Reads JSON from stdin, adds a timestamp, and appends the full payload
as a single JSONL line to .ccx/logs/{session_id}.jsonl.

On PostToolUse events, periodically checks context fill level and returns
a block decision when the context window exceeds 50 %.

Always exits 0 to never block Claude Code.
"""

import json
import os
import sys
import traceback
from datetime import datetime, timezone

# Allow importing ccx package when running as a standalone hook script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ccx.storage import resolve_storage_dir

# Throttle: only check context fill every N PostToolUse events
_MONITOR_CHECK_INTERVAL = 10


def _log_error(project_dir: str, source: str, event: str | None,
               session_id: str | None, exc: Exception) -> None:
    """Append a hook error record to .ccx/logs/hook_errors.jsonl.

    Never raises — error logging must not break the hook.
    """
    try:
        storage_dir = resolve_storage_dir(project_dir)
        log_dir = os.path.join(storage_dir, ".ccx", "logs")
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


def _replace_subagent_stop(log_path: str, agent_id: str, new_line: str) -> bool:
    """Replace an existing SubagentStop line for *agent_id* in the log file.

    When validate_schema.py blocks a SubagentStop, Claude Code retries the
    agent, producing a second SubagentStop for the same agent_id.  Without
    deduplication the log would contain 1 start and 2 stops.

    This function scans the JSONL file for an existing SubagentStop entry
    with a matching ``agent_id`` and replaces it in-place with *new_line*.
    Returns ``True`` if a replacement was made, ``False`` otherwise (caller
    should append normally).
    """
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return False

    replaced = False
    for i, existing_line in enumerate(lines):
        try:
            entry = json.loads(existing_line)
        except (json.JSONDecodeError, ValueError):
            continue
        if (entry.get("hook_event_name") == "SubagentStop"
                and entry.get("agent_id") == agent_id):
            lines[i] = new_line + "\n"
            replaced = True
            break

    if replaced:
        with open(log_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    return replaced


def _monitor_state_path(project_dir: str) -> str:
    """Return the path to .ccx/context-monitor-state.json."""
    return os.path.join(resolve_storage_dir(project_dir), ".ccx", "context-monitor-state.json")


def _load_monitor_state(project_dir: str, session_id: str) -> dict:
    """Load the context monitor state for a session.

    Returns a dict with ``call_count`` and ``already_warned`` fields.
    If the state file does not exist or the session_id differs, returns
    a fresh state.
    """
    path = _monitor_state_path(project_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.loads(f.read())
        if state.get("session_id") != session_id:
            return {"session_id": session_id, "call_count": 0,
                    "already_warned": False}
        return state
    except (OSError, json.JSONDecodeError, KeyError):
        return {"session_id": session_id, "call_count": 0,
                "already_warned": False}


def _save_monitor_state(state: dict, project_dir: str) -> None:
    """Persist the context monitor state to disk."""
    ccx_dir = os.path.join(resolve_storage_dir(project_dir), ".ccx")
    os.makedirs(ccx_dir, exist_ok=True)
    path = _monitor_state_path(project_dir)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(state, ensure_ascii=False))


def _monitor_context(project_dir: str, session_id: str,
                     transcript_path: str) -> dict | None:
    """Check context fill on every Nth PostToolUse event.

    Uses a counter-based throttle so that the (relatively expensive)
    transcript parse only runs every ``_MONITOR_CHECK_INTERVAL`` calls.
    Once a warning has been issued for a session it is not repeated.

    Returns a ``{"decision": "block", "reason": "..."}`` dict when the
    context window fill exceeds 50 %, or ``None`` otherwise.
    """
    if not transcript_path:
        return None

    # 1. Throttle: bump counter, skip unless it's the Nth call
    state = _load_monitor_state(project_dir, session_id)
    state["call_count"] += 1

    if state["call_count"] % _MONITOR_CHECK_INTERVAL != 0:
        _save_monitor_state(state, project_dir)
        return None

    # 2. Already warned for this session — no repeat
    if state.get("already_warned"):
        _save_monitor_state(state, project_dir)
        return None

    # 3. Check context fill via compactor
    from ccx.compactor import check_context_fill

    fill_pct, _ = check_context_fill(transcript_path)
    if fill_pct <= 0.5:
        _save_monitor_state(state, project_dir)
        return None

    # 4. Threshold exceeded — instruct agent to run compact skill
    state["already_warned"] = True
    _save_monitor_state(state, project_dir)

    return {
        "decision": "block",
        "reason": (
            f"Context usage exceeded 50% ({fill_pct:.0%}). "
            f"Please run /ccx:compact {transcript_path} to save a context summary "
            f"before starting a new session."
        ),
    }


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

    # Inject token usage data into SubagentStop events before serialization
    if event == "SubagentStop":
        agent_transcript = data.get("agent_transcript_path")
        data["input_tokens"] = None
        data["output_tokens"] = None
        data["context_fill_pct"] = None
        if agent_transcript:
            try:
                from ccx.token_tracker import parse_transcript
                agent_usage = parse_transcript(agent_transcript)
                data["input_tokens"] = agent_usage.input_tokens
                data["output_tokens"] = agent_usage.output_tokens
            except Exception as e:
                _log_error(project_dir, "_inject_tokens", event, session_id, e)
            try:
                from ccx.compactor import check_context_fill
                fill_pct, _ = check_context_fill(agent_transcript)
                data["context_fill_pct"] = round(fill_pct, 4)
            except Exception as e:
                _log_error(project_dir, "_inject_context_fill", event, session_id, e)

    storage_dir = resolve_storage_dir(project_dir)
    log_dir = os.path.join(storage_dir, ".ccx", "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, f"{session_id}.jsonl")
    line = json.dumps(data, ensure_ascii=False)

    # SubagentStop dedup: replace existing stop for same agent_id so that
    # schema-violation retries don't produce 1-start-2-stop pairs.
    if event == "SubagentStop":
        agent_id = data.get("agent_id")
        if not agent_id or not _replace_subagent_stop(log_path, agent_id, line):
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    else:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # Track token usage on session/subagent completion
    _track_tokens(event, data, project_dir, session_id, transcript_path)

    # Track context window usage on session/subagent completion
    _track_context(event, data, project_dir, session_id, transcript_path)

    # Monitor context fill on PostToolUse (throttled)
    if event == "PostToolUse":
        try:
            result = _monitor_context(project_dir, session_id,
                                      transcript_path)
            if result:
                print(json.dumps(result, ensure_ascii=False))
                return
        except Exception as e:
            _log_error(project_dir, "_monitor_context", event, session_id, e)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
        _log_error(project_dir, "main", None, None, e)
    sys.exit(0)
