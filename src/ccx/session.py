"""
Session state management.
File-based persistence for execution history across Claude Code sessions.
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    return Path(project_dir) / SESSION_DIR / SESSION_FILE


def load_session(project_dir: str, limit: int = 10) -> list[dict]:
    """Load recent execution records from disk."""
    path = _session_path(project_dir)
    if not path.exists():
        return []

    data = json.loads(path.read_text(encoding="utf-8"))
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
