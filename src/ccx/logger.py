"""
Auxiliary logging for MCP tool calls.
Writes JSONL logs to .ccx/logs/ — never raises exceptions.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

LOGS_DIR = ".ccx/logs"


def log_tool_call(
    project_dir: str,
    tool_name: str,
    input_data: dict,
    output_data: object,
    duration_ms: int,
    success: bool,
    error: str | None = None,
) -> None:
    """Append a single tool-call record to the daily JSONL log.

    Silently catches all errors — logging must never break MCP tools.
    """
    try:
        log_dir = Path(project_dir) / LOGS_DIR
        log_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = log_dir / f"mcp_{date_str}.jsonl"

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": str(uuid4()),
            "layer": "mcp",
            "operation": tool_name,
            "input": input_data,
            "output": _safe_serialize(output_data),
            "duration_ms": duration_ms,
            "success": success,
            "error": error,
        }

        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _safe_serialize(obj: object) -> object:
    """Return obj if it's JSON-safe, otherwise convert to string."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (dict, list)):
        return obj
    return str(obj)
