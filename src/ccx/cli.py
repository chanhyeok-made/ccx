"""
Setup CLI for ccx.
Skills, hooks, and MCP configuration are handled by the plugin system
(claude plugin install). This CLI manages base-context.yaml and .ccx/ directory.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import click

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

    Creates base-context.yaml and .ccx/ directory.
    Skills, hooks, and MCP config are managed by the plugin system.
    """
    project = Path(project_dir).resolve()

    # 1. Create base-context.yaml by scanning project
    _create_base_context_starter(project, force)

    # 2. Create .ccx/ directory and logs subdirectory
    ccx_dir = _ensure_ccx_directory(project)

    click.echo("\nccx initialized successfully!")
    click.echo(f"  Base context: {project / 'base-context.yaml'}")
    click.echo(f"  Session dir:  {ccx_dir}")
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
    click.echo("Run 'claude plugin install' to update skills, hooks, and MCP config.")


@cli.command()
@click.argument("project_dir", default=".", type=click.Path(exists=True))
def status(project_dir: str):
    """Check ccx installation status."""
    project = Path(project_dir).resolve()

    checks = {
        "base-context.yaml": (project / "base-context.yaml").exists(),
        ".ccx/": (project / ".ccx").is_dir(),
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
    cache_dir = project / ".ccx" / "cache" / "scopes"
    if cache_dir.exists():
        scope_files = list(cache_dir.rglob("_scope.json"))
        click.echo(f"  Analysis cache: {len(scope_files)} scope(s) cached (directory-based)")
    else:
        # Check for legacy flat file
        legacy = project / ".ccx" / "analysis-cache.json"
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
    ccx_dir = project / ".ccx"

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
    ccx_dir = project / ".ccx"

    if not ccx_dir.exists():
        click.echo("Error: ccx is not initialized. Run 'ccx init' first.", err=True)
        sys.exit(1)

    if detail:
        _show_session_detail(str(project), detail)
        return

    result = list_session_usages(str(project), limit=limit)
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
    ]

    # Header
    header = "  ".join(h.ljust(w) for h, _, w, _ in columns)
    click.echo(header)
    click.echo("-" * len(header))

    # Rows
    for s in sessions:
        row = "  ".join(
            fmt(s.get(key, 0)).rjust(w) if key != "session_id" and key != "timestamp" else fmt(s.get(key, "")).ljust(w)
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
        elif key == "timestamp":
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
    """Ensure .ccx/ and .ccx/logs/ directories exist."""
    ccx_dir = project / ".ccx"
    ccx_dir.mkdir(exist_ok=True)
    (ccx_dir / "logs").mkdir(exist_ok=True)
    return ccx_dir


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
