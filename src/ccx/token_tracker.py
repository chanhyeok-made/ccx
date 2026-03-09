"""
Token usage tracking for Claude Code sessions.
Parses transcript JSONL files to aggregate per-agent token usage,
generates session-level summaries, and persists results to disk.

Storage layout:
    .ccx/token-usage/{session_id}.json
"""

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from ccx._transcript_utils import parse_assistant_messages, infer_agent_info

_CCX_DIR = ".ccx"
_TOKEN_USAGE_DIR = "token-usage"


@dataclass
class AgentUsage:
    """Token usage for a single agent (main or subagent)."""

    agent_id: str
    agent_type: str
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    turn_count: int = 0


@dataclass
class SessionUsage:
    """Aggregated token usage for an entire session."""

    session_id: str
    project_dir: str
    timestamp: str
    agents: list  # list[AgentUsage] serialized as dicts
    total_input_tokens: int = 0
    total_cache_creation_input_tokens: int = 0
    total_cache_read_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _token_usage_dir(project_dir: str) -> Path:
    """Return .ccx/token-usage/ path."""
    return Path(project_dir) / _CCX_DIR / _TOKEN_USAGE_DIR


def _session_usage_path(project_dir: str, session_id: str) -> Path:
    """Return .ccx/token-usage/{session_id}.json path."""
    return _token_usage_dir(project_dir) / f"{session_id}.json"


def _ensure_dir(project_dir: str) -> None:
    """Create token-usage directory if needed."""
    _token_usage_dir(project_dir).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

def parse_transcript(
    transcript_path: str,
    agent_id: str | None = None,
    agent_type: str | None = None,
) -> AgentUsage:
    """Parse a single transcript JSONL file and return aggregated AgentUsage.

    Processes only ``type == "assistant"`` lines with a ``message.usage``
    object.  Handles streaming deduplication so each message is counted once.

    When *agent_id* or *agent_type* are ``None`` they are inferred from the
    transcript path via :func:`infer_agent_info`.
    """
    path = Path(transcript_path)

    # Infer agent info when not explicitly provided
    if agent_id is None or agent_type is None:
        inferred_id, inferred_type = infer_agent_info(path)
        if agent_id is None:
            agent_id = inferred_id
        if agent_type is None:
            agent_type = inferred_type

    # Parse and deduplicate assistant messages
    unique_entries = parse_assistant_messages(transcript_path)

    if not unique_entries:
        return AgentUsage(agent_id=agent_id, agent_type=agent_type)

    # Aggregate
    input_tokens = 0
    cache_creation = 0
    cache_read = 0
    output_tokens = 0
    turn_count = len(unique_entries)

    for entry in unique_entries:
        usage = entry.get("message", {}).get("usage", {})
        input_tokens += usage.get("input_tokens", 0)
        cache_creation += usage.get("cache_creation_input_tokens", 0)
        cache_read += usage.get("cache_read_input_tokens", 0)
        output_tokens += usage.get("output_tokens", 0)

    total = input_tokens + cache_creation + cache_read + output_tokens

    return AgentUsage(
        agent_id=agent_id,
        agent_type=agent_type,
        input_tokens=input_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        output_tokens=output_tokens,
        total_tokens=total,
        turn_count=turn_count,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_agent_usage(project_dir: str, session_id: str, agent_usage: AgentUsage) -> dict:
    """Save or update an agent's usage in the session file.

    If a session file already exists, the agent is appended to (or updated in)
    the ``agents`` list.  Totals are recomputed after the update.

    Returns ``{"status": "ok", "session_id": ...}``.
    """
    _ensure_dir(project_dir)
    path = _session_usage_path(project_dir, session_id)

    # Load existing or create new
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = _empty_session(session_id, project_dir)
    else:
        data = _empty_session(session_id, project_dir)

    # Upsert: replace if same agent_id exists, else append
    agents: list[dict] = data.get("agents", [])
    usage_dict = asdict(agent_usage)
    replaced = False
    for i, existing in enumerate(agents):
        if existing.get("agent_id") == agent_usage.agent_id:
            agents[i] = usage_dict
            replaced = True
            break
    if not replaced:
        agents.append(usage_dict)

    data["agents"] = agents

    # Recompute totals
    data["total_input_tokens"] = sum(a.get("input_tokens", 0) for a in agents)
    data["total_cache_creation_input_tokens"] = sum(
        a.get("cache_creation_input_tokens", 0) for a in agents
    )
    data["total_cache_read_input_tokens"] = sum(
        a.get("cache_read_input_tokens", 0) for a in agents
    )
    data["total_output_tokens"] = sum(a.get("output_tokens", 0) for a in agents)
    data["total_tokens"] = sum(a.get("total_tokens", 0) for a in agents)

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "session_id": session_id}


def _empty_session(session_id: str, project_dir: str) -> dict:
    """Return a blank session usage dict."""
    return {
        "session_id": session_id,
        "project_dir": project_dir,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agents": [],
        "total_input_tokens": 0,
        "total_cache_creation_input_tokens": 0,
        "total_cache_read_input_tokens": 0,
        "total_output_tokens": 0,
        "total_tokens": 0,
    }


def get_session_usage(project_dir: str, session_id: str) -> dict:
    """Retrieve token usage for a session.

    Returns the full session dict if found, or ``{"status": "not_found"}``
    otherwise.
    """
    path = _session_usage_path(project_dir, session_id)
    if not path.exists():
        return {"status": "not_found"}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "not_found"}

    data["status"] = "ok"
    return data


def list_session_usages(project_dir: str, limit: int = 10) -> dict:
    """List recent session usage summaries, sorted by timestamp descending.

    Returns ``{"status": "ok", "sessions": [...], "count": N}``.
    """
    usage_dir = _token_usage_dir(project_dir)
    if not usage_dir.exists():
        return {"status": "ok", "sessions": [], "count": 0}

    sessions: list[dict] = []
    for fp in usage_dir.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sessions.append({
            "session_id": data.get("session_id", fp.stem),
            "timestamp": data.get("timestamp", ""),
            "total_tokens": data.get("total_tokens", 0),
            "total_input_tokens": data.get("total_input_tokens", 0),
            "total_cache_creation_input_tokens": data.get("total_cache_creation_input_tokens", 0),
            "total_cache_read_input_tokens": data.get("total_cache_read_input_tokens", 0),
            "total_output_tokens": data.get("total_output_tokens", 0),
            "agent_count": len(data.get("agents", [])),
        })

    # Sort by timestamp descending (most recent first)
    sessions.sort(key=lambda s: s.get("timestamp", ""), reverse=True)

    if limit:
        sessions = sessions[:limit]

    return {"status": "ok", "sessions": sessions, "count": len(sessions)}
