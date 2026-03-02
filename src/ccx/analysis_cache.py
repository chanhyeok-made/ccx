"""
Analysis cache for reusing module-level knowledge across pipeline runs.
Reduces token usage by caching structured analysis results per scope.
"""

import json
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CACHE_DIR = ".ccx"
CACHE_FILE = "analysis-cache.json"
MAX_SCOPES = 200
CACHE_VERSION = 1
STALENESS_THRESHOLD_SECONDS = 0  # any change after cached_at → stale


@dataclass
class CacheEntry:
    """Cached analysis result for a single scope (module/layer/feature)."""

    scope: str
    summary: str
    key_files: list[str] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    known_issues: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    cached_at: str = ""
    cached_by_request: str = ""
    version: int = CACHE_VERSION
    extra: dict = field(default_factory=dict)


def normalize_scope(scope: str) -> str:
    """Normalize scope to a canonical file-path-based key.

    Rules:
    - Strip leading/trailing whitespace and slashes
    - Use forward slashes
    - Remove common file extensions (.py, .ts, .js, .go, .rs, .java, .md, .yaml, .yml, .json)
    - Lowercase

    Examples:
        "src/ccx/mcp_server.py" → "src/ccx/mcp_server"
        "src/ccx/Skills/" → "src/ccx/skills"
        "Src\\CCX\\Config.py" → "src/ccx/config"
    """
    s = scope.strip().strip("/").strip("\\")
    s = s.replace("\\", "/")
    s = s.lower()
    # Remove known extensions
    for ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".md", ".yaml", ".yml", ".json", ".toml"):
        if s.endswith(ext):
            s = s[: -len(ext)]
            break
    return s.rstrip("/")


def _cache_path(project_dir: str) -> Path:
    return Path(project_dir) / CACHE_DIR / CACHE_FILE


def _load_cache(project_dir: str) -> dict:
    path = _cache_path(project_dir)
    if not path.exists():
        return {"_meta": {"version": CACHE_VERSION, "created_at": datetime.now(timezone.utc).isoformat()}}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_cache(project_dir: str, data: dict) -> None:
    path = _cache_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _check_staleness(project_dir: str, entry: dict) -> tuple[bool, str]:
    """Check if cached entry is stale by detecting file changes since cached_at."""
    cached_at = entry.get("cached_at", "")
    key_files = entry.get("key_files", [])

    if not cached_at or not key_files:
        return False, ""

    # Try git log --since first
    try:
        result = subprocess.run(
            ["git", "log", "--since", cached_at, "--oneline", "--", *key_files],
            capture_output=True,
            text=True,
            cwd=project_dir,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            commits = result.stdout.strip().split("\n")
            return True, f"{len(commits)} commit(s) touching cached files since {cached_at}"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: check mtime
    try:
        cached_dt = datetime.fromisoformat(cached_at)
        if cached_dt.tzinfo is None:
            cached_dt = cached_dt.replace(tzinfo=timezone.utc)
        cached_ts = cached_dt.timestamp()

        for f in key_files:
            fp = Path(project_dir) / f
            if fp.exists() and fp.stat().st_mtime > cached_ts:
                return True, f"File modified after cache: {f}"
    except (ValueError, OSError):
        pass

    return False, ""


def get_analysis_cache(
    project_dir: str, scope: str, check_staleness: bool = True
) -> dict:
    """Look up cached analysis for a scope.

    Returns: {hit: bool, stale: bool, entry: dict|None, stale_reason: str}
    """
    scope = normalize_scope(scope)
    data = _load_cache(project_dir)
    entry = data.get(scope)

    if entry is None or scope == "_meta":
        return {"hit": False, "stale": False, "entry": None, "stale_reason": ""}

    stale = False
    stale_reason = ""
    if check_staleness:
        stale, stale_reason = _check_staleness(project_dir, entry)

    return {
        "hit": True,
        "stale": stale,
        "entry": entry,
        "stale_reason": stale_reason,
    }


def save_analysis_cache(
    project_dir: str,
    scope: str,
    summary: str,
    key_files: list[str] | None = None,
    interfaces: list[str] | None = None,
    known_issues: list[str] | None = None,
    patterns: list[str] | None = None,
    dependencies: list[str] | None = None,
    cached_by_request: str = "",
    extra: dict | None = None,
) -> dict:
    """Save or update analysis cache for a scope.

    Returns: {status: str, scope: str}
    """
    scope = normalize_scope(scope)
    data = _load_cache(project_dir)

    entry = CacheEntry(
        scope=scope,
        summary=summary,
        key_files=key_files or [],
        interfaces=interfaces or [],
        known_issues=known_issues or [],
        patterns=patterns or [],
        dependencies=dependencies or [],
        cached_at=datetime.now(timezone.utc).isoformat(),
        cached_by_request=cached_by_request,
        extra=extra or {},
    )

    data[scope] = asdict(entry)

    # Rolling limit: evict oldest entries beyond MAX_SCOPES
    scope_keys = [k for k in data if k != "_meta"]
    if len(scope_keys) > MAX_SCOPES:
        # Sort by cached_at ascending, evict oldest
        scope_keys.sort(key=lambda k: data[k].get("cached_at", ""))
        for k in scope_keys[: len(scope_keys) - MAX_SCOPES]:
            del data[k]

    _save_cache(project_dir, data)
    return {"status": "saved", "scope": scope}


def invalidate_cache(project_dir: str, scope: str) -> dict:
    """Remove a scope from the cache.

    Returns: {status: str, scope: str}
    """
    scope = normalize_scope(scope)
    data = _load_cache(project_dir)

    if scope in data and scope != "_meta":
        del data[scope]
        _save_cache(project_dir, data)
        return {"status": "invalidated", "scope": scope}

    return {"status": "not_found", "scope": scope}


def list_cached_scopes(project_dir: str) -> list[dict]:
    """List all cached scopes with brief info.

    Returns: list of {scope, summary, cached_at, key_files_count}
    """
    data = _load_cache(project_dir)
    result = []
    for key, entry in data.items():
        if key == "_meta":
            continue
        result.append({
            "scope": key,
            "summary": entry.get("summary", ""),
            "cached_at": entry.get("cached_at", ""),
            "key_files_count": len(entry.get("key_files", [])),
        })
    return result
