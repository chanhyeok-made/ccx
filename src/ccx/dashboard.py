"""
Dashboard for ccx usage metrics.
Aggregates token usage, context window usage, event logs, and execution
history, then renders a single-page HTML report with Chart.js
visualizations and 3-level drill-down navigation.

The primary axis is **execution history**: each user prompt submission
marks the start of a new execution, and agents are grouped under their
parent execution.

Public API:
    aggregate_data(project_dir, limit=50) -> dict
    generate_html(project_dir, limit=50) -> str
"""

import json
from datetime import datetime
from pathlib import Path

from ccx.token_tracker import list_session_usages, get_session_usage
from ccx.context_tracker import list_context_usages, get_context_usage
from ccx.session import load_session
from ccx.storage import resolve_storage_dir


# ---------------------------------------------------------------------------
# Event log parsing
# ---------------------------------------------------------------------------

_LOG_DIR = ".ccx/logs"
_TIMELINE_EVENTS = {
    "SubagentStart", "SubagentStop", "Stop", "SessionStart",
    "UserPromptSubmit",
}
_TOOL_EVENTS = {"PreToolUse", "PostToolUse"}


def _parse_event_log(
    log_path: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Parse a JSONL event log and extract timeline, tool, and agent events.

    Returns a tuple of (timeline_events, tool_events, agent_calls).

    timeline_events include SubagentStart, SubagentStop, Stop,
    SessionStart, and UserPromptSubmit events.  SubagentStop events
    carry ``last_assistant_message``, ``stop_hook_active``,
    ``input_tokens``, ``output_tokens``, and ``context_fill_pct`` fields.
    UserPromptSubmit events carry the ``prompt`` field.

    agent_calls track Agent tool invocations matched across
    PreToolUse / PostToolUse pairs via ``tool_use_id``.
    """
    timeline_events: list[dict] = []
    tool_events: list[dict] = []
    # Pending Agent PreToolUse entries keyed by tool_use_id
    _pending_agents: dict[str, dict] = {}
    path = Path(log_path)
    if not path.exists():
        return timeline_events, tool_events, []

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_name = entry.get("hook_event_name", "")
                if event_name in _TIMELINE_EVENTS:
                    evt: dict = {
                        "hook_event_name": event_name,
                        "agent_id": entry.get("agent_id"),
                        "agent_type": entry.get("agent_type"),
                        "timestamp": entry.get("timestamp", ""),
                    }
                    # SubagentStop extras
                    if event_name == "SubagentStop":
                        evt["last_assistant_message"] = entry.get(
                            "last_assistant_message", ""
                        )
                        evt["stop_hook_active"] = entry.get(
                            "stop_hook_active", False
                        )
                        evt["input_tokens"] = entry.get("input_tokens")
                        evt["output_tokens"] = entry.get("output_tokens")
                        evt["context_fill_pct"] = entry.get(
                            "context_fill_pct"
                        )
                    # UserPromptSubmit extras
                    if event_name == "UserPromptSubmit":
                        evt["prompt"] = entry.get("prompt", "")
                    timeline_events.append(evt)
                elif event_name in _TOOL_EVENTS:
                    tool_name = entry.get("tool_name", "unknown")

                    # --- Agent tool call tracking ---
                    if tool_name == "Agent":
                        tuid = entry.get("tool_use_id", "")
                        if event_name == "PreToolUse" and tuid:
                            ti = entry.get("tool_input") or {}
                            _pending_agents[tuid] = {
                                "prompt": ti.get("prompt", ""),
                                "subagent_type": ti.get(
                                    "subagent_type", ""
                                ),
                                "description": ti.get("description", ""),
                                "caller_agent_id": (
                                    entry.get("agent_id") or "main"
                                ),
                                "tool_use_id": tuid,
                                "start_time": entry.get("timestamp", ""),
                            }
                        elif event_name == "PostToolUse" and tuid:
                            pending = _pending_agents.get(tuid)
                            if pending is not None:
                                tr = entry.get("tool_response") or {}
                                pending["child_agent_id"] = tr.get(
                                    "agentId", ""
                                )
                                pending["total_tokens"] = tr.get(
                                    "totalTokens", 0
                                )
                                pending["total_duration_ms"] = tr.get(
                                    "totalDurationMs", 0
                                )
                                pending["total_tool_use_count"] = tr.get(
                                    "totalToolUseCount", 0
                                )
                                pending["end_time"] = entry.get(
                                    "timestamp", ""
                                )
                        # Skip appending Agent events to tool_events
                        continue

                    tool_events.append({
                        "hook_event_name": event_name,
                        "tool_name": tool_name,
                        "timestamp": entry.get("timestamp", ""),
                    })
    except OSError:
        pass

    agent_calls = list(_pending_agents.values())
    return timeline_events, tool_events, agent_calls


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO timestamp string, returning None on failure.

    Always returns a timezone-aware datetime (UTC) to avoid
    offset-naive vs offset-aware comparison errors.
    """
    if not ts:
        return None
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Execution splitting
# ---------------------------------------------------------------------------

def _split_executions(
    events: list[dict],
    agent_calls: list[dict] | None = None,
) -> list[dict]:
    """Split timeline events into execution units bounded by UserPromptSubmit.

    Each execution is a dict:
        {prompt, start_time, end_time, agents: [
            {agent_id, agent_type, start_time, end_time, duration_ms,
             order, message, input_prompt, parent_agent_id,
             parent_agent_type}
        ]}

    When *agent_calls* (from ``_parse_event_log``) are supplied, each
    agent is enriched with its parent relationship:
    - ``input_prompt`` – the prompt the parent passed to the Agent tool.
    - ``parent_agent_id`` – the caller's agent_id (or ``"main"``).
    - ``parent_agent_type`` – the agent_type of the parent, resolved from
      the starts dict (falls back to ``"main"``).

    Dedup logic for SubagentStop: when the same agent_id has two stop
    events (stop_hook_active=false and true), prefer the one with
    stop_hook_active=false for ``message`` extraction (richer output).
    """
    # Build lookup: child_agent_id -> agent_call record
    child_to_call: dict[str, dict] = {
        ac["child_agent_id"]: ac
        for ac in (agent_calls or [])
        if ac.get("child_agent_id")
    }
    # Collect execution boundaries
    exec_boundaries: list[int] = []
    for i, evt in enumerate(events):
        if evt["hook_event_name"] == "UserPromptSubmit":
            exec_boundaries.append(i)

    # If no UserPromptSubmit found, treat all events as a single execution
    if not exec_boundaries:
        if not events:
            return []
        exec_boundaries = [0]

    executions: list[dict] = []

    for bi, boundary_idx in enumerate(exec_boundaries):
        boundary_evt = events[boundary_idx]

        # Determine event range for this execution
        next_boundary = (
            exec_boundaries[bi + 1] if bi + 1 < len(exec_boundaries)
            else len(events)
        )
        exec_events = events[boundary_idx:next_boundary]

        prompt = boundary_evt.get("prompt", "") if boundary_evt[
            "hook_event_name"
        ] == "UserPromptSubmit" else ""
        exec_start = boundary_evt.get("timestamp", "")

        # Collect starts and stops within this execution
        starts: dict[str, dict] = {}
        # stops: agent_id -> list of stop events (for dedup)
        stops: dict[str, list[dict]] = {}

        for evt in exec_events:
            ename = evt["hook_event_name"]
            aid = evt.get("agent_id")
            if not aid:
                continue
            if ename == "SubagentStart":
                if aid not in starts:
                    starts[aid] = evt
            elif ename == "SubagentStop":
                stops.setdefault(aid, []).append(evt)

        # Build agents list
        agents: list[dict] = []
        exec_end = exec_start

        for aid, start_evt in starts.items():
            stop_list = stops.get(aid, [])

            # Pick the best stop event for timing (first stop by timestamp)
            best_stop: dict | None = None
            if stop_list:
                stop_list_sorted = sorted(
                    stop_list, key=lambda e: e.get("timestamp", "")
                )
                best_stop = stop_list_sorted[0]

            # Pick message: prefer stop_hook_active=false (richer)
            message = ""
            inactive_stops = [
                s for s in stop_list if not s.get("stop_hook_active", True)
            ]
            active_stops = [
                s for s in stop_list if s.get("stop_hook_active", False)
            ]
            if inactive_stops:
                message = inactive_stops[0].get("last_assistant_message", "")
            elif active_stops:
                message = active_stops[0].get("last_assistant_message", "")

            # Extract inline token data from the best stop event
            log_input_tokens = None
            log_output_tokens = None
            log_context_fill_pct = None
            if best_stop:
                log_input_tokens = best_stop.get("input_tokens")
                log_output_tokens = best_stop.get("output_tokens")
                log_context_fill_pct = best_stop.get("context_fill_pct")

            start_ts = start_evt.get("timestamp", "")
            end_ts = best_stop.get("timestamp", "") if best_stop else ""

            start_dt = _parse_iso(start_ts)
            end_dt = _parse_iso(end_ts)

            duration_ms = 0
            if start_dt and end_dt:
                duration_ms = int(
                    (end_dt - start_dt).total_seconds() * 1000
                )

            # Track execution end time
            if end_ts and end_ts > exec_end:
                exec_end = end_ts

            # Enrich with parent-child relationship from agent_calls
            call = child_to_call.get(aid)
            if call:
                input_prompt = call.get("prompt", "")
                parent_agent_id = call.get("caller_agent_id", "")
                parent_agent_type = (
                    starts.get(parent_agent_id, {}).get("agent_type")
                    or "main"
                )
            else:
                input_prompt = ""
                parent_agent_id = ""
                parent_agent_type = ""

            agents.append({
                "agent_id": aid,
                "agent_type": start_evt.get("agent_type", "unknown"),
                "start_time": start_ts,
                "end_time": end_ts or None,
                "duration_ms": duration_ms,
                "order": 0,
                "message": message or "",
                "input_prompt": input_prompt,
                "parent_agent_id": parent_agent_id,
                "parent_agent_type": parent_agent_type,
                "log_input_tokens": log_input_tokens,
                "log_output_tokens": log_output_tokens,
                "log_context_fill_pct": log_context_fill_pct,
            })

        # Also handle stops without matching starts (orphan stops)
        for aid, stop_list in stops.items():
            if aid in starts:
                continue
            # Still extract message
            inactive_stops = [
                s for s in stop_list if not s.get("stop_hook_active", True)
            ]
            active_stops = [
                s for s in stop_list if s.get("stop_hook_active", False)
            ]
            best_stop = stop_list[0]
            message = ""
            if inactive_stops:
                message = inactive_stops[0].get("last_assistant_message", "")
            elif active_stops:
                message = active_stops[0].get("last_assistant_message", "")

            # Extract inline token data from the stop event
            log_input_tokens = best_stop.get("input_tokens")
            log_output_tokens = best_stop.get("output_tokens")
            log_context_fill_pct = best_stop.get("context_fill_pct")

            end_ts = best_stop.get("timestamp", "")
            if end_ts and end_ts > exec_end:
                exec_end = end_ts

            # Enrich orphan-stop agent with parent-child relationship
            call = child_to_call.get(aid)
            if call:
                input_prompt = call.get("prompt", "")
                parent_agent_id = call.get("caller_agent_id", "")
                parent_agent_type = (
                    starts.get(parent_agent_id, {}).get("agent_type")
                    or "main"
                )
            else:
                input_prompt = ""
                parent_agent_id = ""
                parent_agent_type = ""

            agents.append({
                "agent_id": aid,
                "agent_type": best_stop.get("agent_type", "unknown"),
                "start_time": None,
                "end_time": end_ts or None,
                "duration_ms": 0,
                "order": 0,
                "message": message or "",
                "input_prompt": input_prompt,
                "parent_agent_id": parent_agent_id,
                "parent_agent_type": parent_agent_type,
                "log_input_tokens": log_input_tokens,
                "log_output_tokens": log_output_tokens,
                "log_context_fill_pct": log_context_fill_pct,
            })

        # Sort agents by start_time, assign order
        agents.sort(key=lambda a: a.get("start_time") or "")
        for i, agent in enumerate(agents):
            agent["order"] = i + 1

        executions.append({
            "prompt": prompt,
            "start_time": exec_start,
            "end_time": exec_end if exec_end != exec_start else "",
            "agents": agents,
        })

    return executions


# ---------------------------------------------------------------------------
# Data aggregation
# ---------------------------------------------------------------------------

def aggregate_data(project_dir: str, limit: int = 50) -> dict:
    """Collect all dashboard data centred on execution history.

    Returns a dict with keys:
        executions - list of execution dicts (primary axis)
        token      - session list + per-session agent details
        context    - session list
        history    - session.json execution records
        overview   - chart-level aggregate data
    """
    # --- Token usage ---
    token_list = list_session_usages(project_dir, limit=limit)
    token_sessions = token_list.get("sessions", [])

    token_details: dict[str, dict] = {}
    for sess in token_sessions:
        sid = sess.get("session_id", "")
        detail = get_session_usage(project_dir, sid)
        if detail.get("status") == "ok":
            token_details[sid] = detail

    # Agent-type aggregation across all sessions
    agent_type_totals: dict[str, int] = {}
    for detail in token_details.values():
        for agent in detail.get("agents", []):
            atype = agent.get("agent_type", "unknown")
            agent_type_totals[atype] = (
                agent_type_totals.get(atype, 0) + agent.get("total_tokens", 0)
            )

    # --- Context usage ---
    context_list = list_context_usages(project_dir, limit=limit)
    context_sessions = context_list.get("sessions", [])

    context_details: dict[str, dict] = {}
    for sess in context_sessions:
        sid = sess.get("session_id", "")
        detail = get_context_usage(project_dir, sid)
        if detail.get("status") == "ok":
            context_details[sid] = detail

    # --- Event logs -> executions + tool usage ---
    log_dir = Path(resolve_storage_dir(project_dir)) / _LOG_DIR
    all_executions: list[dict] = []
    session_tool_usage: dict[str, dict[str, int]] = {}
    session_time_ranges: dict[str, tuple[str, str]] = {}

    if log_dir.exists():
        for fp in log_dir.glob("*.jsonl"):
            sid = fp.stem
            if sid in ("hook_errors", "schema_violations"):
                continue
            events, tool_events, agent_calls = _parse_event_log(str(fp))

            # Split into executions (pass agent_calls for parent-child)
            execs = _split_executions(events, agent_calls=agent_calls)
            for ex in execs:
                ex["session_id"] = sid
                # Enrich agents with token/context data
                # Build lookup maps by prefixed agent_id
                token_agents: dict[str, dict] = {}
                if sid in token_details:
                    for a in token_details[sid].get("agents", []):
                        token_agents[a.get("agent_id", "")] = a
                context_agents: dict[str, dict] = {}
                if sid in context_details:
                    for a in context_details[sid].get("agents", []):
                        context_agents[a.get("agent_id", "")] = a

                exec_total_tokens = 0
                exec_max_context = 0
                for agent in ex["agents"]:
                    raw_aid = agent["agent_id"]
                    prefixed_aid = f"agent-{raw_aid}"

                    ta = token_agents.get(prefixed_aid, {})
                    ca = context_agents.get(prefixed_aid, {})

                    agent["agent_id_short"] = raw_aid[:8]
                    agent["total_tokens"] = ta.get("total_tokens", 0)
                    agent["input_tokens"] = ta.get("input_tokens", 0)
                    agent["cache_creation_input_tokens"] = ta.get(
                        "cache_creation_input_tokens", 0
                    )
                    agent["cache_read_input_tokens"] = ta.get(
                        "cache_read_input_tokens", 0
                    )
                    agent["output_tokens"] = ta.get("output_tokens", 0)
                    agent["turn_count"] = ta.get("turn_count", 0)
                    agent["max_context_fill"] = ca.get("max_context_fill", 0)
                    agent["compaction_count"] = ca.get("compaction_count", 0)
                    agent["turns"] = ca.get("turns", [])
                    agent["compaction_points"] = ca.get(
                        "compaction_points", []
                    )

                    exec_total_tokens += agent["total_tokens"]
                    if agent["max_context_fill"] > exec_max_context:
                        exec_max_context = agent["max_context_fill"]

                ex["total_tokens"] = exec_total_tokens
                ex["max_context_fill"] = exec_max_context

            all_executions.extend(execs)

            # Tool usage aggregation per session
            if tool_events:
                tool_counts: dict[str, int] = {}
                for te in tool_events:
                    tn = te.get("tool_name", "unknown")
                    tool_counts[tn] = tool_counts.get(tn, 0) + 1
                session_tool_usage[sid] = tool_counts

            # Session time range
            all_timestamps: list[str] = []
            for evt in events:
                ts = evt.get("timestamp", "")
                if ts:
                    all_timestamps.append(ts)
            for te in tool_events:
                ts = te.get("timestamp", "")
                if ts:
                    all_timestamps.append(ts)
            if all_timestamps:
                all_timestamps.sort()
                session_time_ranges[sid] = (
                    all_timestamps[0],
                    all_timestamps[-1],
                )

    # --- Match session.json execution records to executions ---
    history = load_session(project_dir, limit=limit)
    if history:
        for rec in history:
            rec_ts = rec.get("timestamp", "")
            rec_dt = _parse_iso(rec_ts)
            if not rec_dt:
                continue
            # Find the execution whose time window covers this record
            for ex in all_executions:
                ex_start = _parse_iso(ex.get("start_time", ""))
                ex_end = _parse_iso(ex.get("end_time", ""))
                if not ex_start:
                    continue
                # Use a generous window: execution start to end (or +10min)
                if ex_end is None or ex_end <= ex_start:
                    from datetime import timedelta
                    ex_end = ex_start + timedelta(minutes=10)
                if ex_start <= rec_dt <= ex_end:
                    ex["changes"] = rec.get("changes", [])
                    ex["success"] = rec.get("success", False)
                    ex["summary"] = rec.get("summary", "")
                    ex["error"] = rec.get("error", "")
                    break

    # Sort executions by start_time descending (newest first)
    all_executions.sort(
        key=lambda e: e.get("start_time", ""), reverse=True
    )

    return {
        "executions": all_executions,
        "token": {
            "sessions": token_sessions,
            "agent_type_totals": agent_type_totals,
        },
        "context": {
            "sessions": context_sessions,
        },
        "history": history,
        "session_tool_usage": session_tool_usage,
        "session_time_ranges": session_time_ranges,
    }


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

_CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js"
_ANNOTATION_CDN = "https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation"


def _format_date(iso_str: str) -> str:
    """Extract a compact date label from an ISO timestamp."""
    if not iso_str:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%m/%d %H:%M")
    except (ValueError, TypeError):
        return iso_str[:16]


def _escape_html(text: str) -> str:
    """Minimal HTML entity escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate_html(project_dir: str, limit: int = 50) -> str:
    """Generate a self-contained HTML dashboard page with 3-level drill-down.

    Level 1 -- Execution History: execution list + aggregate charts.
    Level 2 -- Execution Detail: agent pipeline Gantt, agent table,
               message previews, changes accordion.
    Level 3 -- Agent Detail: token breakdown, context fill per turn,
               full message panel.

    The returned string is a complete HTML document with inline CSS/JS
    and Chart.js loaded from CDN.  All data is injected as JSON literals
    inside a ``<script>`` tag (serverless SPA).

    Args:
        project_dir: Project root directory path.
        limit: Maximum number of sessions/records to include.

    Returns:
        HTML string.
    """
    data = aggregate_data(project_dir, limit=limit)

    # --- Prepare chart data for overview ---
    token_sessions = list(reversed(data["token"]["sessions"]))
    token_labels = [
        _format_date(s.get("timestamp", "")) for s in token_sessions
    ]
    token_input = [s.get("total_input_tokens", 0) for s in token_sessions]
    token_cache_create = [
        s.get("total_cache_creation_input_tokens", 0) for s in token_sessions
    ]
    token_cache_read = [
        s.get("total_cache_read_input_tokens", 0) for s in token_sessions
    ]
    token_output = [s.get("total_output_tokens", 0) for s in token_sessions]

    context_sessions = list(reversed(data["context"]["sessions"]))
    context_labels = [
        _format_date(s.get("timestamp", "")) for s in context_sessions
    ]
    context_max_fills = [
        s.get("total_max_context_fill", 0) for s in context_sessions
    ]

    # Collect all agent types for consistent palette
    all_agent_types: list[str] = []
    for ex in data["executions"]:
        for a in ex.get("agents", []):
            at = a.get("agent_type", "unknown")
            if at not in all_agent_types:
                all_agent_types.append(at)

    # Build the embedded data JSON
    embedded_data = {
        "overview": {
            "token_labels": token_labels,
            "token_input": token_input,
            "token_cache_create": token_cache_create,
            "token_cache_read": token_cache_read,
            "token_output": token_output,
            "context_labels": context_labels,
            "context_max_fills": context_max_fills,
        },
        "executions": data["executions"],
        "agent_types": all_agent_types,
    }

    data_json = json.dumps(embedded_data, ensure_ascii=False)

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccx Dashboard</title>
<script src="{_CHART_JS_CDN}"></script>
<script src="{_ANNOTATION_CDN}"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #1a1a2e;
    color: #e0e0e0;
    padding: 24px;
    min-height: 100vh;
  }}
  h1 {{
    text-align: center;
    margin-bottom: 8px;
    font-size: 1.6rem;
    color: #4fc3f7;
    letter-spacing: 0.05em;
  }}
  .nav-bar {{
    text-align: center;
    margin-bottom: 20px;
  }}
  .breadcrumb {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 0.85rem;
    color: #90caf9;
  }}
  .breadcrumb a {{
    color: #4fc3f7;
    text-decoration: none;
    cursor: pointer;
  }}
  .breadcrumb a:hover {{ text-decoration: underline; }}
  .breadcrumb .sep {{ color: #555; }}
  .breadcrumb .current {{ color: #e0e0e0; }}
  .grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    max-width: 1400px;
    margin: 0 auto;
  }}
  @media (max-width: 900px) {{
    .grid {{ grid-template-columns: 1fr; }}
  }}
  .card {{
    background: #16213e;
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
  }}
  .card.full {{ grid-column: 1 / -1; }}
  .card h2 {{
    font-size: 1rem;
    margin-bottom: 14px;
    color: #90caf9;
  }}
  .empty-msg {{
    text-align: center;
    color: #666;
    padding: 40px 0;
    font-style: italic;
  }}
  canvas {{ max-height: 340px; }}

  /* Table styles */
  .table-wrap {{ overflow-x: auto; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
  }}
  th, td {{
    padding: 8px 12px;
    text-align: left;
    border-bottom: 1px solid #1a1a2e;
  }}
  th {{ color: #90caf9; font-weight: 600; }}
  td.empty {{ text-align: center; color: #666; font-style: italic; padding: 24px; }}
  .badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 600;
  }}
  .badge.success {{ background: #2e7d32; color: #c8e6c9; }}
  .badge.fail {{ background: #c62828; color: #ffcdd2; }}

  /* Clickable rows */
  tr.clickable {{
    cursor: pointer;
    transition: background 0.15s;
  }}
  tr.clickable:hover {{ background: #1e2f50; }}

  /* Gantt chart container */
  .gantt-container {{
    position: relative;
    overflow-x: auto;
    padding: 10px 0;
  }}
  .gantt-row {{
    display: flex;
    align-items: center;
    margin-bottom: 4px;
    height: 28px;
    cursor: pointer;
    transition: opacity 0.15s;
  }}
  .gantt-row:hover {{ opacity: 0.85; }}
  .gantt-label {{
    width: 140px;
    min-width: 140px;
    font-size: 0.75rem;
    color: #ccc;
    text-align: right;
    padding-right: 10px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .gantt-track {{
    flex: 1;
    position: relative;
    height: 22px;
    background: #1a1a2e;
    border-radius: 3px;
  }}
  .gantt-bar {{
    position: absolute;
    height: 100%;
    border-radius: 3px;
    min-width: 4px;
    display: flex;
    align-items: center;
    padding: 0 6px;
    font-size: 0.7rem;
    color: #fff;
    white-space: nowrap;
    overflow: hidden;
  }}
  .gantt-time-axis {{
    display: flex;
    margin-left: 140px;
    font-size: 0.65rem;
    color: #666;
    padding-top: 4px;
  }}
  .gantt-time-axis span {{
    flex: 1;
    text-align: center;
  }}

  /* Token number formatting */
  .num {{ font-variant-numeric: tabular-nums; }}

  /* View container */
  .view {{ display: none; }}
  .view.active {{ display: block; }}

  /* Back button style */
  .back-btn {{
    background: none;
    border: 1px solid #4fc3f7;
    color: #4fc3f7;
    padding: 4px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.8rem;
    margin-right: 8px;
  }}
  .back-btn:hover {{ background: #4fc3f722; }}

  /* Duration badge */
  .dur {{
    font-size: 0.7rem;
    color: #aaa;
    margin-left: 6px;
  }}

  /* Accordion for changes */
  .accordion-row {{ cursor: pointer; }}
  .accordion-row:hover {{ background: #1e2f50; }}
  .accordion-row td:first-child::before {{
    content: '\\25B6';
    display: inline-block;
    margin-right: 6px;
    font-size: 0.65rem;
    transition: transform 0.2s;
    color: #4fc3f7;
  }}
  .accordion-row.open td:first-child::before {{
    transform: rotate(90deg);
  }}
  .accordion-panel {{ display: none; }}
  .accordion-panel.open {{ display: table-row; }}
  .accordion-panel td {{
    padding: 0;
    border-bottom: 1px solid #1a1a2e;
  }}
  .accordion-content {{
    padding: 14px 20px;
    background: #0d1525;
    font-size: 0.82rem;
  }}
  .accordion-content h4 {{
    color: #90caf9;
    font-size: 0.8rem;
    margin: 12px 0 6px 0;
  }}
  .accordion-content h4:first-child {{ margin-top: 0; }}
  .accordion-content table {{
    width: auto;
    margin-bottom: 4px;
  }}
  .accordion-content table th {{
    font-size: 0.75rem;
    padding: 4px 10px;
  }}
  .accordion-content table td {{
    font-size: 0.78rem;
    padding: 4px 10px;
    border-bottom: 1px solid #16213e;
  }}
  .badge.created {{ background: #2e7d32; color: #c8e6c9; }}
  .badge.modified {{ background: #1565c0; color: #bbdefb; }}
  .badge.deleted {{ background: #c62828; color: #ffcdd2; }}
  .error-box {{
    background: #c6282833;
    color: #ffcdd2;
    padding: 8px 12px;
    border-radius: 6px;
    margin-top: 8px;
    font-size: 0.8rem;
  }}
  .meta-row {{
    display: flex;
    gap: 20px;
    margin-top: 8px;
    font-size: 0.78rem;
    color: #aaa;
  }}
  .meta-row span {{ display: inline-flex; align-items: center; gap: 4px; }}
  .meta-row .label {{ color: #90caf9; }}

  /* Agent card styles */
  .pipeline-flow {{
    display: flex;
    gap: 12px;
    overflow-x: auto;
    padding: 10px 0;
    align-items: flex-start;
  }}
  .agent-card {{
    background: #1a1a2e;
    border-radius: 10px;
    padding: 14px;
    min-width: 220px;
    max-width: 280px;
    flex-shrink: 0;
    cursor: pointer;
    transition: transform 0.15s, box-shadow 0.15s;
    border: 1px solid #2a2a4a;
  }}
  .agent-card:hover {{
    transform: translateY(-2px);
    box-shadow: 0 6px 16px rgba(0,0,0,0.4);
  }}
  .agent-card .card-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }}
  .agent-card .card-header .type-name {{
    font-weight: 600;
    font-size: 0.85rem;
  }}
  .agent-card .card-header .order-badge {{
    font-size: 0.7rem;
    background: #2a2a4a;
    padding: 2px 6px;
    border-radius: 4px;
    color: #90caf9;
  }}
  .agent-card .card-stats {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px;
    font-size: 0.75rem;
    color: #aaa;
    margin-bottom: 8px;
  }}
  .agent-card .card-stats .stat-val {{
    color: #e0e0e0;
    font-weight: 600;
  }}
  .agent-card .card-connector {{
    text-align: center;
    color: #4fc3f7;
    font-size: 1.2rem;
    flex-shrink: 0;
    align-self: center;
  }}
  .message-preview {{
    font-size: 0.75rem;
    color: #888;
    margin-top: 6px;
    max-height: 3.6em;
    overflow: hidden;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    cursor: pointer;
    border-top: 1px solid #2a2a4a;
    padding-top: 6px;
  }}
  .message-preview:hover {{ color: #bbb; }}

  /* Full message panel (Level 3) */
  .message-full {{
    background: #0d1525;
    border-radius: 8px;
    padding: 16px;
    max-height: 500px;
    overflow-y: auto;
    margin-top: 10px;
  }}
  .message-content {{
    white-space: pre-wrap;
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.8rem;
    color: #ccc;
    line-height: 1.6;
    word-break: break-word;
  }}

  /* Parent label in agent cards */
  .parent-label {{
    font-size: 0.7rem;
    color: #90caf9;
    margin-bottom: 6px;
  }}
  .input-prompt-preview {{
    font-size: 0.73rem;
    color: #999;
    margin-top: 4px;
    max-height: 2.8em;
    overflow: hidden;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    border-top: 1px dashed #2a2a4a;
    padding-top: 4px;
  }}

  /* Collapsible input prompt panel (Level 3) */
  .collapsible-header {{
    display: flex;
    align-items: center;
    cursor: pointer;
    user-select: none;
    gap: 8px;
  }}
  .collapsible-header .toggle-icon {{
    display: inline-block;
    font-size: 0.7rem;
    color: #4fc3f7;
    transition: transform 0.2s;
  }}
  .collapsible-header .toggle-icon.open {{
    transform: rotate(90deg);
  }}
  .collapsible-body {{
    display: none;
  }}
  .collapsible-body.open {{
    display: block;
  }}

  /* Exec summary header */
  .exec-summary {{
    display: flex;
    gap: 24px;
    align-items: flex-start;
    flex-wrap: wrap;
    margin-bottom: 6px;
  }}
  .exec-summary .prompt-text {{
    font-size: 0.95rem;
    color: #e0e0e0;
    flex: 1;
    min-width: 200px;
  }}
  .exec-summary .stat-pills {{
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
  }}
  .stat-pill {{
    background: #1a1a2e;
    padding: 4px 12px;
    border-radius: 6px;
    font-size: 0.78rem;
    color: #aaa;
  }}
  .stat-pill .pill-val {{
    color: #4fc3f7;
    font-weight: 600;
    margin-left: 4px;
  }}
</style>
</head>
<body>
<h1>ccx Dashboard</h1>
<div class="nav-bar">
  <div class="breadcrumb" id="breadcrumb"></div>
</div>

<!-- Level 1: Execution History -->
<div class="view active" id="view-executions">
  <div class="grid">
    <div class="card">
      <h2>Token Usage Trend</h2>
      <div id="tokenBarWrap"><canvas id="tokenBar"></canvas></div>
    </div>
    <div class="card">
      <h2>Context Fill Trend</h2>
      <div id="contextBarWrap"><canvas id="contextBar"></canvas></div>
    </div>
    <div class="card full">
      <h2>Execution History</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Prompt</th>
              <th>Agents</th>
              <th>Total Tokens</th>
              <th>Status</th>
              <th>Changes</th>
            </tr>
          </thead>
          <tbody id="exec-table-body"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- Level 2: Execution Detail -->
<div class="view" id="view-exec-detail">
  <div class="grid">
    <div class="card full" id="exec-summary-card">
    </div>
    <div class="card full">
      <h2>Agent Pipeline</h2>
      <div id="pipeline-container" class="pipeline-flow"></div>
    </div>
    <div class="card full">
      <h2>Agent Timeline</h2>
      <div id="gantt-container" class="gantt-container"></div>
    </div>
    <div class="card full">
      <h2>Agent Details</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Agent Type</th>
              <th>Agent ID</th>
              <th>Parent</th>
              <th>Duration</th>
              <th>Tokens</th>
              <th>Max Context</th>
              <th>Compactions</th>
            </tr>
          </thead>
          <tbody id="agent-table-body"></tbody>
        </table>
      </div>
    </div>
    <div class="card full" id="changes-card" style="display:none">
      <h2>Changes</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Path</th>
              <th>Type</th>
              <th>Intent</th>
            </tr>
          </thead>
          <tbody id="changes-table-body"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- Level 3: Agent Detail -->
<div class="view" id="view-agent">
  <div class="grid">
    <div class="card">
      <h2>Token Breakdown</h2>
      <div id="agentTokenWrap"><canvas id="agentTokenChart"></canvas></div>
    </div>
    <div class="card">
      <h2>Agent Summary</h2>
      <div id="agent-summary"></div>
    </div>
    <div class="card full">
      <h2>Context Fill per Turn</h2>
      <div id="agentContextWrap"><canvas id="agentContextChart"></canvas></div>
    </div>
    <div class="card full" id="input-prompt-card" style="display:none">
      <div class="collapsible-header" onclick="window._toggleInputPrompt()">
        <span class="toggle-icon" id="input-prompt-toggle">&#x25B6;</span>
        <h2 style="margin-bottom:0">Input Prompt</h2>
      </div>
      <div class="collapsible-body" id="input-prompt-body">
        <div class="message-full" style="margin-top:10px">
          <div class="message-content" id="input-prompt-content"></div>
        </div>
      </div>
    </div>
    <div class="card full" id="message-card">
      <h2>Agent Output</h2>
      <div id="agent-message" class="message-full">
        <div class="message-content" id="agent-message-content"></div>
      </div>
    </div>
    <div class="card full">
      <h2>Turn Details</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Turn</th>
              <th>Context Fill</th>
              <th>Input Tokens</th>
              <th>Output Tokens</th>
              <th>Compaction</th>
            </tr>
          </thead>
          <tbody id="turn-table-body"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<script>
(function() {{
  // -----------------------------------------------------------------------
  // Embedded data
  // -----------------------------------------------------------------------
  const D = {data_json};

  // -----------------------------------------------------------------------
  // State
  // -----------------------------------------------------------------------
  const state = {{ level: 1, execIdx: null, agentIdx: null }};
  const charts = [];

  // -----------------------------------------------------------------------
  // Palette & helpers
  // -----------------------------------------------------------------------
  const PALETTE = [
    '#4fc3f7', '#81c784', '#ffb74d', '#e57373',
    '#ba68c8', '#4dd0e1', '#aed581', '#ff8a65',
    '#f06292', '#7986cb', '#ce93d8', '#80cbc4',
  ];

  const typeColorMap = {{}};
  (D.agent_types || []).forEach((t, i) => {{
    typeColorMap[t] = PALETTE[i % PALETTE.length];
  }});

  function getTypeColor(t) {{
    if (!typeColorMap[t]) {{
      const keys = Object.keys(typeColorMap);
      typeColorMap[t] = PALETTE[keys.length % PALETTE.length];
    }}
    return typeColorMap[t];
  }}

  function fmtNum(n) {{
    if (n == null) return '-';
    return n.toLocaleString();
  }}

  function fmtDuration(ms) {{
    if (!ms) return '-';
    if (ms < 1000) return ms + 'ms';
    const s = (ms / 1000).toFixed(1);
    if (ms < 60000) return s + 's';
    const m = Math.floor(ms / 60000);
    const rem = ((ms % 60000) / 1000).toFixed(0);
    return m + 'm ' + rem + 's';
  }}

  function fmtTime(iso) {{
    if (!iso) return '-';
    try {{
      const d = new Date(iso);
      return d.toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit', second: '2-digit' }});
    }} catch(e) {{ return iso.slice(11, 19); }}
  }}

  function fmtDate(iso) {{
    if (!iso) return '?';
    try {{
      const d = new Date(iso);
      const mo = String(d.getMonth() + 1).padStart(2, '0');
      const da = String(d.getDate()).padStart(2, '0');
      const hh = String(d.getHours()).padStart(2, '0');
      const mm = String(d.getMinutes()).padStart(2, '0');
      return mo + '/' + da + ' ' + hh + ':' + mm;
    }} catch(e) {{ return iso.slice(0, 16); }}
  }}

  function escapeHtml(t) {{
    if (!t) return '';
    return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }}

  function truncate(s, n) {{
    if (!s) return '';
    return s.length > n ? s.slice(0, n) + '...' : s;
  }}

  function destroyCharts() {{
    while (charts.length) {{
      const c = charts.pop();
      try {{ c.destroy(); }} catch(e) {{}}
    }}
  }}

  // -----------------------------------------------------------------------
  // Navigation
  // -----------------------------------------------------------------------
  function navigate(level, execIdx, agentIdx) {{
    state.level = level;
    state.execIdx = execIdx != null ? execIdx : null;
    state.agentIdx = agentIdx != null ? agentIdx : null;

    destroyCharts();
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));

    if (level === 1) {{
      document.getElementById('view-executions').classList.add('active');
      renderExecutions();
    }} else if (level === 2) {{
      document.getElementById('view-exec-detail').classList.add('active');
      renderExecutionDetail(execIdx);
    }} else if (level === 3) {{
      document.getElementById('view-agent').classList.add('active');
      renderAgentDetail(execIdx, agentIdx);
    }}

    updateBreadcrumb();
  }}

  function updateBreadcrumb() {{
    const bc = document.getElementById('breadcrumb');
    let html = '';
    const execs = D.executions || [];

    if (state.level === 1) {{
      html = '<span class="current">Execution History</span>';
    }} else if (state.level === 2) {{
      const ex = execs[state.execIdx];
      const label = ex ? truncate(ex.prompt || fmtDate(ex.start_time), 40) : 'Execution';
      html = '<a onclick="window._nav(1)">Execution History</a>'
           + '<span class="sep">/</span>'
           + '<span class="current">' + escapeHtml(label) + '</span>';
    }} else if (state.level === 3) {{
      const ex = execs[state.execIdx];
      const exLabel = ex ? truncate(ex.prompt || fmtDate(ex.start_time), 30) : 'Execution';
      const agents = ex ? (ex.agents || []) : [];
      const agent = agents[state.agentIdx];
      const aLabel = agent ? (agent.agent_type + ' (' + agent.agent_id_short + ')') : 'Agent';
      html = '<a onclick="window._nav(1)">Execution History</a>'
           + '<span class="sep">/</span>'
           + '<a onclick="window._nav(2,' + state.execIdx + ')">' + escapeHtml(exLabel) + '</a>'
           + '<span class="sep">/</span>'
           + '<span class="current">' + escapeHtml(aLabel) + '</span>';
    }}

    bc.innerHTML = html;
  }}

  window._nav = function(level, a, b) {{
    navigate(level, a, b);
  }};

  window._toggleInputPrompt = function() {{
    const body = document.getElementById('input-prompt-body');
    const icon = document.getElementById('input-prompt-toggle');
    const isOpen = body.classList.contains('open');
    if (isOpen) {{
      body.classList.remove('open');
      icon.classList.remove('open');
    }} else {{
      body.classList.add('open');
      icon.classList.add('open');
    }}
  }};

  // -----------------------------------------------------------------------
  // Level 1: Execution History
  // -----------------------------------------------------------------------
  function renderExecutions() {{
    const ov = D.overview;
    const execs = D.executions || [];

    // Execution table
    const tbody = document.getElementById('exec-table-body');
    if (execs.length === 0) {{
      tbody.innerHTML = '<tr><td colspan="6" class="empty">No execution data</td></tr>';
    }} else {{
      tbody.innerHTML = execs.map((ex, idx) => {{
        const agentCount = (ex.agents || []).length;
        const totalTokens = ex.total_tokens || 0;
        const changesCount = (ex.changes || []).length;
        const hasStatus = 'success' in ex;
        const badge = hasStatus
          ? (ex.success ? '<span class="badge success">OK</span>' : '<span class="badge fail">FAIL</span>')
          : '<span style="color:#666">-</span>';

        return '<tr class="clickable" onclick="window._nav(2,' + idx + ')">'
          + '<td>' + escapeHtml(fmtDate(ex.start_time)) + '</td>'
          + '<td>' + escapeHtml(truncate(ex.prompt, 80)) + '</td>'
          + '<td>' + agentCount + '</td>'
          + '<td class="num">' + fmtNum(totalTokens) + '</td>'
          + '<td>' + badge + '</td>'
          + '<td>' + changesCount + '</td>'
          + '</tr>';
      }}).join('');
    }}

    // Token stacked bar
    if (ov.token_labels && ov.token_labels.length > 0) {{
      charts.push(new Chart(document.getElementById('tokenBar'), {{
        type: 'bar',
        data: {{
          labels: ov.token_labels,
          datasets: [
            {{ label: 'Input', data: ov.token_input, backgroundColor: '#4fc3f7' }},
            {{ label: 'Cache Create', data: ov.token_cache_create, backgroundColor: '#ffb74d' }},
            {{ label: 'Cache Read', data: ov.token_cache_read, backgroundColor: '#81c784' }},
            {{ label: 'Output', data: ov.token_output, backgroundColor: '#e57373' }},
          ],
        }},
        options: {{
          responsive: true,
          plugins: {{ legend: {{ labels: {{ color: '#ccc' }} }} }},
          scales: {{
            x: {{ stacked: true, ticks: {{ color: '#999', maxRotation: 45 }}, grid: {{ color: '#2a2a4a' }} }},
            y: {{ stacked: true, ticks: {{ color: '#999' }}, grid: {{ color: '#2a2a4a' }} }},
          }},
        }},
      }}));
    }} else {{
      document.getElementById('tokenBarWrap').innerHTML = '<div class="empty-msg">No token data</div>';
    }}

    // Context max fill bar chart
    if (ov.context_labels && ov.context_labels.length > 0) {{
      charts.push(new Chart(document.getElementById('contextBar'), {{
        type: 'bar',
        data: {{
          labels: ov.context_labels,
          datasets: [{{
            label: 'Max Context Fill',
            data: ov.context_max_fills,
            backgroundColor: '#ba68c8',
          }}],
        }},
        options: {{
          responsive: true,
          plugins: {{ legend: {{ labels: {{ color: '#ccc' }} }} }},
          scales: {{
            x: {{ ticks: {{ color: '#999', maxRotation: 45 }}, grid: {{ color: '#2a2a4a' }} }},
            y: {{ ticks: {{ color: '#999' }}, grid: {{ color: '#2a2a4a' }} }},
          }},
        }},
      }}));
    }} else {{
      document.getElementById('contextBarWrap').innerHTML = '<div class="empty-msg">No context data</div>';
    }}
  }}

  // -----------------------------------------------------------------------
  // Level 2: Execution Detail
  // -----------------------------------------------------------------------
  function renderExecutionDetail(execIdx) {{
    const execs = D.executions || [];
    const ex = execs[execIdx];
    if (!ex) {{
      document.getElementById('exec-summary-card').innerHTML = '<div class="empty-msg">Execution not found</div>';
      return;
    }}

    const agents = ex.agents || [];
    const changes = ex.changes || [];

    // --- Summary card ---
    const summaryCard = document.getElementById('exec-summary-card');
    let summaryHtml = '<div class="exec-summary">';
    summaryHtml += '<div class="prompt-text">' + escapeHtml(ex.prompt || '(no prompt)') + '</div>';
    summaryHtml += '<div class="stat-pills">';
    summaryHtml += '<span class="stat-pill">Time<span class="pill-val">' + escapeHtml(fmtDate(ex.start_time)) + '</span></span>';
    summaryHtml += '<span class="stat-pill">Agents<span class="pill-val">' + agents.length + '</span></span>';
    summaryHtml += '<span class="stat-pill">Tokens<span class="pill-val">' + fmtNum(ex.total_tokens || 0) + '</span></span>';
    if ('success' in ex) {{
      const sBadge = ex.success ? '<span class="badge success">OK</span>' : '<span class="badge fail">FAIL</span>';
      summaryHtml += '<span class="stat-pill">Status ' + sBadge + '</span>';
    }}
    if (changes.length > 0) {{
      summaryHtml += '<span class="stat-pill">Changes<span class="pill-val">' + changes.length + '</span></span>';
    }}
    summaryHtml += '</div></div>';
    if (ex.summary) {{
      summaryHtml += '<div style="margin-top:8px;font-size:0.82rem;color:#aaa">' + escapeHtml(ex.summary) + '</div>';
    }}
    if (ex.error) {{
      summaryHtml += '<div class="error-box">' + escapeHtml(ex.error) + '</div>';
    }}
    summaryCard.innerHTML = summaryHtml;

    // --- Agent Pipeline cards ---
    const pipeEl = document.getElementById('pipeline-container');
    if (agents.length === 0) {{
      pipeEl.innerHTML = '<div class="empty-msg">No agents</div>';
    }} else {{
      let pipeHtml = '';
      agents.forEach((a, ai) => {{
        if (ai > 0) {{
          pipeHtml += '<div class="card-connector" style="display:flex;align-items:center;color:#4fc3f7;font-size:1.4rem;flex-shrink:0">&#x2192;</div>';
        }}
        const color = getTypeColor(a.agent_type);
        const msgPreview = a.message ? truncate(a.message, 200) : '';
        const inputPromptPreview = a.input_prompt ? truncate(a.input_prompt, 120) : '';
        pipeHtml += '<div class="agent-card" onclick="window._nav(3,' + execIdx + ',' + ai + ')" style="border-left:3px solid ' + color + '">';
        if (a.parent_agent_type) {{
          pipeHtml += '<div class="parent-label">Spawned by ' + escapeHtml(a.parent_agent_type) + '</div>';
        }}
        pipeHtml += '<div class="card-header"><span class="type-name" style="color:' + color + '">' + escapeHtml(a.agent_type) + '</span>';
        pipeHtml += '<span class="order-badge">#' + a.order + '</span></div>';
        pipeHtml += '<div class="card-stats">';
        pipeHtml += '<span>Duration</span><span class="stat-val">' + fmtDuration(a.duration_ms) + '</span>';
        pipeHtml += '<span>Tokens</span><span class="stat-val">' + fmtNum(a.total_tokens) + '</span>';
        pipeHtml += '<span>Context</span><span class="stat-val">' + fmtNum(a.max_context_fill) + '</span>';
        pipeHtml += '<span>Turns</span><span class="stat-val">' + (a.turn_count || (a.turns || []).length || 0) + '</span>';
        pipeHtml += '</div>';
        if (inputPromptPreview) {{
          pipeHtml += '<div class="input-prompt-preview" title="Input prompt">' + escapeHtml(inputPromptPreview) + '</div>';
        }}
        if (msgPreview) {{
          pipeHtml += '<div class="message-preview" title="Click card to see full message">' + escapeHtml(msgPreview) + '</div>';
        }}
        pipeHtml += '</div>';
      }});
      pipeEl.innerHTML = pipeHtml;
    }}

    // --- Gantt chart ---
    const timedAgents = agents.filter(a => a.start_time);
    const ganttEl = document.getElementById('gantt-container');
    if (timedAgents.length === 0) {{
      ganttEl.innerHTML = '<div class="empty-msg">No timeline data available</div>';
    }} else {{
      let minTime = Infinity, maxTime = -Infinity;
      timedAgents.forEach(a => {{
        const st = new Date(a.start_time).getTime();
        const et = a.end_time ? new Date(a.end_time).getTime() : st;
        if (st < minTime) minTime = st;
        if (et > maxTime) maxTime = et;
      }});
      const totalRange = maxTime - minTime || 1;

      let ganttHtml = '';
      const sorted = [...timedAgents].sort((a, b) => (a.start_time || '').localeCompare(b.start_time || ''));
      sorted.forEach((a, i) => {{
        const origIdx = agents.indexOf(a);
        const st = new Date(a.start_time).getTime();
        const et = a.end_time ? new Date(a.end_time).getTime() : maxTime;
        const left = ((st - minTime) / totalRange * 100).toFixed(2);
        const width = Math.max(((et - st) / totalRange * 100), 0.5).toFixed(2);
        const color = getTypeColor(a.agent_type);
        const durStr = fmtDuration(a.duration_ms);
        const label = a.agent_type + ' (' + a.agent_id_short + ')';

        ganttHtml += '<div class="gantt-row" onclick="window._nav(3,' + execIdx + ',' + origIdx + ')">'
          + '<div class="gantt-label">' + escapeHtml(label) + '</div>'
          + '<div class="gantt-track">'
          + '<div class="gantt-bar" style="left:' + left + '%;width:' + width + '%;background:' + color + '">'
          + durStr
          + '</div></div></div>';
      }});

      const startDate = new Date(minTime);
      const endDate = new Date(maxTime);
      const midDate = new Date(minTime + totalRange / 2);
      ganttHtml += '<div class="gantt-time-axis">'
        + '<span>' + fmtTime(startDate.toISOString()) + '</span>'
        + '<span>' + fmtTime(midDate.toISOString()) + '</span>'
        + '<span>' + fmtTime(endDate.toISOString()) + '</span>'
        + '</div>';

      ganttEl.innerHTML = ganttHtml;
    }}

    // --- Agent table ---
    const atbody = document.getElementById('agent-table-body');
    if (agents.length === 0) {{
      atbody.innerHTML = '<tr><td colspan="8" class="empty">No agent data</td></tr>';
    }} else {{
      atbody.innerHTML = agents.map((a, ai) => {{
        const parentLabel = a.parent_agent_type ? escapeHtml(a.parent_agent_type) : '<span style="color:#666">-</span>';
        return '<tr class="clickable" onclick="window._nav(3,' + execIdx + ',' + ai + ')">'
          + '<td>' + a.order + '</td>'
          + '<td><span style="color:' + getTypeColor(a.agent_type) + '">' + escapeHtml(a.agent_type) + '</span></td>'
          + '<td><code>' + escapeHtml(a.agent_id_short) + '</code></td>'
          + '<td>' + parentLabel + '</td>'
          + '<td>' + fmtDuration(a.duration_ms) + '</td>'
          + '<td class="num">' + fmtNum(a.total_tokens) + '</td>'
          + '<td class="num">' + fmtNum(a.max_context_fill) + '</td>'
          + '<td>' + (a.compaction_count || 0) + '</td>'
          + '</tr>';
      }}).join('');
    }}

    // --- Changes card ---
    const changesCard = document.getElementById('changes-card');
    const ctbody = document.getElementById('changes-table-body');
    if (changes.length > 0) {{
      changesCard.style.display = '';
      ctbody.innerHTML = changes.map(c => {{
        const cType = (c.type || 'modified').toLowerCase();
        let badgeClass = 'modified';
        if (cType === 'created' || cType === 'create') badgeClass = 'created';
        else if (cType === 'deleted' || cType === 'delete') badgeClass = 'deleted';
        return '<tr>'
          + '<td><code>' + escapeHtml(c.path || '') + '</code></td>'
          + '<td><span class="badge ' + badgeClass + '">' + escapeHtml(cType) + '</span></td>'
          + '<td>' + escapeHtml(c.intent || '') + '</td>'
          + '</tr>';
      }}).join('');
    }} else {{
      changesCard.style.display = 'none';
      ctbody.innerHTML = '';
    }}
  }}

  // -----------------------------------------------------------------------
  // Level 3: Agent Detail
  // -----------------------------------------------------------------------
  function renderAgentDetail(execIdx, agentIdx) {{
    const execs = D.executions || [];
    const ex = execs[execIdx];
    if (!ex) {{
      document.getElementById('agent-summary').innerHTML = '<div class="empty-msg">Execution not found</div>';
      return;
    }}

    const agents = ex.agents || [];
    const agent = agents[agentIdx];
    if (!agent) {{
      document.getElementById('agent-summary').innerHTML = '<div class="empty-msg">Agent not found</div>';
      return;
    }}

    // --- Summary card ---
    const summaryEl = document.getElementById('agent-summary');
    const parentDisplay = agent.parent_agent_id
      ? escapeHtml(agent.parent_agent_type || 'unknown') + ' (<code>' + escapeHtml(agent.parent_agent_id) + '</code>)'
      : 'Main (top-level)';
    summaryEl.innerHTML = '<table>'
      + '<tr><th>Agent Type</th><td><span style="color:' + getTypeColor(agent.agent_type) + '">' + escapeHtml(agent.agent_type) + '</span></td></tr>'
      + '<tr><th>Agent ID</th><td><code>' + escapeHtml(agent.agent_id || '') + '</code></td></tr>'
      + '<tr><th>Parent Agent</th><td>' + parentDisplay + '</td></tr>'
      + '<tr><th>Start</th><td>' + fmtTime(agent.start_time) + '</td></tr>'
      + '<tr><th>Duration</th><td>' + fmtDuration(agent.duration_ms) + '</td></tr>'
      + '<tr><th>Total Tokens</th><td class="num">' + fmtNum(agent.total_tokens) + '</td></tr>'
      + '<tr><th>Turns</th><td>' + (agent.turn_count || (agent.turns || []).length || 0) + '</td></tr>'
      + '<tr><th>Max Context Fill</th><td class="num">' + fmtNum(agent.max_context_fill) + '</td></tr>'
      + '<tr><th>Compactions</th><td>' + (agent.compaction_count || 0) + '</td></tr>'
      + '</table>';

    // --- Token breakdown doughnut ---
    const tokenParts = [
      {{ label: 'Input', value: agent.input_tokens || 0, color: '#4fc3f7' }},
      {{ label: 'Cache Create', value: agent.cache_creation_input_tokens || 0, color: '#ffb74d' }},
      {{ label: 'Cache Read', value: agent.cache_read_input_tokens || 0, color: '#81c784' }},
      {{ label: 'Output', value: agent.output_tokens || 0, color: '#e57373' }},
    ];
    const hasTokens = tokenParts.some(p => p.value > 0);

    if (hasTokens) {{
      charts.push(new Chart(document.getElementById('agentTokenChart'), {{
        type: 'doughnut',
        data: {{
          labels: tokenParts.map(p => p.label),
          datasets: [{{
            data: tokenParts.map(p => p.value),
            backgroundColor: tokenParts.map(p => p.color),
            borderWidth: 0,
          }}],
        }},
        options: {{
          responsive: true,
          plugins: {{
            legend: {{ position: 'right', labels: {{ color: '#ccc' }} }},
          }},
        }},
      }}));
    }} else {{
      document.getElementById('agentTokenWrap').innerHTML = '<div class="empty-msg">No token data</div>';
    }}

    // --- Context fill line chart ---
    const turns = agent.turns || [];
    const compactionPts = agent.compaction_points || [];

    if (turns.length > 0) {{
      const turnLabels = turns.map(t => 'T' + t.turn_index);
      const fills = turns.map(t => t.context_fill || 0);

      const annotations = {{}};
      compactionPts.forEach((pt, i) => {{
        annotations['comp_' + i] = {{
          type: 'line',
          xMin: pt,
          xMax: pt,
          borderColor: '#e57373',
          borderWidth: 2,
          borderDash: [4, 4],
          label: {{
            display: true,
            content: 'Compaction',
            color: '#e57373',
            font: {{ size: 10 }},
            position: 'start',
          }},
        }};
      }});

      charts.push(new Chart(document.getElementById('agentContextChart'), {{
        type: 'line',
        data: {{
          labels: turnLabels,
          datasets: [{{
            label: 'Context Fill',
            data: fills,
            borderColor: getTypeColor(agent.agent_type),
            backgroundColor: getTypeColor(agent.agent_type) + '33',
            tension: 0.3,
            fill: true,
            pointRadius: 3,
            pointBackgroundColor: turns.map(t => t.is_compaction ? '#e57373' : getTypeColor(agent.agent_type)),
          }}],
        }},
        options: {{
          responsive: true,
          interaction: {{ mode: 'nearest', intersect: false }},
          plugins: {{
            legend: {{ labels: {{ color: '#ccc' }} }},
            annotation: Object.keys(annotations).length > 0 ? {{ annotations: annotations }} : undefined,
          }},
          scales: {{
            x: {{ ticks: {{ color: '#999' }}, grid: {{ color: '#2a2a4a' }}, title: {{ display: true, text: 'Turn', color: '#999' }} }},
            y: {{ ticks: {{ color: '#999' }}, grid: {{ color: '#2a2a4a' }}, title: {{ display: true, text: 'Context Fill (tokens)', color: '#999' }} }},
          }},
        }},
      }}));
    }} else {{
      document.getElementById('agentContextWrap').innerHTML = '<div class="empty-msg">No turn data</div>';
    }}

    // --- Input Prompt panel ---
    const ipCard = document.getElementById('input-prompt-card');
    const ipContent = document.getElementById('input-prompt-content');
    const ipBody = document.getElementById('input-prompt-body');
    const ipIcon = document.getElementById('input-prompt-toggle');
    if (agent.input_prompt) {{
      ipCard.style.display = '';
      ipContent.textContent = agent.input_prompt;
      ipBody.classList.remove('open');
      ipIcon.classList.remove('open');
    }} else {{
      ipCard.style.display = 'none';
      ipContent.textContent = '';
    }}

    // --- Message panel ---
    const msgCard = document.getElementById('message-card');
    const msgContent = document.getElementById('agent-message-content');
    if (agent.message) {{
      msgCard.style.display = '';
      msgContent.textContent = agent.message;
    }} else {{
      msgCard.style.display = 'none';
      msgContent.textContent = '';
    }}

    // --- Turn table ---
    const ttbody = document.getElementById('turn-table-body');
    if (turns.length === 0) {{
      ttbody.innerHTML = '<tr><td colspan="5" class="empty">No turn data</td></tr>';
    }} else {{
      ttbody.innerHTML = turns.map(t => {{
        const compBadge = t.is_compaction
          ? '<span class="badge fail">Yes</span>'
          : '<span style="color:#666">-</span>';
        return '<tr>'
          + '<td>' + t.turn_index + '</td>'
          + '<td class="num">' + fmtNum(t.context_fill) + '</td>'
          + '<td class="num">' + fmtNum(t.input_tokens) + '</td>'
          + '<td class="num">' + fmtNum(t.output_tokens) + '</td>'
          + '<td>' + compBadge + '</td>'
          + '</tr>';
      }}).join('');
    }}
  }}

  // -----------------------------------------------------------------------
  // Initial render
  // -----------------------------------------------------------------------
  navigate(1);
}})();
</script>
</body>
</html>"""

    return html
