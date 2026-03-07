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
CACHE_VERSION = 2
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
    file_hashes: dict[str, str] = field(default_factory=dict)  # {relative_path: git_blob_hash}
    children: list[str] = field(default_factory=list)  # child scope keys
    parent: str | None = None  # parent scope key


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


def _migrate_v1_to_v2(data: dict) -> dict:
    """Migrate cache data from v1 to v2: add file_hashes, children, parent fields."""
    data.setdefault("_meta", {"version": 1, "created_at": ""})
    for key, entry in data.items():
        if key == "_meta":
            continue
        entry.setdefault("file_hashes", {})
        entry.setdefault("children", [])
        entry.setdefault("parent", None)
        entry["version"] = 2
    data["_meta"]["version"] = 2
    return data


def _load_cache(project_dir: str) -> dict:
    path = _cache_path(project_dir)
    if not path.exists():
        return {"_meta": {"version": CACHE_VERSION, "created_at": datetime.now(timezone.utc).isoformat()}}
    data = json.loads(path.read_text(encoding="utf-8"))

    # Migration: v1 → v2
    meta = data.get("_meta", {})
    if meta.get("version", 1) < 2:
        data = _migrate_v1_to_v2(data)
        _save_cache(project_dir, data)

    return data


def _save_cache(project_dir: str, data: dict) -> None:
    path = _cache_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _check_staleness(project_dir: str, entry: dict) -> tuple[bool, str]:
    """Check if cached entry is stale by detecting file changes.

    Primary: compare file_hashes (git blob hashes) against current git index.
    Fallback: git log --since + mtime comparison for entries without file_hashes.
    """
    file_hashes = entry.get("file_hashes", {})

    # Primary: file_hashes-based staleness check
    if file_hashes:
        try:
            result = subprocess.run(
                ["git", "ls-files", "-s"],
                capture_output=True,
                text=True,
                cwd=project_dir,
                timeout=5,
            )
            if result.returncode == 0:
                # Parse git ls-files -s output: "100644 {blob_hash} 0\t{path}"
                index: dict[str, str] = {}
                for line in result.stdout.strip().split("\n"):
                    if not line:
                        continue
                    parts = line.split("\t", 1)
                    if len(parts) == 2:
                        blob_hash = parts[0].split()[1]
                        path = parts[1]
                        index[path] = blob_hash

                changed: list[str] = []
                for path, cached_hash in file_hashes.items():
                    current_hash = index.get(path)
                    if current_hash is None:
                        changed.append(f"removed/untracked: {path}")
                    elif current_hash != cached_hash:
                        changed.append(f"changed: {path}")

                if changed:
                    return True, f"{len(changed)} file(s) differ — {'; '.join(changed)}"
                return False, ""
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass  # git unavailable, fall through to legacy check

    # Fallback: git log --since + mtime (for entries without file_hashes or git failure)
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
    file_hashes: dict[str, str] | None = None,
    children: list[str] | None = None,
    parent: str | None = None,
    scope_tree: dict[str, list[str]] | None = None,
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
        file_hashes=file_hashes or {},
        children=children or [],
        parent=parent,
    )

    data[scope] = asdict(entry)

    # Update scope_tree in _meta if provided
    if scope_tree is not None:
        data["_meta"]["scope_tree"] = scope_tree

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


def build_scope_tree(project_dir: str) -> dict:
    """Build hierarchical scope tree from project structure.

    Discovers all scopes, updates parent/children relationships,
    saves scope_tree to cache _meta.
    """
    from ccx.scanner import discover_scopes

    scopes = discover_scopes(project_dir)
    data = _load_cache(project_dir)

    # Build scope_tree: {parent_key: [child_keys]}
    scope_tree: dict[str, list[str]] = {}
    for s in scopes:
        parent = s.get("parent")
        if parent is not None:
            scope_tree.setdefault(parent, [])
            if s["key"] not in scope_tree[parent]:
                scope_tree[parent].append(s["key"])

    # Track all discovered scope keys
    discovered_keys = {s["key"] for s in scopes}

    # Identify new and stale scopes
    existing_keys = {k for k in data if k != "_meta"}
    new_scopes = sorted(discovered_keys - existing_keys)
    stale_scopes = sorted(existing_keys - discovered_keys)

    # Count types
    packages = sum(1 for s in scopes if s["type"] == "package")
    modules = sum(1 for s in scopes if s["type"] == "module")

    # Update existing cache entries with parent/children relationships
    for s in scopes:
        key = s["key"]
        children = scope_tree.get(key, [])
        parent = s.get("parent")

        if key in data and key != "_meta":
            data[key]["children"] = children
            data[key]["parent"] = parent
        # For new scopes not yet in cache, create a minimal placeholder entry
        elif key not in data:
            data[key] = {
                "scope": key,
                "summary": "",
                "key_files": s.get("files", []),
                "interfaces": [],
                "known_issues": [],
                "patterns": [],
                "dependencies": [],
                "cached_at": "",
                "cached_by_request": "",
                "version": CACHE_VERSION,
                "extra": {},
                "file_hashes": {},
                "children": children,
                "parent": parent,
            }

    # Save scope_tree to _meta
    data["_meta"]["scope_tree"] = scope_tree

    # Rolling limit: evict oldest entries beyond MAX_SCOPES
    scope_keys = [k for k in data if k != "_meta"]
    if len(scope_keys) > MAX_SCOPES:
        scope_keys.sort(key=lambda k: data[k].get("cached_at", ""))
        for k in scope_keys[: len(scope_keys) - MAX_SCOPES]:
            del data[k]

    _save_cache(project_dir, data)

    return {
        "total_scopes": len(scopes),
        "packages": packages,
        "modules": modules,
        "scope_tree": scope_tree,
        "new_scopes": new_scopes,
        "stale_scopes": stale_scopes,
    }


def _load_git_index(project_dir: str) -> dict[str, str] | None:
    """Run `git ls-files -s` once and return {path: blob_hash} or None on failure."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-s"],
            capture_output=True,
            text=True,
            cwd=project_dir,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        index: dict[str, str] = {}
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                blob_hash = parts[0].split()[1]
                path = parts[1]
                index[path] = blob_hash
        return index
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _check_staleness_with_index(
    project_dir: str, entry: dict, git_index: dict[str, str] | None
) -> tuple[bool, str]:
    """Check staleness using a pre-computed git index to avoid repeated subprocess calls."""
    file_hashes = entry.get("file_hashes", {})

    if file_hashes and git_index is not None:
        changed: list[str] = []
        for path, cached_hash in file_hashes.items():
            current_hash = git_index.get(path)
            if current_hash is None:
                changed.append(f"removed/untracked: {path}")
            elif current_hash != cached_hash:
                changed.append(f"changed: {path}")
        if changed:
            return True, f"{len(changed)} file(s) differ — {'; '.join(changed)}"
        return False, ""

    # Fall back to full _check_staleness for entries without file_hashes or when index unavailable
    return _check_staleness(project_dir, entry)


def get_scope_with_children(
    project_dir: str, scope: str, check_staleness: bool = True
) -> dict:
    """Get a scope entry with summaries of all descendant scopes."""
    scope = normalize_scope(scope)
    data = _load_cache(project_dir)
    entry = data.get(scope)

    if entry is None or scope == "_meta":
        return {"scope": None, "children": [], "stale": True}

    if check_staleness:
        # Pre-compute git index once instead of N subprocess calls
        git_index = _load_git_index(project_dir)
        stale, _ = _check_staleness_with_index(project_dir, entry, git_index)
    else:
        stale = False

    # Recursively collect all descendant scopes
    children_summaries: list[dict] = []
    visited: set[str] = set()

    def _collect_descendants(parent_key: str) -> None:
        child_keys = data.get(parent_key, {}).get("children", [])
        for child_key in child_keys:
            if child_key in visited or child_key == "_meta":
                continue
            visited.add(child_key)
            child_entry = data.get(child_key)
            if child_entry is not None:
                if check_staleness:
                    child_stale, _ = _check_staleness_with_index(
                        project_dir, child_entry, git_index
                    )
                else:
                    child_stale = False
                children_summaries.append({
                    "key": child_key,
                    "summary": child_entry.get("summary", ""),
                    "stale": child_stale,
                })
                _collect_descendants(child_key)

    _collect_descendants(scope)

    return {
        "scope": entry,
        "children": children_summaries,
        "stale": stale,
    }


def mark_stale_cascade(project_dir: str, scope: str) -> dict:
    """Mark a scope and all its ancestor scopes as stale.

    Clears file_hashes so _check_staleness detects them as needing re-analysis.
    """
    scope = normalize_scope(scope)
    data = _load_cache(project_dir)

    marked: list[str] = []

    # Mark the scope itself
    if scope in data and scope != "_meta":
        data[scope]["file_hashes"] = {}
        marked.append(scope)

    # Walk up through ancestors
    current = scope
    visited: set[str] = {scope}
    while True:
        entry = data.get(current)
        if entry is None:
            break
        parent = entry.get("parent")
        if parent is None or parent == "_meta":
            break
        if parent in visited:
            break
        visited.add(parent)
        if parent in data:
            data[parent]["file_hashes"] = {}
            if parent not in marked:
                marked.append(parent)
        current = parent

    if marked:
        _save_cache(project_dir, data)

    return {"marked": marked}
