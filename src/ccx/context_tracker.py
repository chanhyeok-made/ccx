"""
Context window usage tracking for Claude Code sessions.
Parses transcript JSONL files to build per-turn context fill time-series,
computes fill rate statistics, and detects compaction events.

Storage layout:
    .ccx/context-usage/{session_id}.json
"""

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

from ccx._transcript_utils import parse_assistant_messages, infer_agent_info

_CCX_DIR = ".ccx"
_CONTEXT_USAGE_DIR = "context-usage"

# A turn is flagged as compaction when context_fill drops below this fraction
# of the previous turn's value.  Empirical data shows real compactions
# typically cause an 80-90 % drop (e.g. 167 287 -> 19 771).
_COMPACTION_RATIO = 0.5


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class TurnContext:
    """Context window snapshot for a single assistant turn."""

    turn_index: int
    message_id: str
    input_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    output_tokens: int
    context_fill: int
    is_compaction: bool


@dataclass
class AgentContextUsage:
    """Per-agent context usage time-series with compaction metadata."""

    agent_id: str
    agent_type: str
    turns: list[dict] = field(default_factory=list)
    max_context_fill: int = 0
    final_context_fill: int = 0
    avg_context_fill: int = 0
    compaction_count: int = 0
    compaction_points: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _context_usage_dir(project_dir: str) -> Path:
    """Return .ccx/context-usage/ path."""
    return Path(project_dir) / _CCX_DIR / _CONTEXT_USAGE_DIR


def _session_path(project_dir: str, session_id: str) -> Path:
    """Return .ccx/context-usage/{session_id}.json path."""
    return _context_usage_dir(project_dir) / f"{session_id}.json"


def _ensure_dir(project_dir: str) -> None:
    """Create context-usage directory if needed."""
    _context_usage_dir(project_dir).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Core: parse transcript into context usage time-series
# ---------------------------------------------------------------------------

def parse_context_usage(
    transcript_path: str,
    agent_id: str | None = None,
    agent_type: str | None = None,
) -> AgentContextUsage:
    """Parse a transcript JSONL file into per-turn context fill time-series.

    Extracts ``message.usage`` from each ``type == "assistant"`` line,
    deduplicates streaming entries, and computes ``context_fill`` as
    ``input_tokens + cache_creation_input_tokens + cache_read_input_tokens``.

    Compaction is detected when ``context_fill`` drops below 50 % of the
    previous turn's value.

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
        return AgentContextUsage(agent_id=agent_id, agent_type=agent_type)

    # Build per-turn time-series
    turns: list[TurnContext] = []
    compaction_points: list[int] = []
    prev_fill = 0

    for idx, entry in enumerate(unique_entries):
        msg = entry.get("message", {})
        usage = msg.get("usage", {})
        msg_id = msg.get("id", "")

        input_tokens = usage.get("input_tokens", 0)
        cache_creation = usage.get("cache_creation_input_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)

        context_fill = input_tokens + cache_creation + cache_read

        # Compaction detection: fill dropped to less than 50 % of previous
        is_compaction = False
        if idx > 0 and prev_fill > 0:
            if context_fill < prev_fill * _COMPACTION_RATIO:
                is_compaction = True
                compaction_points.append(idx)

        turns.append(TurnContext(
            turn_index=idx,
            message_id=msg_id,
            input_tokens=input_tokens,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
            output_tokens=output_tokens,
            context_fill=context_fill,
            is_compaction=is_compaction,
        ))

        prev_fill = context_fill

    # Compute summary statistics
    fills = [t.context_fill for t in turns]
    max_fill = max(fills) if fills else 0
    final_fill = fills[-1] if fills else 0
    avg_fill = int(sum(fills) / len(fills)) if fills else 0

    return AgentContextUsage(
        agent_id=agent_id,
        agent_type=agent_type,
        turns=[asdict(t) for t in turns],
        max_context_fill=max_fill,
        final_context_fill=final_fill,
        avg_context_fill=avg_fill,
        compaction_count=len(compaction_points),
        compaction_points=compaction_points,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _empty_session(session_id: str, project_dir: str) -> dict:
    """Return a blank context usage session dict."""
    return {
        "session_id": session_id,
        "project_dir": project_dir,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agents": [],
        "total_max_context_fill": 0,
        "total_compaction_count": 0,
    }


def save_context_usage(
    project_dir: str,
    session_id: str,
    agent_context: AgentContextUsage,
) -> dict:
    """Save or update an agent's context usage in the session file.

    If a session file already exists, the agent is appended to (or updated
    in) the ``agents`` list.  Session-level totals are recomputed.

    Returns ``{"status": "ok", "session_id": ...}``.
    """
    _ensure_dir(project_dir)
    path = _session_path(project_dir, session_id)

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
    usage_dict = asdict(agent_context)
    replaced = False
    for i, existing in enumerate(agents):
        if existing.get("agent_id") == agent_context.agent_id:
            agents[i] = usage_dict
            replaced = True
            break
    if not replaced:
        agents.append(usage_dict)

    data["agents"] = agents

    # Recompute session-level totals
    data["total_max_context_fill"] = max(
        (a.get("max_context_fill", 0) for a in agents), default=0,
    )
    data["total_compaction_count"] = sum(
        a.get("compaction_count", 0) for a in agents
    )

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "session_id": session_id}


def get_context_usage(project_dir: str, session_id: str) -> dict:
    """Retrieve context usage for a session.

    Returns the full session dict with ``"status": "ok"`` if found,
    or ``{"status": "not_found"}`` otherwise.
    """
    path = _session_path(project_dir, session_id)
    if not path.exists():
        return {"status": "not_found"}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "not_found"}

    data["status"] = "ok"
    return data


def list_context_usages(project_dir: str, limit: int = 10) -> dict:
    """List recent session context usage summaries, sorted newest first.

    Returns ``{"status": "ok", "sessions": [...], "count": N}``.
    """
    usage_dir = _context_usage_dir(project_dir)
    if not usage_dir.exists():
        return {"status": "ok", "sessions": [], "count": 0}

    sessions: list[dict] = []
    for fp in usage_dir.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        agents = data.get("agents", [])

        # Compute session-level avg and final context fill from agents
        avg_fills = [a.get("avg_context_fill", 0) for a in agents if a.get("avg_context_fill")]
        final_fills = [a.get("final_context_fill", 0) for a in agents if a.get("final_context_fill")]
        session_avg_fill = int(sum(avg_fills) / len(avg_fills)) if avg_fills else 0
        session_final_fill = max(final_fills) if final_fills else 0

        sessions.append({
            "session_id": data.get("session_id", fp.stem),
            "timestamp": data.get("timestamp", ""),
            "total_max_context_fill": data.get("total_max_context_fill", 0),
            "avg_context_fill": session_avg_fill,
            "final_context_fill": session_final_fill,
            "total_compaction_count": data.get("total_compaction_count", 0),
            "agent_count": len(agents),
        })

    sessions.sort(key=lambda s: s.get("timestamp", ""), reverse=True)

    if limit:
        sessions = sessions[:limit]

    return {"status": "ok", "sessions": sessions, "count": len(sessions)}
