"""
Session state management.
File-based persistence for execution history across Claude Code sessions.
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ccx.storage import resolve_storage_dir

SESSION_DIR = ".ccx"
SESSION_FILE = "session.json"
MAX_RECORDS = 50


@dataclass
class ExecutionRecord:
    """Record of a single request execution."""
    timestamp: str
    request: str
    success: bool
    summary: str = ""
    changes: list = field(default_factory=list)
    error: str = ""


def _session_path(project_dir: str) -> Path:
    return Path(resolve_storage_dir(project_dir)) / SESSION_DIR / SESSION_FILE


def load_session(project_dir: str, limit: int = 10) -> list[dict]:
    """Load recent execution records from disk."""
    path = _session_path(project_dir)
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    records = data.get("records", [])
    return records[-limit:] if limit else records


def save_record(
    project_dir: str,
    request: str,
    success: bool,
    summary: str = "",
    changes: list | None = None,
    error: str = "",
) -> dict:
    """Append an execution record and persist to disk. Returns the saved record."""
    path = _session_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"records": []}

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request": request,
        "success": success,
        "summary": summary,
        "changes": changes or [],
        "error": error,
    }

    data["records"].append(record)

    # Rolling window
    if len(data["records"]) > MAX_RECORDS:
        data["records"] = data["records"][-MAX_RECORDS:]

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def save_task_title(project_dir: str, title: str, session_id: str = "") -> None:
    """Save current task title to .ccx/task_title.json and a flat file .ccx/current_task_title for hooks."""
    storage = Path(resolve_storage_dir(project_dir)) / SESSION_DIR
    storage.mkdir(parents=True, exist_ok=True)

    data = {
        "title": title,
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # JSON for MCP consumption
    path = storage / "task_title.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Plain text flat file for hook consumption (hooks can't call MCP)
    flat = storage / "current_task_title"
    flat.write_text(title, encoding="utf-8")


def get_task_title(project_dir: str) -> dict:
    """Read current task title from .ccx/task_title.json."""
    storage = Path(resolve_storage_dir(project_dir)) / SESSION_DIR
    path = storage / "task_title.json"
    if not path.exists():
        return {"title": "", "session_id": "", "timestamp": ""}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"title": "", "session_id": "", "timestamp": ""}


def get_context_summary(project_dir: str) -> str:
    """Generate a summary of recent context for follow-up requests."""
    records = load_session(project_dir, limit=3)
    if not records:
        return ""

    lines = ["[Previous context]"]
    for r in records:
        status = "Success" if r["success"] else "Failed"
        lines.append(f"- {r['request']}: {status} - {r.get('summary', r.get('error', ''))}")
        if r["success"] and r.get("changes"):
            files = [c.get("path", c) if isinstance(c, dict) else str(c) for c in r["changes"][:5]]
            lines.append(f"  Modified: {', '.join(files)}")

    return "\n".join(lines)
