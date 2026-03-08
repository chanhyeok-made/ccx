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
        click.echo(f"  Stale scopes: {stale_count}")

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
