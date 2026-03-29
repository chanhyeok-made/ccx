#!/usr/bin/env python3
"""
Claude Code hook handler: send macOS system notifications via osascript.

Reads JSON from stdin, builds a human-readable notification title and body
based on hook_event_name, and displays a macOS notification using osascript.

Only fires for user-facing events (Notification, Stop, StopFailure,
PermissionRequest, Elicitation, TaskCompleted, TeammateIdle).

Always exits 0 to never block Claude Code.
"""

import json
import os
import subprocess
import sys

# Maximum notification body length to avoid osascript issues
_MAX_BODY_LEN = 200


def _truncate(text: str, limit: int = _MAX_BODY_LEN) -> str:
    """Truncate text to *limit* characters, appending ellipsis if needed."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "\u2026"


def _escape(text: str) -> str:
    """Escape characters that break osascript single-quoted strings.

    osascript uses single-quoted AppleScript strings where backslashes and
    double quotes need escaping.  We also replace single quotes with a
    right-quote to avoid breaking the shell quoting.
    """
    return (
        text
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("'", "\u2019")
    )


def _read_task_title(project_dir: str) -> str:
    """Read the current task title from .ccx/current_task_title if it exists."""
    if not project_dir:
        return ""
    path = os.path.join(project_dir, ".ccx", "current_task_title")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, IOError):
        return ""


def _project_name(cwd: str) -> str:
    """Extract the project directory name from the working directory path."""
    if not cwd:
        return ""
    return os.path.basename(cwd)


def _build_message(event: str, data: dict) -> tuple[str, str] | None:
    """Return (title, body) for a supported event, or None to skip."""
    if event == "Notification":
        msg = data.get("message") or "Attention needed"
        ntype = data.get("notification_type")
        body = f"[{ntype}] {msg}" if ntype else msg
        return "Claude Code", body

    if event == "Stop":
        last_msg = data.get("last_assistant_message") or ""
        if last_msg:
            first_line = last_msg.split("\n", 1)[0]
            body = _truncate(first_line, 100)
        else:
            body = "\uc751\ub2f5 \uc644\ub8cc \u2014 \ud655\uc778\ud574\uc8fc\uc138\uc694"
        return "Claude Code", body

    if event == "StopFailure":
        error = data.get("error") or "unknown error"
        details = data.get("error_details")
        body = f"\uc624\ub958 \ubc1c\uc0dd: {error}"
        if details:
            body += f" ({_truncate(details, 80)})"
        return "Claude Code", body

    if event == "PermissionRequest":
        tool = data.get("tool_name") or "unknown"
        tool_input = data.get("tool_input") or {}
        summary = ""
        if isinstance(tool_input, dict):
            if "command" in tool_input:
                summary = _truncate(str(tool_input["command"]), 80)
            elif tool_input:
                first_key = next(iter(tool_input))
                summary = f"{first_key}: {_truncate(str(tool_input[first_key]), 60)}"
        body = f"\uad8c\ud55c \uc694\uccad: {tool}"
        if summary:
            body += f" \u2014 {summary}"
        return "Claude Code", body

    if event == "Elicitation":
        tool = data.get("tool_name") or "unknown"
        server = data.get("mcp_server")
        body = f"\uc785\ub825 \uc694\uccad: {tool}"
        if server:
            body += f" ({server})"
        return "Claude Code", body

    if event == "TaskCompleted":
        subject = data.get("task_subject") or "task"
        desc = data.get("task_description") or ""
        first_line = desc.split("\n", 1)[0] if desc else ""
        body = f"\ud0dc\uc2a4\ud06c \uc644\ub8cc: {subject}"
        if first_line:
            body += f" \u2014 {_truncate(first_line, 80)}"
        return "Claude Code", body

    if event == "TeammateIdle":
        name = data.get("teammate_name") or "teammate"
        team = data.get("team_name")
        body = f"\ud300\uba54\uc774\ud2b8 \ub300\uae30\uc911: {name}"
        if team:
            body += f" ({team})"
        return "Claude Code", body

    return None


def _notify(title: str, subtitle: str, body: str) -> None:
    """Display a macOS notification via osascript with optional subtitle."""
    safe_title = _escape(_truncate(title))
    safe_subtitle = _escape(_truncate(subtitle, 80))
    safe_body = _escape(_truncate(body))

    script = (
        f'display notification "{safe_body}" '
        f'with title "{safe_title}" '
    )
    if safe_subtitle:
        script += f'subtitle "{safe_subtitle}" '
    script += 'sound name "Glass"'

    subprocess.run(
        ["osascript", "-e", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return

    data = json.loads(raw)
    event = data.get("hook_event_name", "unknown")
    cwd = data.get("cwd", "")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", cwd)

    task_title = _read_task_title(project_dir)
    proj_name = _project_name(cwd)
    subtitle = task_title or proj_name

    message = _build_message(event, data)
    if message is None:
        return

    title, body = message
    _notify(title, subtitle, body)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
