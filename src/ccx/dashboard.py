"""
Dashboard for ccx usage metrics.
Aggregates token usage, context window usage, and execution history,
then renders a single-page HTML report with Chart.js visualizations.

Public API:
    aggregate_data(project_dir, limit=50) -> dict
    generate_html(project_dir, limit=50) -> str
"""

import json
from datetime import datetime

from ccx.token_tracker import list_session_usages, get_session_usage
from ccx.context_tracker import list_context_usages, get_context_usage
from ccx.session import load_session


# ---------------------------------------------------------------------------
# Data aggregation
# ---------------------------------------------------------------------------

def aggregate_data(project_dir: str, limit: int = 50) -> dict:
    """Collect all dashboard data from token, context, and session sources.

    Returns a dict with three top-level keys:
        token    - session list + per-session agent details
        context  - session list + per-session turn-level details
        history  - execution records
    """
    # --- Token usage ---
    token_list = list_session_usages(project_dir, limit=limit)
    token_sessions = token_list.get("sessions", [])

    token_details: list[dict] = []
    for sess in token_sessions:
        sid = sess.get("session_id", "")
        detail = get_session_usage(project_dir, sid)
        if detail.get("status") == "ok":
            token_details.append(detail)

    # Agent-type aggregation across all sessions
    agent_type_totals: dict[str, int] = {}
    for detail in token_details:
        for agent in detail.get("agents", []):
            atype = agent.get("agent_type", "unknown")
            agent_type_totals[atype] = (
                agent_type_totals.get(atype, 0) + agent.get("total_tokens", 0)
            )

    # --- Context usage ---
    context_list = list_context_usages(project_dir, limit=limit)
    context_sessions = context_list.get("sessions", [])

    context_details: list[dict] = []
    for sess in context_sessions:
        sid = sess.get("session_id", "")
        detail = get_context_usage(project_dir, sid)
        if detail.get("status") == "ok":
            context_details.append(detail)

    # --- Execution history ---
    history = load_session(project_dir, limit=limit)

    return {
        "token": {
            "sessions": token_sessions,
            "details": token_details,
            "agent_type_totals": agent_type_totals,
        },
        "context": {
            "sessions": context_sessions,
            "details": context_details,
        },
        "history": history,
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
        dt = datetime.fromisoformat(iso_str)
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
    """Generate a self-contained HTML dashboard page.

    The returned string is a complete HTML document with inline CSS
    and Chart.js loaded from CDN.  Data is injected as JSON literals
    inside ``<script>`` tags.

    Args:
        project_dir: Project root directory path.
        limit: Maximum number of sessions/records to include.

    Returns:
        HTML string.
    """
    data = aggregate_data(project_dir, limit=limit)

    # --- Prepare chart data ---

    # (a) Token stacked bar chart data
    # Sessions are sorted newest-first from the API; reverse for chronological X axis
    token_sessions = list(reversed(data["token"]["sessions"]))
    token_labels = json.dumps(
        [_format_date(s.get("timestamp", "")) for s in token_sessions]
    )
    token_input = json.dumps(
        [s.get("total_input_tokens", 0) for s in token_sessions]
    )
    token_cache_create = json.dumps(
        [s.get("total_cache_creation_input_tokens", 0) for s in token_sessions]
    )
    token_cache_read = json.dumps(
        [s.get("total_cache_read_input_tokens", 0) for s in token_sessions]
    )
    token_output = json.dumps(
        [s.get("total_output_tokens", 0) for s in token_sessions]
    )

    # (b) Token donut chart data — agent_type breakdown
    agent_totals = data["token"]["agent_type_totals"]
    donut_labels = json.dumps(list(agent_totals.keys()))
    donut_values = json.dumps(list(agent_totals.values()))

    # (c) Context fill line chart — per-agent turn series
    #     Build datasets: one line per agent across all sessions
    context_datasets: list[dict] = []
    palette = [
        "#4fc3f7", "#81c784", "#ffb74d", "#e57373",
        "#ba68c8", "#4dd0e1", "#aed581", "#ff8a65",
        "#f06292", "#7986cb",
    ]
    for detail in data["context"]["details"]:
        session_ts = _format_date(detail.get("timestamp", ""))
        for idx, agent in enumerate(detail.get("agents", [])):
            atype = agent.get("agent_type", "unknown")
            agent_id = agent.get("agent_id", "")
            label = f"{session_ts} / {atype} ({agent_id})"
            turns = agent.get("turns", [])
            fills = [t.get("context_fill", 0) for t in turns]
            compaction_points = agent.get("compaction_points", [])
            color = palette[idx % len(palette)]
            context_datasets.append({
                "label": label,
                "data": fills,
                "borderColor": color,
                "backgroundColor": color + "33",
                "tension": 0.3,
                "fill": False,
                "pointRadius": 2,
                "compaction_points": compaction_points,
            })
    context_datasets_json = json.dumps(context_datasets)

    # (d) Session history table rows
    history = data["history"]
    history_rows = ""
    if history:
        for rec in reversed(history):
            ts = _format_date(rec.get("timestamp", ""))
            req = _escape_html(rec.get("request", "")[:120])
            success = rec.get("success", False)
            badge = (
                '<span class="badge success">OK</span>'
                if success
                else '<span class="badge fail">FAIL</span>'
            )
            summary = _escape_html(rec.get("summary", rec.get("error", ""))[:200])
            changes = rec.get("changes", [])
            changes_count = len(changes) if changes else 0
            history_rows += (
                f"<tr>"
                f"<td>{ts}</td>"
                f"<td>{req}</td>"
                f"<td>{badge}</td>"
                f"<td>{summary}</td>"
                f"<td>{changes_count}</td>"
                f"</tr>\n"
            )
    else:
        history_rows = (
            '<tr><td colspan="5" class="empty">No execution history</td></tr>'
        )

    # Empty-state flags
    has_token_data = "true" if token_sessions else "false"
    has_donut_data = "true" if agent_totals else "false"
    has_context_data = "true" if context_datasets else "false"

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
    margin-bottom: 24px;
    font-size: 1.6rem;
    color: #4fc3f7;
    letter-spacing: 0.05em;
  }}
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
</style>
</head>
<body>
<h1>ccx Dashboard</h1>
<div class="grid">

  <!-- (a) Token Usage Stacked Bar -->
  <div class="card">
    <h2>Token Usage per Session</h2>
    <div id="tokenBarWrap">
      <canvas id="tokenBar"></canvas>
    </div>
  </div>

  <!-- (b) Token Breakdown Donut -->
  <div class="card">
    <h2>Token Breakdown by Agent Type</h2>
    <div id="donutWrap">
      <canvas id="tokenDonut"></canvas>
    </div>
  </div>

  <!-- (c) Context Fill Line Chart -->
  <div class="card full">
    <h2>Context Window Fill over Turns</h2>
    <div id="contextWrap">
      <canvas id="contextLine"></canvas>
    </div>
  </div>

  <!-- (d) Session History Table -->
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
        <tbody>
          {history_rows}
        </tbody>
      </table>
    </div>
  </div>

</div>

<script>
(function() {{
  const hasTokenData = {has_token_data};
  const hasDonutData = {has_donut_data};
  const hasContextData = {has_context_data};

  const emptyMsg = (id) => {{
    const el = document.getElementById(id);
    el.innerHTML = '<div class="empty-msg">No data available</div>';
  }};

  // --- (a) Token Stacked Bar ---
  if (!hasTokenData) {{
    emptyMsg('tokenBarWrap');
  }} else {{
    new Chart(document.getElementById('tokenBar'), {{
      type: 'bar',
      data: {{
        labels: {token_labels},
        datasets: [
          {{
            label: 'Input',
            data: {token_input},
            backgroundColor: '#4fc3f7',
          }},
          {{
            label: 'Cache Create',
            data: {token_cache_create},
            backgroundColor: '#ffb74d',
          }},
          {{
            label: 'Cache Read',
            data: {token_cache_read},
            backgroundColor: '#81c784',
          }},
          {{
            label: 'Output',
            data: {token_output},
            backgroundColor: '#e57373',
          }},
        ],
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ labels: {{ color: '#ccc' }} }},
        }},
        scales: {{
          x: {{
            stacked: true,
            ticks: {{ color: '#999', maxRotation: 45 }},
            grid: {{ color: '#2a2a4a' }},
          }},
          y: {{
            stacked: true,
            ticks: {{ color: '#999' }},
            grid: {{ color: '#2a2a4a' }},
          }},
        }},
      }},
    }});
  }}

  // --- (b) Token Donut ---
  if (!hasDonutData) {{
    emptyMsg('donutWrap');
  }} else {{
    const donutColors = [
      '#4fc3f7', '#81c784', '#ffb74d', '#e57373',
      '#ba68c8', '#4dd0e1', '#aed581', '#ff8a65',
      '#f06292', '#7986cb',
    ];
    new Chart(document.getElementById('tokenDonut'), {{
      type: 'doughnut',
      data: {{
        labels: {donut_labels},
        datasets: [{{
          data: {donut_values},
          backgroundColor: donutColors.slice(0, {len(agent_totals)}),
          borderWidth: 0,
        }}],
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ position: 'right', labels: {{ color: '#ccc' }} }},
        }},
      }},
    }});
  }}

  // --- (c) Context Fill Line ---
  if (!hasContextData) {{
    emptyMsg('contextWrap');
  }} else {{
    const ctxDatasets = {context_datasets_json};

    // Find the longest turn series for X axis labels
    let maxTurns = 0;
    ctxDatasets.forEach(ds => {{ if (ds.data.length > maxTurns) maxTurns = ds.data.length; }});
    const turnLabels = Array.from({{ length: maxTurns }}, (_, i) => 'T' + i);

    // Build annotation lines for compaction events
    const compactionAnnotations = {{}};
    ctxDatasets.forEach((ds, dsIdx) => {{
      (ds.compaction_points || []).forEach(pt => {{
        const key = 'comp_' + dsIdx + '_' + pt;
        compactionAnnotations[key] = {{
          type: 'line',
          xMin: pt,
          xMax: pt,
          borderColor: '#e57373',
          borderWidth: 1,
          borderDash: [4, 4],
          label: {{
            display: false,
          }},
        }};
      }});
    }});

    // Remove compaction_points from datasets (not a Chart.js property)
    ctxDatasets.forEach(ds => {{ delete ds.compaction_points; }});

    const ctxConfig = {{
      type: 'line',
      data: {{
        labels: turnLabels,
        datasets: ctxDatasets,
      }},
      options: {{
        responsive: true,
        interaction: {{
          mode: 'nearest',
          intersect: false,
        }},
        plugins: {{
          legend: {{ labels: {{ color: '#ccc', font: {{ size: 11 }} }} }},
          annotation: Object.keys(compactionAnnotations).length > 0
            ? {{ annotations: compactionAnnotations }}
            : undefined,
        }},
        scales: {{
          x: {{
            ticks: {{ color: '#999' }},
            grid: {{ color: '#2a2a4a' }},
            title: {{ display: true, text: 'Turn', color: '#999' }},
          }},
          y: {{
            ticks: {{ color: '#999' }},
            grid: {{ color: '#2a2a4a' }},
            title: {{ display: true, text: 'Context Fill (tokens)', color: '#999' }},
          }},
        }},
      }},
    }};

    new Chart(document.getElementById('contextLine'), ctxConfig);
  }}
}})();
</script>
</body>
</html>"""

    return html
