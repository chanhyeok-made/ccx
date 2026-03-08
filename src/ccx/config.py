"""
Base Context loader.
Looks for base-context.yaml in the project root, or uses a minimal default.
"""

import yaml
from pathlib import Path

DEFAULT_BASE_CONTEXT = {
    "project_name": "unknown",
    "stack": {},
    "architecture": "",
    "structure": "",
    "exception_rules": {
        "forbidden": [],
        "required": [],
        "gotchas": [],
    },
}


def load_base_context(project_dir: str, path: str | None = None) -> dict:
    """Load base context from yaml file or return defaults."""
    project_path = Path(project_dir)

    # Explicit path
    if path:
        p = Path(path)
        if p.exists():
            return _load_yaml(p)
        return DEFAULT_BASE_CONTEXT

    # Auto-detect in project root
    candidates = [
        project_path / "base-context.yaml",
        project_path / "base-context.yml",
        project_path / ".agent" / "base-context.yaml",
    ]

    for candidate in candidates:
        if candidate.exists():
            return _load_yaml(candidate)

    return DEFAULT_BASE_CONTEXT


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Merge with defaults for missing fields
    merged = DEFAULT_BASE_CONTEXT.copy()
    if data:
        _deep_merge(merged, data)
    return merged


def _deep_merge(base: dict, override: dict):
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
