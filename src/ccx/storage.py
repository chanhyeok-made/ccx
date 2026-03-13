"""
Shared storage directory resolution for .ccx data.
Ensures all sessions (including git worktrees) share a single .ccx storage
directory located at the original repository root.
"""

import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=64)
def resolve_storage_dir(project_dir: str) -> str:
    """Return the original repo root for .ccx storage.

    In a worktree, resolves to the original repo root via
    ``git rev-parse --git-common-dir``.  In a normal repo or non-git
    directory, returns *project_dir* as-is.

    Results are cached per *project_dir* to avoid repeated subprocess calls.
    """
    try:
        result = subprocess.run(
            ["git", "-C", project_dir, "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return project_dir

    if result.returncode != 0:
        return project_dir

    git_common_dir = result.stdout.strip()

    if not git_common_dir:
        return project_dir

    # Relative ".git" means we are in a normal (non-worktree) repo.
    if git_common_dir == ".git":
        return project_dir

    # Absolute path -> worktree.  The common git dir is the .git directory
    # of the original repo, so its parent is the repo root.
    common_path = Path(git_common_dir)
    if common_path.is_absolute():
        return str(common_path.parent)

    # Any other relative path (e.g. "../<main>/.git") — resolve relative
    # to project_dir and take the parent.
    resolved = (Path(project_dir) / common_path).resolve()
    return str(resolved.parent)
