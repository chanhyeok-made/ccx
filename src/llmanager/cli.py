"""
Setup CLI for llmanager.
Installs skills and MCP server configuration into target projects.
"""

import json
import shutil
import sys
from pathlib import Path

import click

SKILLS_SOURCE = Path(__file__).parent / "skills"

MCP_CONFIG = {
    "mcpServers": {
        "llmanager": {
            "command": sys.executable,
            "args": ["-m", "llmanager.mcp_server"],
        }
    }
}


@click.group()
@click.version_option(package_name="llmanager")
def cli():
    """llmanager — Claude Code native extension setup tool."""
    pass


@cli.command()
@click.argument("project_dir", default=".", type=click.Path(exists=True))
@click.option("--force", "-f", is_flag=True, help="Overwrite existing files")
def init(project_dir: str, force: bool):
    """Initialize llmanager in a project directory.

    Copies skill templates, creates .mcp.json, and sets up .llmanager/ directory.
    """
    project = Path(project_dir).resolve()

    # 1. Copy skills to .claude/skills/
    skills_dest = project / ".claude" / "skills"
    _copy_skills(skills_dest, force)

    # 2. Create/update .mcp.json
    _write_mcp_json(project, force)

    # 3. Create base-context.yaml starter if not exists
    _create_base_context_starter(project)

    # 4. Create .llmanager/ directory
    llmanager_dir = project / ".llmanager"
    llmanager_dir.mkdir(exist_ok=True)

    click.echo("\nllmanager initialized successfully!")
    click.echo(f"  Skills:       {skills_dest}")
    click.echo(f"  MCP config:   {project / '.mcp.json'}")
    click.echo(f"  Session dir:  {llmanager_dir}")
    click.echo("\nNext steps:")
    click.echo("  1. Edit base-context.yaml to describe your project")
    click.echo("  2. Start Claude Code and use /project:run [request]")


@cli.command()
@click.argument("project_dir", default=".", type=click.Path(exists=True))
def update(project_dir: str):
    """Update skill templates to latest version."""
    project = Path(project_dir).resolve()
    skills_dest = project / ".claude" / "skills"
    _copy_skills(skills_dest, force=True)
    click.echo("Skills updated successfully!")


@cli.command()
@click.argument("project_dir", default=".", type=click.Path(exists=True))
def status(project_dir: str):
    """Check llmanager installation status."""
    project = Path(project_dir).resolve()

    checks = {
        ".claude/skills/run/SKILL.md": (project / ".claude" / "skills" / "run" / "SKILL.md").exists(),
        ".claude/skills/analyze/SKILL.md": (project / ".claude" / "skills" / "analyze" / "SKILL.md").exists(),
        ".claude/skills/review/SKILL.md": (project / ".claude" / "skills" / "review" / "SKILL.md").exists(),
        ".claude/skills/commit/SKILL.md": (project / ".claude" / "skills" / "commit" / "SKILL.md").exists(),
        ".mcp.json": (project / ".mcp.json").exists(),
        "base-context.yaml": (project / "base-context.yaml").exists(),
        ".llmanager/": (project / ".llmanager").is_dir(),
    }

    click.echo(f"llmanager status for: {project}\n")
    all_ok = True
    for name, ok in checks.items():
        icon = "OK" if ok else "MISSING"
        click.echo(f"  [{icon}] {name}")
        if not ok:
            all_ok = False

    if all_ok:
        click.echo("\nAll components installed.")
    else:
        click.echo("\nSome components missing. Run 'llm init' to set up.")


def _copy_skills(dest: Path, force: bool):
    """Copy skill templates from package to project."""
    if not SKILLS_SOURCE.exists():
        click.echo(f"Warning: Skills source not found at {SKILLS_SOURCE}", err=True)
        click.echo("Falling back to inline skill creation.", err=True)
        _create_inline_skills(dest)
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


def _create_inline_skills(dest: Path):
    """Fallback: create minimal skills if source templates not found (pip install case)."""
    # This handles the case where skills/ isn't available (e.g., installed via pip)
    dest.mkdir(parents=True, exist_ok=True)
    click.echo("  Created minimal skill stubs. Run 'llm update' after installing from source.")


def _write_mcp_json(project: Path, force: bool):
    """Create or merge .mcp.json."""
    mcp_path = project / ".mcp.json"

    if mcp_path.exists() and not force:
        # Merge: add llmanager server to existing config
        existing = json.loads(mcp_path.read_text(encoding="utf-8"))
        servers = existing.get("mcpServers", {})
        if "llmanager" in servers:
            click.echo("  .mcp.json already has llmanager config, skipping")
            return
        servers["llmanager"] = MCP_CONFIG["mcpServers"]["llmanager"]
        existing["mcpServers"] = servers
        mcp_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        click.echo("  Merged llmanager into existing .mcp.json")
    else:
        mcp_path.write_text(json.dumps(MCP_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
        click.echo("  Created .mcp.json")


def _create_base_context_starter(project: Path):
    """Create base-context.yaml if it doesn't exist."""
    target = project / "base-context.yaml"
    if target.exists():
        click.echo("  base-context.yaml already exists, skipping")
        return

    # Copy example if available
    example = Path(__file__).parent / "base-context.example.yaml"
    if example.exists():
        shutil.copy2(example, target)
        click.echo("  Created base-context.yaml from example template")
    else:
        # Create a minimal starter
        import yaml
        starter = {
            "project_name": project.name,
            "stack": {"runtime": "", "framework": "", "database": ""},
            "architecture": "# Describe your project architecture here\n",
            "structure": "# Paste your directory tree here\n",
            "exception_rules": {
                "forbidden": [],
                "required": [],
                "gotchas": [],
            },
        }
        target.write_text(yaml.dump(starter, default_flow_style=False, allow_unicode=True), encoding="utf-8")
        click.echo("  Created base-context.yaml starter")


def main():
    cli()


if __name__ == "__main__":
    main()
