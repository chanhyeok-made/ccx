#!/usr/bin/env python3
"""
Claude Code SessionStart hook: ensure .claude/settings.local.json exists.

When Claude Code starts a session (especially in a worktree), this hook
checks if .claude/settings.local.json exists in the project directory.
If missing, it copies from the main repo root (for worktrees) or generates
a default one with all ccx permissions.

Always exits 0 to never block Claude Code.
"""

import json
import os
import shutil
import sys

# Allow importing ccx package when running as a standalone hook script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ccx.storage import resolve_storage_dir

# Default settings.local.json content with all ccx permissions
_DEFAULT_SETTINGS = {
    "permissions": {
        "allow": [
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
    }
}


def main():
    raw = sys.stdin.read()

    # Determine project directory from env var or stdin JSON
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project_dir and raw.strip():
        try:
            data = json.loads(raw)
            project_dir = data.get("cwd", ".")
        except (json.JSONDecodeError, ValueError):
            project_dir = "."
    if not project_dir:
        project_dir = "."

    settings_path = os.path.join(project_dir, ".claude", "settings.local.json")

    # If settings.local.json already exists, nothing to do
    if os.path.isfile(settings_path):
        return

    # Resolve the main repo root (differs from project_dir in worktrees)
    main_repo_root = resolve_storage_dir(project_dir)

    # Ensure .claude directory exists in the project
    os.makedirs(os.path.join(project_dir, ".claude"), exist_ok=True)

    # Try to copy from main repo root (worktree scenario)
    if main_repo_root != project_dir:
        source_path = os.path.join(main_repo_root, ".claude", "settings.local.json")
        if os.path.isfile(source_path):
            shutil.copy2(source_path, settings_path)
            return

    # No source to copy from — generate default settings
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(_DEFAULT_SETTINGS, f, indent=2, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
