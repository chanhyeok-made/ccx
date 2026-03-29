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


def _build_message(event: str, data: dict) -> tuple[str, str] | None:
    """Return (title, body) for a supported event, or None to skip."""
    if event == "Notification":
        body = data.get("message") or "Attention needed"
        return "Claude Code", body

    if event == "Stop":
        return "Claude Code", "\uc751\ub2f5 \uc644\ub8cc \u2014 \ud655\uc778\ud574\uc8fc\uc138\uc694"

    if event == "StopFailure":
        error = data.get("error") or "unknown error"
        return "Claude Code \u26a0\ufe0f", f"\uc624\ub958 \ubc1c\uc0dd: {error}"

    if event == "PermissionRequest":
        tool = data.get("tool_name") or "unknown"
        return "Claude Code", f"\uad8c\ud55c \uc694\uccad: {tool}"

    if event == "Elicitation":
        tool = data.get("tool_name") or "unknown"
        return "Claude Code", f"\uc785\ub825 \uc694\uccad: {tool}"

    if event == "TaskCompleted":
        subject = data.get("task_subject") or "task"
        return "Claude Code", f"\ud0dc\uc2a4\ud06c \uc644\ub8cc: {subject}"

    if event == "TeammateIdle":
        name = data.get("teammate_name") or "teammate"
        return "Claude Code", f"\ud300\uba54\uc774\ud2b8 \ub300\uae30\uc911: {name}"

    return None


def _notify(title: str, body: str) -> None:
    """Display a macOS notification via osascript."""
    safe_title = _escape(_truncate(title))
    safe_body = _escape(_truncate(body))

    script = (
        f'display notification "{safe_body}" '
        f'with title "{safe_title}" '
        f'sound name "Glass"'
    )

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

    message = _build_message(event, data)
    if message is None:
        return

    title, body = message
    _notify(title, body)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
