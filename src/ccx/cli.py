"""
Setup CLI for ccx.
Skills, hooks, and MCP configuration are handled by the plugin system
(claude plugin install). This CLI manages base-context.yaml and .ccx/ directory.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import click

from ccx.storage import resolve_storage_dir

GIT_REPO_URL = "git+https://github.com/chanhyeok-made/ccx.git"


@click.group()
@click.version_option(package_name="ccx")
def cli():
    """ccx — Claude Code eXtension setup tool."""
    pass


@cli.command()
@click.argument("project_dir", default=".", type=click.Path(exists=True))
@click.option("--force", "-f", is_flag=True, help="Overwrite existing files")
def init(project_dir: str, force: bool):
    """Initialize ccx in a project directory.

    Creates base-context.yaml, .ccx/ directory, and configures tool
    permissions in .claude/settings.local.json so that all ccx tools
    are auto-approved.
    Skills, hooks, and MCP config are managed by the plugin system.
    """
    project = Path(project_dir).resolve()

    # 1. Create base-context.yaml by scanning project
    _create_base_context_starter(project, force)

    # 2. Create .ccx/ directory and logs subdirectory
    ccx_dir = _ensure_ccx_directory(project)

    # 3. Configure tool permissions in .claude/settings.local.json
    _ensure_permissions_settings(project, force)

    click.echo("\nccx initialized successfully!")
    click.echo(f"  Base context:  {project / 'base-context.yaml'}")
    click.echo(f"  Session dir:   {ccx_dir}")
    click.echo(f"  Permissions:   {project / '.claude' / 'settings.local.json'}")
    click.echo("\nNext steps:")
    click.echo("  1. Edit base-context.yaml to describe your project")
    click.echo("  2. Run 'claude plugin install' to set up skills, hooks, and MCP")
    click.echo("  3. Start Claude Code and use /ccx:run [request]")


@cli.command()
@click.argument("project_dir", default=".", type=click.Path(exists=True))
def update(project_dir: str):
    """Upgrade ccx package to latest version.

    Skills, hooks, and MCP config are updated via the plugin system.
    """
    project = Path(project_dir).resolve()

    from ccx import __version__ as current_version
    click.echo(f"Current version: {current_version}")
    click.echo("Upgrading ccx package...")

    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install",
             "--no-cache-dir", "--force-reinstall", "-q",
             GIT_REPO_URL],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        click.echo(f"Package upgrade failed:\n{e.stderr}", err=True)
        sys.exit(1)

    _ensure_ccx_directory(project)

    click.echo(f"\nccx package upgraded successfully!")

    # Update the Claude Code plugin (skills, hooks, MCP config)
    click.echo("Updating Claude Code plugin...")
    try:
        subprocess.run(
            ["claude", "plugin", "update", "ccx@chanhyeok-plugins"],
            check=True, capture_output=True, text=True,
        )
        click.echo("Plugin updated successfully.")
    except subprocess.CalledProcessError as e:
        click.echo(
            f"Warning: Plugin update failed:\n{e.stderr}\n"
            "You can manually run: claude plugin update ccx@chanhyeok-plugins",
            err=True,
        )
    except FileNotFoundError:
        click.echo(
            "Warning: 'claude' CLI not found. "
            "Manually run: claude plugin update ccx@chanhyeok-plugins",
            err=True,
        )


@cli.command()
@click.argument("project_dir", default=".", type=click.Path(exists=True))
def status(project_dir: str):
    """Check ccx installation status."""
    project = Path(project_dir).resolve()
    storage_root = Path(resolve_storage_dir(str(project)))

    checks = {
        "base-context.yaml": (project / "base-context.yaml").exists(),
        ".ccx/": (storage_root / ".ccx").is_dir(),
        ".claude-plugin/plugin.json": (project / ".claude-plugin" / "plugin.json").exists(),
    }

    click.echo(f"ccx status for: {project}\n")
    all_ok = True
    for name, ok in checks.items():
        icon = "OK" if ok else "MISSING"
        click.echo(f"  [{icon}] {name}")
        if not ok:
            all_ok = False

    # Analysis cache info
    cache_dir = storage_root / ".ccx" / "cache" / "scopes"
    if cache_dir.exists():
        scope_files = list(cache_dir.rglob("_scope.json"))
        click.echo(f"  Analysis cache: {len(scope_files)} scope(s) cached (directory-based)")
    else:
        # Check for legacy flat file
        legacy = storage_root / ".ccx" / "analysis-cache.json"
        if legacy.exists():
            click.echo("  Analysis cache: legacy format (will migrate on next use)")
        else:
            click.echo("  Analysis cache: not initialized (run 'ccx index')")

    if all_ok:
        click.echo("\nAll components installed.")
    else:
        click.echo("\nSome components missing. Run 'ccx init' and 'claude plugin install'.")


@cli.command()
@click.argument("project_dir", default=".", type=click.Path(exists=True))
@click.option("--reset", is_flag=True, help="Reset analysis cache before indexing")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed scope information")
def index(project_dir: str, reset: bool, verbose: bool):
    """Discover and index project scopes for analysis caching."""
    from ccx.analysis_cache import build_scope_tree

    project = Path(project_dir).resolve()
    storage_root = Path(resolve_storage_dir(str(project)))
    ccx_dir = storage_root / ".ccx"

    if not ccx_dir.exists():
        click.echo("Error: ccx is not initialized. Run 'ccx init' first.", err=True)
        sys.exit(1)

    if reset:
        cache_subdir = ccx_dir / "cache"
        if cache_subdir.exists():
            shutil.rmtree(cache_subdir)
            click.echo("Analysis cache reset.")
        # Also clean up legacy file if present
        legacy_file = ccx_dir / "analysis-cache.json"
        if legacy_file.exists():
            legacy_file.unlink()
            click.echo("Legacy cache file removed.")

    click.echo(f"Indexing project: {project}")

    # Build scope tree
    result = build_scope_tree(str(project))

    click.echo(f"\nDiscovered {result['total_scopes']} scopes:")
    click.echo(f"  Packages: {result['packages']}")
    click.echo(f"  Modules:  {result['modules']}")

    new_count = result["new_scope_count"]
    stale_count = result["stale_scope_count"]

    if new_count:
        click.echo(f"\n  New scopes:   {new_count}")
    if stale_count:
        click.echo(f"  Orphan scopes cleaned: {stale_count}")

    if verbose:
        from ccx.analysis_cache import _load_meta
        meta = _load_meta(str(project))
        scope_tree = meta.get("scope_tree", {})
        if scope_tree:
            click.echo("\nScope tree:")
            for parent, children in sorted(scope_tree.items()):
                click.echo(f"  {parent}/")
                for child in children:
                    click.echo(f"    ├── {child}")

    click.echo("\nDone. Run '/ccx:run' or '/ccx:analyze' to trigger code-level analysis.")


@cli.command()
@click.argument("project_dir", default=".", type=click.Path(exists=True))
@click.option("--limit", "-n", default=10, show_default=True, help="Number of recent sessions to show")
@click.option("--detail", "-d", default=None, help="Show per-agent breakdown for a session ID")
def usage(project_dir: str, limit: int, detail: str):
    """Show token usage statistics for recent sessions."""
    from ccx.token_tracker import list_session_usages

    project = Path(project_dir).resolve()
    storage_root = Path(resolve_storage_dir(str(project)))
    ccx_dir = storage_root / ".ccx"

    if not ccx_dir.exists():
        click.echo("Error: ccx is not initialized. Run 'ccx init' first.", err=True)
        sys.exit(1)

    if detail:
        _show_session_detail(str(storage_root), detail)
        return

    result = list_session_usages(str(storage_root), limit=limit)
    sessions = result.get("sessions", [])

    if not sessions:
        click.echo("No token usage data found.")
        click.echo("Usage data is recorded automatically during /ccx:run sessions.")
        return

    click.echo(f"Token usage for: {project}")
    click.echo(f"Showing {len(sessions)} most recent session(s)\n")

    # Column definitions: (header, key, width, formatter)
    columns = [
        ("Session ID",    "session_id",    12, lambda v: v[:12] if len(v) > 12 else v),
        ("Timestamp",     "timestamp",     19, lambda v: v[:19].replace("T", " ") if v else "-"),
        ("Agents",        "agent_count",    6, lambda v: str(v)),
        ("Input",         "total_input_tokens",         10, _format_tokens),
        ("Cache Create",  "total_cache_creation_input_tokens", 13, _format_tokens),
        ("Cache Read",    "total_cache_read_input_tokens",     10, _format_tokens),
        ("Output",        "total_output_tokens",        10, _format_tokens),
        ("Total",         "total_tokens",               10, _format_tokens),
        ("Trend",         "_trend",                     10, lambda v: v),
    ]

    # Pre-compute per-session sparkline character (total_tokens across sessions)
    total_values = [s.get("total_tokens", 0) for s in sessions]
    trend_chars = _sparkline(total_values) if total_values else ""
    for i, s in enumerate(sessions):
        s["_trend"] = trend_chars[i] if i < len(trend_chars) else " "

    # Header
    header = "  ".join(h.ljust(w) for h, _, w, _ in columns)
    click.echo(header)
    click.echo("-" * len(header))

    # Rows
    for s in sessions:
        row = "  ".join(
            fmt(s.get(key, 0)).rjust(w) if key not in ("session_id", "timestamp", "_trend") else fmt(s.get(key, "")).ljust(w)
            for _, key, w, fmt in columns
        )
        click.echo(row)

    # Grand totals
    click.echo("-" * len(header))
    totals = {
        "total_input_tokens": sum(s.get("total_input_tokens", 0) for s in sessions),
        "total_cache_creation_input_tokens": sum(s.get("total_cache_creation_input_tokens", 0) for s in sessions),
        "total_cache_read_input_tokens": sum(s.get("total_cache_read_input_tokens", 0) for s in sessions),
        "total_output_tokens": sum(s.get("total_output_tokens", 0) for s in sessions),
        "total_tokens": sum(s.get("total_tokens", 0) for s in sessions),
    }
    total_row_parts = []
    for h, key, w, fmt in columns:
        if key == "session_id":
            total_row_parts.append("TOTAL".ljust(w))
        elif key in ("timestamp", "_trend"):
            total_row_parts.append("".ljust(w))
        elif key == "agent_count":
            total_row_parts.append("".rjust(w))
        else:
            total_row_parts.append(fmt(totals.get(key, 0)).rjust(w))
    click.echo("  ".join(total_row_parts))

    click.echo(f"\nUse 'ccx usage --detail <session-id>' for per-agent breakdown.")


def _show_session_detail(project_dir: str, session_id: str):
    """Display per-agent token breakdown for a single session."""
    from ccx.token_tracker import get_session_usage

    data = get_session_usage(project_dir, session_id)
    if data.get("status") != "ok":
        click.echo(f"Session not found: {session_id}", err=True)
        sys.exit(1)

    click.echo(f"Session: {data['session_id']}")
    click.echo(f"Time:    {data.get('timestamp', '-')}")
    click.echo("")

    agents = data.get("agents", [])
    if not agents:
        click.echo("No agent data recorded.")
        return

    # Agent table
    columns = [
        ("Agent ID",       "agent_id",                    20, lambda v: v[:20] if len(v) > 20 else v),
        ("Type",           "agent_type",                  12, lambda v: v),
        ("Turns",          "turn_count",                   5, lambda v: str(v)),
        ("Input",          "input_tokens",                10, _format_tokens),
        ("Cache Create",   "cache_creation_input_tokens", 13, _format_tokens),
        ("Cache Read",     "cache_read_input_tokens",     10, _format_tokens),
        ("Output",         "output_tokens",               10, _format_tokens),
        ("Total",          "total_tokens",                10, _format_tokens),
    ]

    header = "  ".join(h.ljust(w) for h, _, w, _ in columns)
    click.echo(header)
    click.echo("-" * len(header))

    for agent in agents:
        parts = []
        for _, key, w, fmt in columns:
            val = agent.get(key, 0)
            if key in ("agent_id", "agent_type"):
                parts.append(fmt(str(val)).ljust(w))
            else:
                parts.append(fmt(val).rjust(w))
        click.echo("  ".join(parts))

    # Session totals
    click.echo("-" * len(header))
    click.echo(
        f"  Total: input={_format_tokens(data.get('total_input_tokens', 0))}"
        f"  cache_create={_format_tokens(data.get('total_cache_creation_input_tokens', 0))}"
        f"  cache_read={_format_tokens(data.get('total_cache_read_input_tokens', 0))}"
        f"  output={_format_tokens(data.get('total_output_tokens', 0))}"
        f"  total={_format_tokens(data.get('total_tokens', 0))}"
    )


@cli.command()
@click.argument("project_dir", default=".", type=click.Path(exists=True))
@click.option("--limit", "-n", default=10, show_default=True, help="Number of recent sessions to show")
@click.option("--detail", "-d", default=None, help="Show per-agent context breakdown for a session ID")
def context(project_dir: str, limit: int, detail: str):
    """Show context window usage statistics for recent sessions."""
    from ccx.context_tracker import list_context_usages, get_context_usage

    project = Path(project_dir).resolve()
    storage_root = Path(resolve_storage_dir(str(project)))
    ccx_dir = storage_root / ".ccx"

    if not ccx_dir.exists():
        click.echo("Error: ccx is not initialized. Run 'ccx init' first.", err=True)
        sys.exit(1)

    if detail:
        _show_context_detail(str(storage_root), detail)
        return

    result = list_context_usages(str(storage_root), limit=limit)
    sessions = result.get("sessions", [])

    if not sessions:
        click.echo("No context usage data found.")
        click.echo("Context data is recorded automatically during /ccx:run sessions.")
        return

    click.echo(f"Context window usage for: {project}")
    click.echo(f"Showing {len(sessions)} most recent session(s)\n")

    columns = [
        ("Session ID",   "session_id",            12, lambda v: v[:12] if len(v) > 12 else v),
        ("Timestamp",    "timestamp",             19, lambda v: v[:19].replace("T", " ") if v else "-"),
        ("Agents",       "agent_count",            6, lambda v: str(v)),
        ("Max Fill",     "total_max_context_fill", 10, _format_tokens),
        ("Avg Fill",     "avg_context_fill",       10, _format_tokens),
        ("Final Fill",   "final_context_fill",     10, _format_tokens),
        ("Compactions",  "total_compaction_count",  11, lambda v: str(v)),
        ("Trend",        "_trend",                 10, lambda v: v),
    ]

    # Pre-compute per-session sparkline character (max context fill across sessions)
    fill_values = [s.get("total_max_context_fill", 0) for s in sessions]
    trend_chars = _sparkline(fill_values) if fill_values else ""
    for i, s in enumerate(sessions):
        s["_trend"] = trend_chars[i] if i < len(trend_chars) else " "

    header = "  ".join(h.ljust(w) for h, _, w, _ in columns)
    click.echo(header)
    click.echo("-" * len(header))

    for s in sessions:
        row = "  ".join(
            fmt(s.get(key, 0)).rjust(w)
            if key not in ("session_id", "timestamp", "_trend")
            else fmt(s.get(key, "")).ljust(w)
            for _, key, w, fmt in columns
        )
        click.echo(row)

    click.echo(f"\nUse 'ccx context --detail <session-id>' for per-agent breakdown.")


def _show_context_detail(project_dir: str, session_id: str):
    """Display per-agent context window breakdown for a single session."""
    from ccx.context_tracker import get_context_usage

    data = get_context_usage(project_dir, session_id)
    if data.get("status") != "ok":
        click.echo(f"Session not found: {session_id}", err=True)
        sys.exit(1)

    click.echo(f"Session: {data['session_id']}")
    click.echo(f"Time:    {data.get('timestamp', '-')}")
    click.echo("")

    agents = data.get("agents", [])
    if not agents:
        click.echo("No agent data recorded.")
        return

    columns = [
        ("Agent ID",     "agent_id",            20, lambda v: v[:20] if len(v) > 20 else v),
        ("Type",         "agent_type",          12, lambda v: v),
        ("Turns",        "turns",                5, lambda v: str(len(v)) if isinstance(v, list) else str(v)),
        ("Max Fill",     "max_context_fill",    10, _format_tokens),
        ("Avg Fill",     "avg_context_fill",    10, _format_tokens),
        ("Final Fill",   "final_context_fill",  10, _format_tokens),
        ("Compactions",  "compaction_count",    11, lambda v: str(v)),
    ]

    header = "  ".join(h.ljust(w) for h, _, w, _ in columns)
    click.echo(header)
    click.echo("-" * len(header))

    for agent in agents:
        parts = []
        for _, key, w, fmt in columns:
            val = agent.get(key, 0)
            if key in ("agent_id", "agent_type"):
                parts.append(fmt(str(val)).ljust(w))
            else:
                parts.append(fmt(val).rjust(w))
        click.echo("  ".join(parts))

    # Context fill trend per agent (sparkline)
    has_turns = any(a.get("turns") for a in agents)
    if has_turns:
        click.echo("")
        click.echo("Context fill trend:")
        for agent in agents:
            turns = agent.get("turns", [])
            if turns:
                fills = [t.get("context_fill", 0) for t in turns]
                agent_id = agent.get("agent_id", "unknown")[:20]
                spark = _sparkline(fills)
                click.echo(f"  {agent_id}: {spark}")

    # Compaction detail per agent
    has_compactions = any(a.get("compaction_points") for a in agents)
    if has_compactions:
        click.echo("")
        click.echo("Compaction points:")
        for agent in agents:
            points = agent.get("compaction_points", [])
            if points:
                agent_id = agent.get("agent_id", "unknown")
                click.echo(f"  {agent_id}: turn(s) {', '.join(str(p) for p in points)}")

    # Session totals
    click.echo("-" * len(header))
    click.echo(
        f"  Total: max_fill={_format_tokens(data.get('total_max_context_fill', 0))}"
        f"  compactions={data.get('total_compaction_count', 0)}"
    )


@cli.command()
@click.argument("project_dir", default=".", type=click.Path(exists=True))
@click.option("--port", default=8484, show_default=True, help="Server port for local dashboard")
@click.option("--export", "export_html", is_flag=True, help="Export HTML file instead of starting server")
@click.option("--limit", "-n", default=50, show_default=True, help="Number of sessions to include")
def dashboard(project_dir: str, port: int, export_html: bool, limit: int):
    """Launch a local dashboard with usage metrics and execution history.

    By default starts a local HTTP server and opens the browser.
    Use --export to save the HTML to .ccx/dashboard.html instead.
    """
    from ccx.dashboard import generate_html

    project = Path(project_dir).resolve()
    storage_root = Path(resolve_storage_dir(str(project)))
    ccx_dir = storage_root / ".ccx"

    if not ccx_dir.exists():
        click.echo("Error: ccx is not initialized. Run 'ccx init' first.", err=True)
        sys.exit(1)

    click.echo(f"Generating dashboard for: {project}")
    html = generate_html(str(storage_root), limit=limit)

    if export_html:
        out_path = ccx_dir / "dashboard.html"
        out_path.write_text(html, encoding="utf-8")
        click.echo(f"Dashboard exported to: {out_path}")
        return

    # Serve locally via stdlib http.server
    import http.server
    import webbrowser

    class _DashboardHandler(http.server.BaseHTTPRequestHandler):
        """Serve the generated HTML on GET /."""

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        def log_message(self, format, *args):
            # Silence default request logging
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), _DashboardHandler)
    url = f"http://127.0.0.1:{port}"
    click.echo(f"Serving dashboard at {url}")
    click.echo("Press Ctrl+C to stop.")
    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nDashboard server stopped.")
    finally:
        server.server_close()


def _sparkline(values: list, width: int = 0) -> str:
    """Return a Unicode sparkline string for a list of numeric values.

    Uses 8-level block characters (▁▂▃▄▅▆▇█).
    If *width* > 0 and differs from len(values), the data is resampled
    to fit the requested width.  width=0 means use len(values) as-is.
    """
    blocks = "▁▂▃▄▅▆▇█"

    if not values:
        return ""

    # Resample to target width if needed
    if width > 0 and width != len(values):
        n = len(values)
        resampled = []
        for i in range(width):
            # Map target index back to source range
            src = i * (n - 1) / (width - 1) if width > 1 else 0
            lo = int(src)
            hi = min(lo + 1, n - 1)
            frac = src - lo
            resampled.append(values[lo] * (1 - frac) + values[hi] * frac)
        values = resampled

    lo = min(values)
    hi = max(values)
    span = hi - lo

    chars = []
    for v in values:
        if span == 0:
            idx = 3  # mid-level when all values are equal
        else:
            idx = int((v - lo) / span * 7)
            idx = max(0, min(7, idx))
        chars.append(blocks[idx])

    return "".join(chars)


def _format_tokens(value) -> str:
    """Format token count for display: 1234 -> '1,234', 1500000 -> '1.5M'."""
    if not isinstance(value, (int, float)):
        return str(value)
    n = int(value)
    if n == 0:
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:,}"


def _ensure_ccx_directory(project: Path):
    """Ensure .ccx/ and .ccx/logs/ directories exist.

    Resolves the storage directory via ``resolve_storage_dir`` so that
    worktrees share the original repo's .ccx/ directory.
    """
    storage_root = Path(resolve_storage_dir(str(project)))
    ccx_dir = storage_root / ".ccx"
    ccx_dir.mkdir(exist_ok=True)
    (ccx_dir / "logs").mkdir(exist_ok=True)
    return ccx_dir


# Tools that ccx auto-approves so users never need to click "Allow" manually.
_PERMISSION_ALLOW_LIST = [
    "Bash",
    "Edit",
    "Write",
    "Read",
    "Grep",
    "Glob",
    "WebSearch",
    "WebFetch",
    "mcp__plugin_ccx_ccx__*",
    "mcp__ide__*",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "TaskOutput",
    "TaskStop",
    "NotebookEdit",
    "CronCreate",
    "CronDelete",
    "CronList",
    "EnterWorktree",
    "ExitWorktree",
    "EnterPlanMode",
    "ExitPlanMode",
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
    "Agent",
]


def _ensure_permissions_settings(project: Path, force: bool = False):
    """Create or update .claude/settings.local.json with tool permissions.

    If the file already exists and *force* is False, new permissions are
    merged into the existing ``permissions.allow`` list (union, deduplicated,
    order-preserved).  Other keys in the file are preserved.

    When *force* is True the ``permissions.allow`` list is replaced entirely.
    """
    claude_dir = project / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_path = claude_dir / "settings.local.json"

    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    if force:
        data.setdefault("permissions", {})["allow"] = list(_PERMISSION_ALLOW_LIST)
    else:
        existing = data.get("permissions", {}).get("allow", [])
        # Union with deduplication, preserving order (existing first)
        seen = set(existing)
        merged = list(existing)
        for perm in _PERMISSION_ALLOW_LIST:
            if perm not in seen:
                merged.append(perm)
                seen.add(perm)
        data.setdefault("permissions", {})["allow"] = merged

    settings_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    click.echo(f"  Permissions configured in .claude/settings.local.json")


def _create_base_context_starter(project: Path, force: bool = False):
    """Create base-context.yaml by scanning the project."""
    target = project / "base-context.yaml"
    if target.exists() and not force:
        click.echo("  base-context.yaml already exists, skipping (use --force to rescan)")
        return

    import yaml
    from ccx.scanner import scan_project

    click.echo("  Scanning project...")
    context = scan_project(str(project))

    target.write_text(
        yaml.dump(context, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    stack = context.get("stack", {})
    if stack:
        parts = [f"{k}: {v}" for k, v in stack.items() if v]
        click.echo(f"  Detected: {', '.join(parts)}")
    click.echo("  Created base-context.yaml (auto-generated, review and edit as needed)")


def main():
    cli()


if __name__ == "__main__":
    main()
