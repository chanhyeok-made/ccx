#!/usr/bin/env python3
"""
Claude Code SubagentStop hook: validate agent output against required schema.

Reads JSON from stdin, checks that the last assistant message from ccx agents
contains all required output markers. On violation, outputs a JSON decision
to block the agent (prompting it to include missing fields).

Always exits 0 — uses {"decision": "block", ...} for enforcement.
"""

import json
import os
import sys
from datetime import datetime, timezone

# Allow importing ccx package when running as a standalone hook script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ccx.storage import resolve_storage_dir

# Per-agent required output markers (case-insensitive search)
AGENT_SCHEMAS = {
    "ccx:planner": {
        "required_markers": [
            "Intent:", "Scope:", "Constraints:",
            "Complexity:", "| #", "Execution order:",
        ],
    },
    "ccx:researcher": {
        "required_markers": ["Files:", "Dependencies:", "Impact zone:"],
    },
    "ccx:implementer": {
        "required_markers": ["Changed files:"],
    },
    "ccx:reviewer": {
        "required_markers": ["Verdict:", "Summary:"],
    },
    "ccx:module-analyzer": {
        "required_markers": ["STATUS: COMPLETE"],
    },
    "ccx:package-synthesizer": {
        "required_markers": ["STATUS: COMPLETE"],
    },
    "ccx:clarifier": {
        "required_markers": ["task_title:", "purpose:", "scope_summary:"],
    },
}


def log_violation(project_dir: str, agent_type: str, missing: list[str]) -> None:
    """Append a schema violation record to .ccx/logs/schema_violations.jsonl."""
    log_dir = os.path.join(resolve_storage_dir(project_dir), ".ccx", "logs")
    os.makedirs(log_dir, exist_ok=True)

    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agent_type": agent_type,
        "missing_fields": missing,
    }

    log_path = os.path.join(log_dir, "schema_violations.jsonl")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return

    data = json.loads(raw)

    # Guard: prevent infinite loops when this hook itself spawns a subagent
    if data.get("stop_hook_active"):
        return

    # Only validate ccx-namespaced agents
    agent_type = data.get("agent_type", "")
    if not agent_type.startswith("ccx:"):
        return

    schema = AGENT_SCHEMAS.get(agent_type)
    if schema is None:
        return

    message = data.get("last_assistant_message", "")
    message_lower = message.lower()

    missing = [
        marker
        for marker in schema["required_markers"]
        if marker.lower() not in message_lower
    ]

    if not missing:
        return

    # Log the violation
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd", ".")
    log_violation(project_dir, agent_type, missing)

    # Output block decision to stdout
    decision = {
        "decision": "block",
        "reason": (
            f"Schema violation: missing required fields: {missing}. "
            "Your Output Schema requires these fields."
        ),
    }
    print(json.dumps(decision, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
