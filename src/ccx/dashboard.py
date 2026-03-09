"""
Dashboard for ccx usage metrics.
Aggregates token usage, context window usage, event logs, and execution
history, then renders a single-page HTML report with Chart.js
visualizations and 3-level drill-down navigation.

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


# ---------------------------------------------------------------------------
# Event log parsing
# ---------------------------------------------------------------------------

_LOG_DIR = ".ccx/logs"
_TIMELINE_EVENTS = {"SubagentStart", "SubagentStop", "Stop", "SessionStart"}
_TOOL_EVENTS = {"PreToolUse"}


def _parse_event_log(log_path: str) -> tuple[list[dict], list[dict]]:
    """Parse a JSONL event log and extract timeline + tool events.

    Returns a tuple of (timeline_events, tool_events).

    timeline_events: list of dicts with keys: hook_event_name, agent_id,
        agent_type, timestamp.  Includes SubagentStart, SubagentStop,
        Stop, and SessionStart events.

    tool_events: list of dicts with keys: hook_event_name, tool_name,
        timestamp.  Includes PreToolUse events.
    """
    timeline_events: list[dict] = []
    tool_events: list[dict] = []
    path = Path(log_path)
    if not path.exists():
        return timeline_events, tool_events

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
                    timeline_events.append({
                        "hook_event_name": event_name,
                        "agent_id": entry.get("agent_id"),
                        "agent_type": entry.get("agent_type"),
                        "timestamp": entry.get("timestamp", ""),
                    })
                elif event_name in _TOOL_EVENTS:
                    tool_events.append({
                        "hook_event_name": event_name,
                        "tool_name": entry.get("tool_name", "unknown"),
                        "timestamp": entry.get("timestamp", ""),
                    })
    except OSError:
        pass

    return timeline_events, tool_events


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO timestamp string, returning None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _build_agent_timeline(events: list[dict]) -> list[dict]:
    """Match SubagentStart/Stop pairs into an agent timeline.

    Returns a list of dicts sorted by start_time:
        {agent_id, agent_type, start_time, end_time, duration_ms, order}

    Handles the case where SubagentStop events outnumber SubagentStart
    events by matching only the first Stop per Start (keyed by agent_id).
    """
    # Collect starts
    starts: dict[str, dict] = {}
    for evt in events:
        if evt["hook_event_name"] == "SubagentStart" and evt.get("agent_id"):
            aid = evt["agent_id"]
            if aid not in starts:
                starts[aid] = evt

    # Match with first stop per agent_id
    matched_stops: set[str] = set()
    timeline: list[dict] = []

    for evt in events:
        if evt["hook_event_name"] == "SubagentStop" and evt.get("agent_id"):
            aid = evt["agent_id"]
            if aid in starts and aid not in matched_stops:
                matched_stops.add(aid)
                start_evt = starts[aid]
                start_dt = _parse_iso(start_evt["timestamp"])
                end_dt = _parse_iso(evt["timestamp"])

                duration_ms = 0
                if start_dt and end_dt:
                    duration_ms = int((end_dt - start_dt).total_seconds() * 1000)

                timeline.append({
                    "agent_id": aid,
                    "agent_type": start_evt.get("agent_type", "unknown"),
                    "start_time": start_evt["timestamp"],
                    "end_time": evt["timestamp"],
                    "duration_ms": duration_ms,
                })

    # Also add unmatched starts (still running or no stop event)
    for aid, start_evt in starts.items():
        if aid not in matched_stops:
            timeline.append({
                "agent_id": aid,
                "agent_type": start_evt.get("agent_type", "unknown"),
                "start_time": start_evt["timestamp"],
                "end_time": None,
                "duration_ms": 0,
            })

    # Sort by start_time chronologically, assign order
    timeline.sort(key=lambda x: x.get("start_time", ""))
    for i, entry in enumerate(timeline):
        entry["order"] = i + 1

    return timeline


# ---------------------------------------------------------------------------
# Data aggregation
# ---------------------------------------------------------------------------

def aggregate_data(project_dir: str, limit: int = 50) -> dict:
    """Collect all dashboard data from token, context, event log, and session
    sources.

    Returns a dict with keys:
        token    - session list + per-session agent details
        context  - session list + per-session agent details
        history  - execution records
        sessions - merged per-session data for drill-down
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

    # --- Event logs → timelines + tool usage ---
    log_dir = Path(project_dir) / _LOG_DIR
    timelines: dict[str, list[dict]] = {}
    session_starts: dict[str, str] = {}  # session_id -> model
    session_tool_usage: dict[str, dict[str, int]] = {}  # session_id -> {tool_name: count}
    session_time_ranges: dict[str, tuple[str, str]] = {}  # session_id -> (first_ts, last_ts)
    if log_dir.exists():
        for fp in log_dir.glob("*.jsonl"):
            sid = fp.stem
            if sid in ("hook_errors", "schema_violations"):
                continue
            events, tool_events = _parse_event_log(str(fp))
            timeline = _build_agent_timeline(events)
            if timeline:
                timelines[sid] = timeline
            # Extract SessionStart model info
            for evt in events:
                if evt["hook_event_name"] == "SessionStart":
                    session_starts[sid] = evt.get("timestamp", "")

            # Tool usage aggregation per session
            if tool_events:
                tool_counts: dict[str, int] = {}
                for te in tool_events:
                    tn = te.get("tool_name", "unknown")
                    tool_counts[tn] = tool_counts.get(tn, 0) + 1
                session_tool_usage[sid] = tool_counts

            # Compute session time range from all events (timeline + tool)
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
                session_time_ranges[sid] = (all_timestamps[0], all_timestamps[-1])

    # --- Execution history ---
    history = load_session(project_dir, limit=limit)

    # --- Merge per-session data for drill-down ---
    all_session_ids: set[str] = set()
    for s in token_sessions:
        all_session_ids.add(s.get("session_id", ""))
    for s in context_sessions:
        all_session_ids.add(s.get("session_id", ""))
    for sid in timelines:
        all_session_ids.add(sid)

    sessions_merged: list[dict] = []
    for sid in all_session_ids:
        if not sid:
            continue

        # Token summary
        token_summary = {}
        for ts in token_sessions:
            if ts.get("session_id") == sid:
                token_summary = ts
                break

        # Context summary
        context_summary = {}
        for cs in context_sessions:
            if cs.get("session_id") == sid:
                context_summary = cs
                break

        timestamp = (
            token_summary.get("timestamp")
            or context_summary.get("timestamp")
            or session_starts.get(sid, "")
        )

        # Build agent details by merging token + context + timeline
        # Use the prefixed agent_id mapping: 'agent-' + event_log_agent_id
        timeline = timelines.get(sid, [])

        # Token agent details indexed by agent_id
        token_agents: dict[str, dict] = {}
        if sid in token_details:
            for a in token_details[sid].get("agents", []):
                token_agents[a.get("agent_id", "")] = a

        # Context agent details indexed by agent_id
        context_agents: dict[str, dict] = {}
        if sid in context_details:
            for a in context_details[sid].get("agents", []):
                context_agents[a.get("agent_id", "")] = a

        # Build merged agent list
        agents_merged: list[dict] = []

        # Start with timeline agents (they have timing info)
        seen_agent_ids: set[str] = set()
        for tl_entry in timeline:
            raw_aid = tl_entry["agent_id"]
            prefixed_aid = f"agent-{raw_aid}"
            seen_agent_ids.add(prefixed_aid)

            token_agent = token_agents.get(prefixed_aid, {})
            context_agent = context_agents.get(prefixed_aid, {})

            agents_merged.append({
                "agent_id": prefixed_aid,
                "agent_id_short": raw_aid[:8],
                "agent_type": tl_entry.get("agent_type", token_agent.get("agent_type", "unknown")),
                "start_time": tl_entry.get("start_time"),
                "end_time": tl_entry.get("end_time"),
                "duration_ms": tl_entry.get("duration_ms", 0),
                "order": tl_entry.get("order", 0),
                "total_tokens": token_agent.get("total_tokens", 0),
                "input_tokens": token_agent.get("input_tokens", 0),
                "cache_creation_input_tokens": token_agent.get("cache_creation_input_tokens", 0),
                "cache_read_input_tokens": token_agent.get("cache_read_input_tokens", 0),
                "output_tokens": token_agent.get("output_tokens", 0),
                "turn_count": token_agent.get("turn_count", 0),
                "max_context_fill": context_agent.get("max_context_fill", 0),
                "compaction_count": context_agent.get("compaction_count", 0),
                "turns": context_agent.get("turns", []),
                "compaction_points": context_agent.get("compaction_points", []),
            })

        # Add agents from token/context data not in timeline (e.g. main)
        for aid, ta in token_agents.items():
            if aid not in seen_agent_ids:
                seen_agent_ids.add(aid)
                context_agent = context_agents.get(aid, {})
                agents_merged.append({
                    "agent_id": aid,
                    "agent_id_short": aid[:8] if aid != "main" else "main",
                    "agent_type": ta.get("agent_type", "unknown"),
                    "start_time": None,
                    "end_time": None,
                    "duration_ms": 0,
                    "order": 0,
                    "total_tokens": ta.get("total_tokens", 0),
                    "input_tokens": ta.get("input_tokens", 0),
                    "cache_creation_input_tokens": ta.get("cache_creation_input_tokens", 0),
                    "cache_read_input_tokens": ta.get("cache_read_input_tokens", 0),
                    "output_tokens": ta.get("output_tokens", 0),
                    "turn_count": ta.get("turn_count", 0),
                    "max_context_fill": context_agent.get("max_context_fill", 0),
                    "compaction_count": context_agent.get("compaction_count", 0),
                    "turns": context_agent.get("turns", []),
                    "compaction_points": context_agent.get("compaction_points", []),
                })

        for aid, ca in context_agents.items():
            if aid not in seen_agent_ids:
                seen_agent_ids.add(aid)
                agents_merged.append({
                    "agent_id": aid,
                    "agent_id_short": aid[:8] if aid != "main" else "main",
                    "agent_type": ca.get("agent_type", "unknown"),
                    "start_time": None,
                    "end_time": None,
                    "duration_ms": 0,
                    "order": 0,
                    "total_tokens": 0,
                    "input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 0,
                    "turn_count": 0,
                    "max_context_fill": ca.get("max_context_fill", 0),
                    "compaction_count": ca.get("compaction_count", 0),
                    "turns": ca.get("turns", []),
                    "compaction_points": ca.get("compaction_points", []),
                })

        sessions_merged.append({
            "session_id": sid,
            "timestamp": timestamp,
            "total_tokens": token_summary.get("total_tokens", 0),
            "total_input_tokens": token_summary.get("total_input_tokens", 0),
            "total_cache_creation_input_tokens": token_summary.get("total_cache_creation_input_tokens", 0),
            "total_cache_read_input_tokens": token_summary.get("total_cache_read_input_tokens", 0),
            "total_output_tokens": token_summary.get("total_output_tokens", 0),
            "total_max_context_fill": context_summary.get("total_max_context_fill", 0),
            "avg_context_fill": context_summary.get("avg_context_fill", 0),
            "agent_count": max(
                token_summary.get("agent_count", 0),
                context_summary.get("agent_count", 0),
                len(agents_merged),
            ),
            "agents": agents_merged,
        })

    # Sort by timestamp descending
    sessions_merged.sort(key=lambda s: s.get("timestamp", ""), reverse=True)

    return {
        "token": {
            "sessions": token_sessions,
            "agent_type_totals": agent_type_totals,
        },
        "context": {
            "sessions": context_sessions,
        },
        "history": history,
        "sessions": sessions_merged,
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

    Level 1 -- Session overview: session list table + aggregate charts.
    Level 2 -- Session detail: agent timeline (Gantt), agent table.
    Level 3 -- Agent detail: token breakdown, context fill per turn.

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

    # --- Prepare chart data for Level 1 overview ---
    token_sessions = list(reversed(data["token"]["sessions"]))
    token_labels = [_format_date(s.get("timestamp", "")) for s in token_sessions]
    token_input = [s.get("total_input_tokens", 0) for s in token_sessions]
    token_cache_create = [s.get("total_cache_creation_input_tokens", 0) for s in token_sessions]
    token_cache_read = [s.get("total_cache_read_input_tokens", 0) for s in token_sessions]
    token_output = [s.get("total_output_tokens", 0) for s in token_sessions]

    agent_totals = data["token"]["agent_type_totals"]

    context_sessions = list(reversed(data["context"]["sessions"]))
    context_labels = [_format_date(s.get("timestamp", "")) for s in context_sessions]
    context_max_fills = [s.get("total_max_context_fill", 0) for s in context_sessions]

    # Execution history rows (for Level 1) — enriched with session matching
    history = data["history"]
    session_tool_usage = data.get("session_tool_usage", {})
    session_time_ranges = data.get("session_time_ranges", {})
    sessions_data_for_match = data["sessions"]

    # Build lookup: session_id -> session summary (tokens, context)
    session_summary_map: dict[str, dict] = {}
    for sm in sessions_data_for_match:
        session_summary_map[sm["session_id"]] = sm

    history_rows: list[dict] = []
    if history:
        for rec in reversed(history):
            rec_ts = rec.get("timestamp", "")
            changes = rec.get("changes", [])

            # Session matching: find session whose time range covers this record
            matched_session_id: str | None = None
            if rec_ts:
                rec_dt = _parse_iso(rec_ts)
                if rec_dt:
                    for sid, (range_start, range_end) in session_time_ranges.items():
                        start_dt = _parse_iso(range_start)
                        end_dt = _parse_iso(range_end)
                        if start_dt and end_dt and start_dt <= rec_dt <= end_dt:
                            matched_session_id = sid
                            break

            # Tool usage from matched session
            tool_usage: dict[str, int] = {}
            if matched_session_id and matched_session_id in session_tool_usage:
                tool_usage = session_tool_usage[matched_session_id]

            # Token/context summary from matched session
            session_tokens = 0
            session_max_context = 0
            if matched_session_id and matched_session_id in session_summary_map:
                sm = session_summary_map[matched_session_id]
                session_tokens = sm.get("total_tokens", 0)
                session_max_context = sm.get("total_max_context_fill", 0)

            history_rows.append({
                "timestamp": _format_date(rec_ts),
                "request": rec.get("request", "")[:120],
                "success": rec.get("success", False),
                "summary": rec.get("summary", rec.get("error", ""))[:200],
                "changes_count": len(changes),
                "changes": changes,
                "error": rec.get("error", ""),
                "matched_session_id": matched_session_id,
                "tool_usage": tool_usage,
                "session_tokens": session_tokens,
                "session_max_context": session_max_context,
            })

    # --- Sessions data for drill-down ---
    sessions_data = data["sessions"]

    # Agent type color map for consistent coloring
    all_agent_types: list[str] = []
    for s in sessions_data:
        for a in s.get("agents", []):
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
            "donut_labels": list(agent_totals.keys()),
            "donut_values": list(agent_totals.values()),
            "context_labels": context_labels,
            "context_max_fills": context_max_fills,
            "history": history_rows,
        },
        "sessions": sessions_data,
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

  /* Accordion for execution history */
  .accordion-row {{ cursor: pointer; }}
  .accordion-row:hover {{ background: #1e2f50; }}
  .accordion-row td:first-child::before {{
    content: '\u25B6';
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
  .session-link {{
    color: #4fc3f7;
    cursor: pointer;
    text-decoration: none;
    font-size: 0.8rem;
  }}
  .session-link:hover {{ text-decoration: underline; }}
  .tool-list {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }}
  .tool-chip {{
    display: inline-block;
    background: #1e2f50;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.75rem;
    color: #ccc;
  }}
  .tool-chip .tool-count {{
    color: #4fc3f7;
    font-weight: 600;
    margin-left: 4px;
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
</style>
</head>
<body>
<h1>ccx Dashboard</h1>
<div class="nav-bar">
  <div class="breadcrumb" id="breadcrumb"></div>
</div>

<!-- Level 1: Overview -->
<div class="view active" id="view-overview">
  <div class="grid">
    <div class="card">
      <h2>Token Usage per Session</h2>
      <div id="tokenBarWrap"><canvas id="tokenBar"></canvas></div>
    </div>
    <div class="card">
      <h2>Context Max Fill per Session</h2>
      <div id="contextBarWrap"><canvas id="contextBar"></canvas></div>
    </div>
    <div class="card full">
      <h2>Sessions</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Session ID</th>
              <th>Timestamp</th>
              <th>Total Tokens</th>
              <th>Max Context Fill</th>
              <th>Agents</th>
            </tr>
          </thead>
          <tbody id="session-table-body"></tbody>
        </table>
      </div>
    </div>
    <div class="card full">
      <h2>Execution History</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Request</th>
              <th>Status</th>
              <th>Summary</th>
              <th>Changes</th>
            </tr>
          </thead>
          <tbody id="history-table-body"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- Level 2: Session Detail -->
<div class="view" id="view-session">
  <div class="grid">
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
              <th>Start</th>
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
    <div class="card">
      <h2>Session Token Breakdown</h2>
      <div id="sessionDonutWrap"><canvas id="sessionDonut"></canvas></div>
    </div>
    <div class="card">
      <h2>Agent Type Token Distribution</h2>
      <div id="sessionTypeBarWrap"><canvas id="sessionTypeBar"></canvas></div>
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
  const state = {{ level: 1, sessionId: null, agentId: null }};
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

  function escapeHtml(t) {{
    if (!t) return '';
    return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }}

  function destroyCharts() {{
    while (charts.length) {{
      const c = charts.pop();
      try {{ c.destroy(); }} catch(e) {{}}
    }}
  }}

  function getSession(sid) {{
    return (D.sessions || []).find(s => s.session_id === sid);
  }}

  function getAgent(sid, aid) {{
    const sess = getSession(sid);
    if (!sess) return null;
    return (sess.agents || []).find(a => a.agent_id === aid);
  }}

  // -----------------------------------------------------------------------
  // Navigation
  // -----------------------------------------------------------------------
  function navigate(level, sessionId, agentId) {{
    state.level = level;
    state.sessionId = sessionId || null;
    state.agentId = agentId || null;

    destroyCharts();

    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));

    if (level === 1) {{
      document.getElementById('view-overview').classList.add('active');
      renderOverview();
    }} else if (level === 2) {{
      document.getElementById('view-session').classList.add('active');
      renderSessionDetail(sessionId);
    }} else if (level === 3) {{
      document.getElementById('view-agent').classList.add('active');
      renderAgentDetail(sessionId, agentId);
    }}

    updateBreadcrumb();
  }}

  function updateBreadcrumb() {{
    const bc = document.getElementById('breadcrumb');
    let html = '';

    if (state.level === 1) {{
      html = '<span class="current">Overview</span>';
    }} else if (state.level === 2) {{
      html = '<a onclick="window._nav(1)">Overview</a>'
           + '<span class="sep">/</span>'
           + '<span class="current">' + escapeHtml(state.sessionId.slice(0,8)) + '...</span>';
    }} else if (state.level === 3) {{
      const agent = getAgent(state.sessionId, state.agentId);
      const aLabel = agent ? (agent.agent_type + ' (' + agent.agent_id_short + ')') : state.agentId;
      html = '<a onclick="window._nav(1)">Overview</a>'
           + '<span class="sep">/</span>'
           + '<a onclick="window._nav(2,\\'' + state.sessionId + '\\')">' + escapeHtml(state.sessionId.slice(0,8)) + '...</a>'
           + '<span class="sep">/</span>'
           + '<span class="current">' + escapeHtml(aLabel) + '</span>';
    }}

    bc.innerHTML = html;
  }}

  window._nav = function(level, sid, aid) {{
    navigate(level, sid, aid);
  }};

  window._toggleHistory = function(idx) {{
    const row = document.getElementById('hrow-' + idx);
    const panel = document.getElementById('hpanel-' + idx);
    if (row && panel) {{
      row.classList.toggle('open');
      panel.classList.toggle('open');
    }}
  }};

  // -----------------------------------------------------------------------
  // Level 1: Overview
  // -----------------------------------------------------------------------
  function renderOverview() {{
    const ov = D.overview;

    // Session table
    const tbody = document.getElementById('session-table-body');
    const sessions = D.sessions || [];
    if (sessions.length === 0) {{
      tbody.innerHTML = '<tr><td colspan="5" class="empty">No session data</td></tr>';
    }} else {{
      tbody.innerHTML = sessions.map(s => {{
        const sid = s.session_id;
        return '<tr class="clickable" onclick="window._nav(2,\\'' + sid + '\\')">'
          + '<td><code>' + escapeHtml(sid.slice(0,8)) + '...</code></td>'
          + '<td>' + escapeHtml(fmtTime(s.timestamp) || '?') + '</td>'
          + '<td class="num">' + fmtNum(s.total_tokens) + '</td>'
          + '<td class="num">' + fmtNum(s.total_max_context_fill) + '</td>'
          + '<td>' + (s.agent_count || 0) + '</td>'
          + '</tr>';
      }}).join('');
    }}

    // History table (accordion)
    const htbody = document.getElementById('history-table-body');
    if (!ov.history || ov.history.length === 0) {{
      htbody.innerHTML = '<tr><td colspan="5" class="empty">No execution history</td></tr>';
    }} else {{
      htbody.innerHTML = ov.history.map((r, idx) => {{
        const badge = r.success
          ? '<span class="badge success">OK</span>'
          : '<span class="badge fail">FAIL</span>';

        // Main row (clickable accordion trigger)
        let html = '<tr class="accordion-row" id="hrow-' + idx + '" onclick="window._toggleHistory(' + idx + ')">'
          + '<td>' + escapeHtml(r.timestamp) + '</td>'
          + '<td>' + escapeHtml(r.request) + '</td>'
          + '<td>' + badge + '</td>'
          + '<td>' + escapeHtml(r.summary) + '</td>'
          + '<td>' + (r.changes_count || 0) + '</td>'
          + '</tr>';

        // Accordion panel row
        html += '<tr class="accordion-panel" id="hpanel-' + idx + '">'
          + '<td colspan="5"><div class="accordion-content">';

        // (a) Changes table
        const changes = r.changes || [];
        if (changes.length > 0) {{
          html += '<h4>Changes</h4><table><thead><tr><th>Path</th><th>Type</th><th>Intent</th></tr></thead><tbody>';
          changes.forEach(c => {{
            const cType = (c.type || 'modified').toLowerCase();
            let badgeClass = 'modified';
            if (cType === 'created' || cType === 'create') badgeClass = 'created';
            else if (cType === 'deleted' || cType === 'delete') badgeClass = 'deleted';
            html += '<tr>'
              + '<td><code>' + escapeHtml(c.path || '') + '</code></td>'
              + '<td><span class="badge ' + badgeClass + '">' + escapeHtml(cType) + '</span></td>'
              + '<td>' + escapeHtml(c.intent || '') + '</td>'
              + '</tr>';
          }});
          html += '</tbody></table>';
        }} else {{
          html += '<h4>Changes</h4><span style="color:#666;font-style:italic">No file changes</span>';
        }}

        // (b) Tool Usage
        const toolUsage = r.tool_usage || {{}};
        const toolEntries = Object.entries(toolUsage).sort((a, b) => b[1] - a[1]);
        if (toolEntries.length > 0) {{
          html += '<h4>Tool Usage</h4><div class="tool-list">';
          const topTools = toolEntries.slice(0, 10);
          topTools.forEach(([name, count]) => {{
            html += '<span class="tool-chip">' + escapeHtml(name) + '<span class="tool-count">' + count + '</span></span>';
          }});
          if (toolEntries.length > 10) {{
            const remaining = toolEntries.length - 10;
            html += '<span class="tool-chip" style="color:#666">+' + remaining + ' more</span>';
          }}
          html += '</div>';
        }}

        // (c) Session link + (d) Token/Context summary
        if (r.matched_session_id) {{
          html += '<div class="meta-row">';
          html += '<span><span class="label">Session:</span> <a class="session-link" onclick="event.stopPropagation();window._nav(2,\\'' + r.matched_session_id + '\\')">' + r.matched_session_id.slice(0, 8) + '... &rarr;</a></span>';
          if (r.session_tokens) {{
            html += '<span><span class="label">Tokens:</span> ' + fmtNum(r.session_tokens) + '</span>';
          }}
          if (r.session_max_context) {{
            html += '<span><span class="label">Max Context:</span> ' + fmtNum(r.session_max_context) + '</span>';
          }}
          html += '</div>';
        }}

        // (e) Error
        if (r.error) {{
          html += '<div class="error-box">' + escapeHtml(r.error) + '</div>';
        }}

        html += '</div></td></tr>';
        return html;
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
  // Level 2: Session Detail
  // -----------------------------------------------------------------------
  function renderSessionDetail(sessionId) {{
    const sess = getSession(sessionId);
    if (!sess) {{
      document.getElementById('gantt-container').innerHTML = '<div class="empty-msg">Session not found</div>';
      return;
    }}

    const agents = sess.agents || [];
    const timedAgents = agents.filter(a => a.start_time);
    const untimedAgents = agents.filter(a => !a.start_time);

    // --- Gantt chart ---
    const ganttEl = document.getElementById('gantt-container');
    if (timedAgents.length === 0) {{
      ganttEl.innerHTML = '<div class="empty-msg">No timeline data available</div>';
    }} else {{
      // Compute time range
      let minTime = Infinity, maxTime = -Infinity;
      timedAgents.forEach(a => {{
        const st = new Date(a.start_time).getTime();
        const et = a.end_time ? new Date(a.end_time).getTime() : st;
        if (st < minTime) minTime = st;
        if (et > maxTime) maxTime = et;
      }});
      const totalRange = maxTime - minTime || 1;

      let ganttHtml = '';
      timedAgents.sort((a, b) => (a.start_time || '').localeCompare(b.start_time || ''));
      timedAgents.forEach(a => {{
        const st = new Date(a.start_time).getTime();
        const et = a.end_time ? new Date(a.end_time).getTime() : maxTime;
        const left = ((st - minTime) / totalRange * 100).toFixed(2);
        const width = Math.max(((et - st) / totalRange * 100), 0.5).toFixed(2);
        const color = getTypeColor(a.agent_type);
        const durStr = fmtDuration(a.duration_ms);
        const label = a.agent_type + ' (' + a.agent_id_short + ')';

        ganttHtml += '<div class="gantt-row" onclick="window._nav(3,\\'' + sessionId + '\\',\\'' + a.agent_id + '\\')">'
          + '<div class="gantt-label">' + escapeHtml(label) + '</div>'
          + '<div class="gantt-track">'
          + '<div class="gantt-bar" style="left:' + left + '%;width:' + width + '%;background:' + color + '">'
          + durStr
          + '</div></div></div>';
      }});

      // Time axis
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
    const allAgents = [...timedAgents, ...untimedAgents];
    if (allAgents.length === 0) {{
      atbody.innerHTML = '<tr><td colspan="8" class="empty">No agent data</td></tr>';
    }} else {{
      atbody.innerHTML = allAgents.map((a, i) => {{
        return '<tr class="clickable" onclick="window._nav(3,\\'' + sessionId + '\\',\\'' + a.agent_id + '\\')">'
          + '<td>' + (a.order || (i + 1)) + '</td>'
          + '<td><span style="color:' + getTypeColor(a.agent_type) + '">' + escapeHtml(a.agent_type) + '</span></td>'
          + '<td><code>' + escapeHtml(a.agent_id_short) + '</code></td>'
          + '<td>' + fmtTime(a.start_time) + '</td>'
          + '<td>' + fmtDuration(a.duration_ms) + '</td>'
          + '<td class="num">' + fmtNum(a.total_tokens) + '</td>'
          + '<td class="num">' + fmtNum(a.max_context_fill) + '</td>'
          + '<td>' + (a.compaction_count || 0) + '</td>'
          + '</tr>';
      }}).join('');
    }}

    // --- Session token donut ---
    const donutData = allAgents.filter(a => a.total_tokens > 0);
    if (donutData.length > 0) {{
      charts.push(new Chart(document.getElementById('sessionDonut'), {{
        type: 'doughnut',
        data: {{
          labels: donutData.map(a => a.agent_type + ' (' + a.agent_id_short + ')'),
          datasets: [{{
            data: donutData.map(a => a.total_tokens),
            backgroundColor: donutData.map(a => getTypeColor(a.agent_type)),
            borderWidth: 0,
          }}],
        }},
        options: {{
          responsive: true,
          plugins: {{ legend: {{ position: 'right', labels: {{ color: '#ccc', font: {{ size: 11 }} }} }} }},
        }},
      }}));
    }} else {{
      document.getElementById('sessionDonutWrap').innerHTML = '<div class="empty-msg">No token data</div>';
    }}

    // --- Agent type bar chart ---
    const typeMap = {{}};
    allAgents.forEach(a => {{
      const t = a.agent_type;
      typeMap[t] = (typeMap[t] || 0) + (a.total_tokens || 0);
    }});
    const typeKeys = Object.keys(typeMap).filter(k => typeMap[k] > 0);
    if (typeKeys.length > 0) {{
      charts.push(new Chart(document.getElementById('sessionTypeBar'), {{
        type: 'bar',
        data: {{
          labels: typeKeys,
          datasets: [{{
            label: 'Total Tokens',
            data: typeKeys.map(k => typeMap[k]),
            backgroundColor: typeKeys.map(k => getTypeColor(k)),
          }}],
        }},
        options: {{
          responsive: true,
          indexAxis: 'y',
          plugins: {{ legend: {{ display: false }} }},
          scales: {{
            x: {{ ticks: {{ color: '#999' }}, grid: {{ color: '#2a2a4a' }} }},
            y: {{ ticks: {{ color: '#ccc' }}, grid: {{ color: '#2a2a4a' }} }},
          }},
        }},
      }}));
    }} else {{
      document.getElementById('sessionTypeBarWrap').innerHTML = '<div class="empty-msg">No token data</div>';
    }}
  }}

  // -----------------------------------------------------------------------
  // Level 3: Agent Detail
  // -----------------------------------------------------------------------
  function renderAgentDetail(sessionId, agentId) {{
    const agent = getAgent(sessionId, agentId);
    if (!agent) {{
      document.getElementById('agent-summary').innerHTML = '<div class="empty-msg">Agent not found</div>';
      return;
    }}

    // --- Summary card ---
    const summaryEl = document.getElementById('agent-summary');
    summaryEl.innerHTML = '<table>'
      + '<tr><th>Agent Type</th><td><span style="color:' + getTypeColor(agent.agent_type) + '">' + escapeHtml(agent.agent_type) + '</span></td></tr>'
      + '<tr><th>Agent ID</th><td><code>' + escapeHtml(agent.agent_id) + '</code></td></tr>'
      + '<tr><th>Start</th><td>' + fmtTime(agent.start_time) + '</td></tr>'
      + '<tr><th>Duration</th><td>' + fmtDuration(agent.duration_ms) + '</td></tr>'
      + '<tr><th>Total Tokens</th><td class="num">' + fmtNum(agent.total_tokens) + '</td></tr>'
      + '<tr><th>Turns</th><td>' + (agent.turn_count || (agent.turns || []).length || 0) + '</td></tr>'
      + '<tr><th>Max Context Fill</th><td class="num">' + fmtNum(agent.max_context_fill) + '</td></tr>'
      + '<tr><th>Compactions</th><td>' + (agent.compaction_count || 0) + '</td></tr>'
      + '</table>';

    // --- Token breakdown bar chart ---
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
