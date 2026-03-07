"""
Setup CLI for ccx.
Installs skills and MCP server configuration into target projects.
"""

import json
import os
import shutil
import sys
from pathlib import Path

import click

SKILLS_SOURCE = Path(__file__).parent / "skills"
HOOKS_SOURCE = Path(__file__).parent / "hooks"

MCP_CONFIG = {
    "mcpServers": {
        "ccx": {
            "command": sys.executable,
            "args": ["-m", "ccx.mcp_server"],
        }
    }
}


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

    Copies skill templates, creates .mcp.json, and sets up .ccx/ directory.
    """
    project = Path(project_dir).resolve()

    # 1. Copy skills to .claude/skills/
    skills_dest = project / ".claude" / "skills"
    _copy_skills(skills_dest, force)

    # 2. Copy hooks to .claude/hooks/
    hooks_dest = project / ".claude" / "hooks"
    _copy_hooks(hooks_dest, force)

    # 3. Configure hooks in .claude/settings.json
    _write_hook_settings(project, force)

    # 4. Create/update .mcp.json
    _write_mcp_json(project, force)

    # 5. Create base-context.yaml by scanning project
    _create_base_context_starter(project, force)

    # 6. Create .ccx/ directory and logs subdirectory
    ccx_dir = project / ".ccx"
    ccx_dir.mkdir(exist_ok=True)
    logs_dir = ccx_dir / "logs"
    logs_dir.mkdir(exist_ok=True)

    click.echo("\nccx initialized successfully!")
    click.echo(f"  Skills:       {skills_dest}")
    click.echo(f"  Hooks:        {hooks_dest}")
    click.echo(f"  MCP config:   {project / '.mcp.json'}")
    click.echo(f"  Session dir:  {ccx_dir}")
    click.echo("\nNext steps:")
    click.echo("  1. Edit base-context.yaml to describe your project")
    click.echo("  2. Start Claude Code and use /project:run [request]")


@cli.command()
@click.argument("project_dir", default=".", type=click.Path(exists=True))
def update(project_dir: str):
    """Update skill templates and hooks to latest version."""
    project = Path(project_dir).resolve()
    skills_dest = project / ".claude" / "skills"
    _copy_skills(skills_dest, force=True)
    hooks_dest = project / ".claude" / "hooks"
    _copy_hooks(hooks_dest, force=True)
    _write_hook_settings(project, force=True)
    click.echo("Skills and hooks updated successfully!")


@cli.command()
@click.argument("project_dir", default=".", type=click.Path(exists=True))
def status(project_dir: str):
    """Check ccx installation status."""
    project = Path(project_dir).resolve()

    checks = {
        ".claude/skills/run/SKILL.md": (project / ".claude" / "skills" / "run" / "SKILL.md").exists(),
        ".claude/skills/analyze/SKILL.md": (project / ".claude" / "skills" / "analyze" / "SKILL.md").exists(),
        ".claude/skills/review/SKILL.md": (project / ".claude" / "skills" / "review" / "SKILL.md").exists(),
        ".claude/skills/commit/SKILL.md": (project / ".claude" / "skills" / "commit" / "SKILL.md").exists(),
        ".claude/hooks/log_event.sh": (project / ".claude" / "hooks" / "log_event.sh").exists(),
        ".mcp.json": (project / ".mcp.json").exists(),
        "base-context.yaml": (project / "base-context.yaml").exists(),
        ".ccx/": (project / ".ccx").is_dir(),
    }

    click.echo(f"ccx status for: {project}\n")
    all_ok = True
    for name, ok in checks.items():
        icon = "OK" if ok else "MISSING"
        click.echo(f"  [{icon}] {name}")
        if not ok:
            all_ok = False

    if all_ok:
        click.echo("\nAll components installed.")
    else:
        click.echo("\nSome components missing. Run 'ccx init' to set up.")


def _copy_skills(dest: Path, force: bool):
    """Copy skill templates from package to project."""
    if not SKILLS_SOURCE.exists():
        click.echo(f"Warning: Skills source not found at {SKILLS_SOURCE}", err=True)
        dest.mkdir(parents=True, exist_ok=True)
        return

    for skill_dir in SKILLS_SOURCE.iterdir():
        if not skill_dir.is_dir():
            continue

        target = dest / skill_dir.name
        if target.exists() and not force:
            click.echo(f"  Skipping {skill_dir.name}/ (exists, use --force to overwrite)")
            continue

        target.mkdir(parents=True, exist_ok=True)
        for f in skill_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, target / f.name)
                click.echo(f"  Copied {skill_dir.name}/{f.name}")


def _copy_hooks(dest: Path, force: bool):
    """Copy hook scripts from package to project."""
    if not HOOKS_SOURCE.exists():
        click.echo(f"Warning: Hooks source not found at {HOOKS_SOURCE}", err=True)
        return

    dest.mkdir(parents=True, exist_ok=True)
    for f in HOOKS_SOURCE.iterdir():
        if not f.is_file():
            continue

        target = dest / f.name
        if target.exists() and not force:
            click.echo(f"  Skipping hooks/{f.name} (exists, use --force to overwrite)")
            continue

        shutil.copy2(f, target)
        os.chmod(target, 0o755)
        click.echo(f"  Copied hooks/{f.name}")


def _write_hook_settings(project: Path, force: bool):
    """Add hook configuration to .claude/settings.json."""
    settings_path = project / ".claude" / "settings.json"

    existing = {}
    if settings_path.exists():
        raw = settings_path.read_text(encoding="utf-8")
        try:
            existing = json.loads(raw)
        except json.JSONDecodeError:
            click.echo(
                f"  Warning: {settings_path} is malformed JSON, skipping hook config",
                err=True,
            )
            return

    if "hooks" in existing and not force:
        click.echo("  settings.json already has hooks config, skipping")
        return

    hook_entry = {
        "type": "command",
        "command": '"$CLAUDE_PROJECT_DIR"/.claude/hooks/log_event.sh',
        "timeout": 10,
    }

    hooks = {}
    for event in [
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "UserPromptSubmit",
        "SubagentStart",
        "SubagentStop",
        "Stop",
    ]:
        hooks[event] = [{"matcher": "", "hooks": [hook_entry]}]

    existing["hooks"] = hooks

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    click.echo("  Configured hooks in .claude/settings.json")


def _write_mcp_json(project: Path, force: bool):
    """Create or merge .mcp.json."""
    mcp_path = project / ".mcp.json"

    if mcp_path.exists() and not force:
        existing = json.loads(mcp_path.read_text(encoding="utf-8"))
        servers = existing.get("mcpServers", {})
        if "ccx" in servers:
            click.echo("  .mcp.json already has ccx config, skipping")
            return
        servers["ccx"] = MCP_CONFIG["mcpServers"]["ccx"]
        existing["mcpServers"] = servers
        mcp_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        click.echo("  Merged ccx into existing .mcp.json")
    else:
        mcp_path.write_text(json.dumps(MCP_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
        click.echo("  Created .mcp.json")


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
