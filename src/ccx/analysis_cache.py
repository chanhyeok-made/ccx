"""
Analysis cache for reusing module-level knowledge across pipeline runs.
Reduces token usage by caching structured analysis results per scope.

Storage layout (v2 directory-based):
    .ccx/cache/
    ├── _meta.json              <- {version, created_at, scope_tree}
    └── scopes/
        └── src/
            └── ccx/
                ├── _scope.json     <- CacheEntry for "src/ccx"
                └── hooks/
                    └── log_event/
                        └── _scope.json <- CacheEntry for "src/ccx/hooks/log_event"
"""

import json
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CACHE_DIR = ".ccx"
CACHE_SUBDIR = "cache"
SCOPES_SUBDIR = "scopes"
SCOPE_FILE = "_scope.json"
META_FILE = "_meta.json"
CACHE_VERSION = 2
MAX_SCOPES = 5000
STALENESS_THRESHOLD_SECONDS = 0  # any change after cached_at -> stale
VALID_ANNOTATION_TYPES = {"domain", "architecture", "usage", "ambiguity"}

# Legacy flat-file name (for migration)
_LEGACY_CACHE_FILE = "analysis-cache.json"


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
    annotations: list[dict] = field(default_factory=list)  # [{type, content, question?, answer?, added_by, added_at}]


# ---------------------------------------------------------------------------
# Scope normalization (unchanged)
# ---------------------------------------------------------------------------

def normalize_scope(scope: str) -> str:
    """Normalize scope to a canonical file-path-based key.

    Rules:
    - Strip leading/trailing whitespace and slashes
    - Use forward slashes
    - Remove common file extensions (.py, .ts, .js, .go, .rs, .java, .md, .yaml, .yml, .json)
    - Lowercase

    Examples:
        "src/ccx/mcp_server.py" -> "src/ccx/mcp_server"
        "src/ccx/Skills/" -> "src/ccx/skills"
        "Src\\CCX\\Config.py" -> "src/ccx/config"
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


# ---------------------------------------------------------------------------
# Low-level path helpers
# ---------------------------------------------------------------------------

def _cache_base(project_dir: str) -> Path:
    """Return .ccx/cache/ path."""
    return Path(project_dir) / CACHE_DIR / CACHE_SUBDIR


def _scopes_dir(project_dir: str) -> Path:
    """Return .ccx/cache/scopes/ path."""
    return _cache_base(project_dir) / SCOPES_SUBDIR


def _meta_path(project_dir: str) -> Path:
    """Return .ccx/cache/_meta.json path."""
    return _cache_base(project_dir) / META_FILE


def _scope_to_path(project_dir: str, scope: str) -> Path:
    """Convert scope key to _scope.json path.
    e.g. 'src/ccx/hooks/log_event' -> .ccx/cache/scopes/src/ccx/hooks/log_event/_scope.json
    """
    return _scopes_dir(project_dir) / scope / SCOPE_FILE


# ---------------------------------------------------------------------------
# Low-level I/O helpers
# ---------------------------------------------------------------------------

def _load_meta(project_dir: str) -> dict:
    """Load _meta.json. Return default if not exists."""
    path = _meta_path(project_dir)
    if not path.exists():
        return {
            "version": CACHE_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "version": CACHE_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


def _save_meta(project_dir: str, meta: dict) -> None:
    """Write _meta.json."""
    path = _meta_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_scope(project_dir: str, scope: str) -> dict | None:
    """Load a single scope's _scope.json. Return None if not exists."""
    path = _scope_to_path(project_dir, scope)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_scope(project_dir: str, scope: str, entry: dict) -> None:
    """Write a single scope's _scope.json. Create dirs as needed."""
    path = _scope_to_path(project_dir, scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")


def _delete_scope(project_dir: str, scope: str) -> None:
    """Delete a scope's _scope.json and clean up empty parent dirs up to scopes/."""
    path = _scope_to_path(project_dir, scope)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            return
    # Clean up empty parent directories up to (but not including) scopes/
    _cleanup_empty_dirs(path.parent, _scopes_dir(project_dir))


def _cleanup_empty_dirs(path: Path, stop_at: Path) -> None:
    """Remove empty directories from path up to (but not including) stop_at."""
    current = path
    stop_resolved = stop_at.resolve()
    while True:
        try:
            current_resolved = current.resolve()
        except OSError:
            break
        # Don't delete the stop_at directory itself or anything above it
        if current_resolved == stop_resolved or not str(current_resolved).startswith(str(stop_resolved)):
            break
        try:
            if current.exists() and current.is_dir() and not any(current.iterdir()):
                current.rmdir()
            else:
                break  # Directory not empty, stop
        except OSError:
            break
        current = current.parent


def _list_all_scopes(project_dir: str) -> list[dict]:
    """Walk scopes/ directory, find all _scope.json files, load and return entries."""
    scopes_root = _scopes_dir(project_dir)
    if not scopes_root.exists():
        return []

    entries: list[dict] = []
    for scope_file in scopes_root.rglob(SCOPE_FILE):
        try:
            entry = json.loads(scope_file.read_text(encoding="utf-8"))
            entries.append(entry)
        except (json.JSONDecodeError, OSError):
            continue
    return entries


# ---------------------------------------------------------------------------
# Migration from flat file to directory-based cache
# ---------------------------------------------------------------------------

def _migrate_flat_to_dir(project_dir: str) -> None:
    """If old analysis-cache.json exists, split into per-scope files + _meta.json.
    Also handle v1->v2 field upgrades.
    Rename old file to analysis-cache.json.bak.
    """
    legacy_path = Path(project_dir) / CACHE_DIR / _LEGACY_CACHE_FILE
    if not legacy_path.exists():
        return

    try:
        data = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Can't read legacy file; rename it and move on
        try:
            legacy_path.rename(legacy_path.with_suffix(".json.bak"))
        except OSError:
            pass
        return

    # Extract _meta or build a default one
    old_meta = data.pop("_meta", {})
    old_version = old_meta.get("version", 1)

    meta: dict[str, Any] = {
        "version": CACHE_VERSION,
        "created_at": old_meta.get("created_at", datetime.now(timezone.utc).isoformat()),
    }
    if "scope_tree" in old_meta:
        meta["scope_tree"] = old_meta["scope_tree"]

    # Write meta
    _save_meta(project_dir, meta)

    # Migrate each scope entry
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue

        # v1 -> v2 field upgrades
        if old_version < 2:
            entry.setdefault("file_hashes", {})
            entry.setdefault("children", [])
            entry.setdefault("parent", None)
            entry["version"] = CACHE_VERSION

        _save_scope(project_dir, key, entry)

    # Rename old file to .bak
    try:
        backup_path = legacy_path.with_suffix(".json.bak")
        if backup_path.exists():
            backup_path.unlink()
        legacy_path.rename(backup_path)
    except OSError:
        pass


def _ensure_cache(project_dir: str) -> None:
    """Create cache dirs if needed. Run migration if old file exists."""
    scopes_root = _scopes_dir(project_dir)
    scopes_root.mkdir(parents=True, exist_ok=True)

    # Run migration if legacy flat file exists
    legacy_path = Path(project_dir) / CACHE_DIR / _LEGACY_CACHE_FILE
    if legacy_path.exists():
        _migrate_flat_to_dir(project_dir)


# ---------------------------------------------------------------------------
# Staleness detection (unchanged from original)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Public API (signatures unchanged)
# ---------------------------------------------------------------------------

def get_analysis_cache(
    project_dir: str, scope: str, check_staleness: bool = True
) -> dict:
    """Look up cached analysis for a scope.

    Returns: {hit: bool, stale: bool, entry: dict|None, stale_reason: str}
    """
    scope = normalize_scope(scope)
    _ensure_cache(project_dir)
    entry = _load_scope(project_dir, scope)

    if entry is None:
        return {"hit": False, "stale": False, "entry": None, "stale_reason": ""}

    stale = False
    stale_reason = ""
    if check_staleness:
        git_index = _load_git_index(project_dir)
        stale, stale_reason = _check_staleness_with_index(project_dir, entry, git_index)

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
    annotations: list[dict] | None = None,
) -> dict:
    """Save or update analysis cache for a scope.

    Returns: {status: str, scope: str}
    """
    scope = normalize_scope(scope)
    _ensure_cache(project_dir)

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
        annotations=annotations or [],
    )

    _save_scope(project_dir, scope, asdict(entry))

    # Update scope_tree in _meta if provided
    if scope_tree is not None:
        meta = _load_meta(project_dir)
        meta["scope_tree"] = scope_tree
        _save_meta(project_dir, meta)

    # Rolling limit: lightweight count first, only load entries if eviction needed
    scopes_root = _scopes_dir(project_dir)
    scope_count = sum(1 for _ in scopes_root.rglob(SCOPE_FILE))
    if scope_count > MAX_SCOPES:
        all_entries = _list_all_scopes(project_dir)
        all_entries.sort(key=lambda e: e.get("cached_at", ""))
        to_evict = all_entries[: len(all_entries) - MAX_SCOPES]
        for old_entry in to_evict:
            old_scope = old_entry.get("scope", "")
            if old_scope:
                _delete_scope(project_dir, old_scope)

    return {"status": "saved", "scope": scope}


def invalidate_cache(project_dir: str, scope: str) -> dict:
    """Remove a scope from the cache.

    Returns: {status: str, scope: str}
    """
    scope = normalize_scope(scope)
    _ensure_cache(project_dir)

    existing = _load_scope(project_dir, scope)
    if existing is not None:
        _delete_scope(project_dir, scope)
        return {"status": "invalidated", "scope": scope}

    return {"status": "not_found", "scope": scope}


def list_cached_scopes(project_dir: str) -> list[dict]:
    """List all cached scopes with brief info.

    Returns: list of {scope, summary, cached_at, key_files_count}
    """
    _ensure_cache(project_dir)
    all_entries = _list_all_scopes(project_dir)
    result = []
    for entry in all_entries:
        result.append({
            "scope": entry.get("scope", ""),
            "summary": entry.get("summary", ""),
            "cached_at": entry.get("cached_at", ""),
            "key_files_count": len(entry.get("key_files", [])),
        })
    return result


def _collect_pending(project_dir: str) -> list[dict]:
    """Collect all scopes with empty summary (needing analysis)."""
    _ensure_cache(project_dir)
    all_entries = _list_all_scopes(project_dir)

    pending: list[dict] = []
    for entry in all_entries:
        if entry.get("summary"):
            continue
        stype = "package" if entry.get("children") else "module"
        pending.append({
            "key": entry.get("scope", ""),
            "type": stype,
            "files": entry.get("key_files", []),
            "parent": entry.get("parent"),
            "children": entry.get("children", []),
        })
    return pending


def get_pending_scopes(
    project_dir: str,
    scope_type: str = "all",
    offset: int = 0,
    limit: int = 50,
    prefix: str = "",
) -> dict:
    """Get scopes that need analysis (empty summary), paginated.

    Returns scopes sorted: modules first (by key), then packages by depth
    (deepest first, so children are analyzed before parents).

    Args:
        project_dir: Project root directory path.
        scope_type: Filter by type — "module", "package", or "all".
        offset: Number of scopes to skip (for pagination).
        limit: Max scopes to return per page.
        prefix: Filter scopes whose key starts with this prefix (e.g. "src/api").

    Returns:
        {total_pending, offset, limit, has_more, scopes: [{key, type, files, parent, children}]}
    """
    pending = _collect_pending(project_dir)

    # Apply filters
    if scope_type != "all":
        pending = [s for s in pending if s["type"] == scope_type]
    if prefix:
        pending = [s for s in pending if s["key"].startswith(prefix)]

    # Sort: modules first (by key), then packages by depth descending
    def _sort_key(s: dict) -> tuple:
        is_pkg = 1 if s["type"] == "package" else 0
        depth = -s["key"].count("/") if is_pkg else 0
        return (is_pkg, depth, s["key"])

    pending.sort(key=_sort_key)

    total = len(pending)
    page = pending[offset : offset + limit]

    return {
        "total_pending": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
        "scopes": page,
    }


def get_pending_summary(project_dir: str, group_depth: int = 1) -> dict:
    """Get a compact summary of pending scopes grouped by top-level directory.

    Args:
        project_dir: Project root directory path.
        group_depth: Number of path segments to use for grouping (default 1).

    Returns:
        {total_pending, module_count, package_count,
         groups: [{name, module_count, package_count, total}]}
    """
    pending = _collect_pending(project_dir)

    module_count = 0
    package_count = 0
    groups: dict[str, dict] = {}

    for s in pending:
        parts = s["key"].split("/")
        group_name = "/".join(parts[:group_depth]) if len(parts) >= group_depth else s["key"]

        if group_name not in groups:
            groups[group_name] = {"name": group_name, "module_count": 0, "package_count": 0, "total": 0}

        if s["type"] == "module":
            module_count += 1
            groups[group_name]["module_count"] += 1
        else:
            package_count += 1
            groups[group_name]["package_count"] += 1
        groups[group_name]["total"] += 1

    # Sort groups by total descending
    sorted_groups = sorted(groups.values(), key=lambda g: -g["total"])

    return {
        "total_pending": len(pending),
        "module_count": module_count,
        "package_count": package_count,
        "groups": sorted_groups,
    }


def get_annotations(
    project_dir: str,
    scope: str = "",
    annotation_type: str = "all",
    unresolved_only: bool = False,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """Get annotations across scopes, with optional filters.

    Args:
        project_dir: Project root directory path.
        scope: Filter to a specific scope (empty = all scopes).
        annotation_type: Filter by type — "domain", "architecture", "usage",
                         "ambiguity", or "all".
        unresolved_only: If True, only return ambiguity-type annotations without answers.
        offset: Pagination offset.
        limit: Max items per page.

    Returns:
        {total, offset, limit, has_more,
         items: [{scope, type, content, question?, answer?, added_by, added_at}]}
    """
    _ensure_cache(project_dir)

    if scope:
        scope = normalize_scope(scope)
        entry = _load_scope(project_dir, scope)
        entries = [entry] if entry else []
    else:
        entries = _list_all_scopes(project_dir)

    items: list[dict] = []
    for entry in entries:
        entry_scope = entry.get("scope", "")
        for ann in entry.get("annotations", []):
            atype = ann.get("type", "")
            if annotation_type != "all" and atype != annotation_type:
                continue
            if unresolved_only and (atype != "ambiguity" or ann.get("answer")):
                continue
            item = {"scope": entry_scope, **ann}
            items.append(item)

    items.sort(key=lambda x: (x["scope"], x.get("type", "")))
    total = len(items)
    page = items[offset : offset + limit]

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
        "items": page,
    }


def add_annotation(
    project_dir: str,
    scope: str,
    annotation_type: str,
    content: str,
    added_by: str = "user",
    question: str = "",
    answer: str = "",
) -> dict:
    """Add an annotation to a scope.

    Args:
        project_dir: Project root directory path.
        scope: Scope key to annotate.
        annotation_type: One of "domain", "architecture", "usage", "ambiguity".
        content: Main content of the annotation.
        added_by: Who added this — "ai" or "user".
        question: Question text (for ambiguity type).
        answer: Answer text (for ambiguity type; empty = unresolved).

    Returns:
        {status: "added"/"not_found", scope}
    """
    if annotation_type not in VALID_ANNOTATION_TYPES:
        return {"status": "invalid_type", "scope": scope, "valid_types": sorted(VALID_ANNOTATION_TYPES)}

    scope = normalize_scope(scope)
    _ensure_cache(project_dir)
    entry = _load_scope(project_dir, scope)

    if entry is None:
        return {"status": "not_found", "scope": scope}

    ann: dict = {
        "type": annotation_type,
        "content": content,
        "added_by": added_by,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    if annotation_type == "ambiguity":
        ann["question"] = question or content
        ann["answer"] = answer

    entry.setdefault("annotations", []).append(ann)
    _save_scope(project_dir, scope, entry)
    return {"status": "added", "scope": scope}


def resolve_ambiguity(
    project_dir: str, scope: str, question: str, answer: str
) -> dict:
    """Resolve an ambiguity-type annotation by saving the user's answer.

    Matches by question text within the scope's annotations.

    Returns:
        {status: "resolved"/"not_found", scope, question}
    """
    scope = normalize_scope(scope)
    _ensure_cache(project_dir)
    entry = _load_scope(project_dir, scope)

    if entry is None:
        return {"status": "not_found", "scope": scope, "question": question}

    matched = False
    for ann in entry.get("annotations", []):
        if ann.get("type") == "ambiguity" and ann.get("question") == question:
            ann["answer"] = answer
            matched = True
            break

    if not matched:
        return {"status": "not_found", "scope": scope, "question": question}

    _save_scope(project_dir, scope, entry)
    return {"status": "resolved", "scope": scope, "question": question}


def build_scope_tree(project_dir: str) -> dict:
    """Build hierarchical scope tree from project structure.

    Discovers all scopes, updates parent/children relationships,
    saves scope_tree to cache _meta.
    """
    from ccx.scanner import discover_scopes

    scopes = discover_scopes(project_dir)
    _ensure_cache(project_dir)

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

    # Load all existing cached scopes
    all_existing = _list_all_scopes(project_dir)
    existing_keys = {e.get("scope", "") for e in all_existing}

    # Identify new and stale scopes
    new_scopes = sorted(discovered_keys - existing_keys)
    stale_scopes = sorted(existing_keys - discovered_keys)

    # Count types
    packages = sum(1 for s in scopes if s["type"] == "package")
    modules = sum(1 for s in scopes if s["type"] == "module")

    # Update existing cache entries with parent/children relationships
    for s in scopes:
        key = s["key"]
        children_list = scope_tree.get(key, [])
        parent = s.get("parent")

        existing_entry = _load_scope(project_dir, key)
        if existing_entry is not None:
            # Update parent/children on existing scope
            existing_entry["children"] = children_list
            existing_entry["parent"] = parent
            _save_scope(project_dir, key, existing_entry)
        else:
            # Create a minimal placeholder entry for new scopes
            placeholder = {
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
                "children": children_list,
                "parent": parent,
            }
            _save_scope(project_dir, key, placeholder)

    # Save scope_tree to _meta
    meta = _load_meta(project_dir)
    meta["scope_tree"] = scope_tree
    _save_meta(project_dir, meta)

    # Rolling limit: evict oldest entries beyond MAX_SCOPES
    all_entries = _list_all_scopes(project_dir)
    if len(all_entries) > MAX_SCOPES:
        all_entries.sort(key=lambda e: e.get("cached_at", ""))
        to_evict = all_entries[: len(all_entries) - MAX_SCOPES]
        for old_entry in to_evict:
            old_scope = old_entry.get("scope", "")
            if old_scope:
                _delete_scope(project_dir, old_scope)

    return {
        "total_scopes": len(scopes),
        "packages": packages,
        "modules": modules,
        "new_scope_count": len(new_scopes),
        "stale_scope_count": len(stale_scopes),
    }


def get_scope_with_children(
    project_dir: str, scope: str, check_staleness: bool = True
) -> dict:
    """Get a scope entry with summaries of all descendant scopes."""
    scope = normalize_scope(scope)
    _ensure_cache(project_dir)
    entry = _load_scope(project_dir, scope)

    if entry is None:
        return {"scope": None, "children": [], "stale": True}

    if check_staleness:
        # Pre-compute git index once instead of N subprocess calls
        git_index = _load_git_index(project_dir)
        stale, _ = _check_staleness_with_index(project_dir, entry, git_index)
    else:
        stale = False

    # Get scope_tree from meta to find descendants
    meta = _load_meta(project_dir)
    scope_tree = meta.get("scope_tree", {})

    # Recursively collect all descendant scopes
    children_summaries: list[dict] = []
    visited: set[str] = set()

    def _collect_descendants(parent_key: str) -> None:
        # Use scope_tree from meta if available, otherwise fall back to entry's children field
        child_keys = scope_tree.get(parent_key, [])
        if not child_keys:
            # Fallback: read the parent's entry to get its children field
            parent_entry = _load_scope(project_dir, parent_key)
            if parent_entry is not None:
                child_keys = parent_entry.get("children", [])

        for child_key in child_keys:
            if child_key in visited:
                continue
            visited.add(child_key)
            child_entry = _load_scope(project_dir, child_key)
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
    _ensure_cache(project_dir)

    marked: list[str] = []

    # Mark the scope itself
    entry = _load_scope(project_dir, scope)
    if entry is not None:
        entry["file_hashes"] = {}
        _save_scope(project_dir, scope, entry)
        marked.append(scope)

    # Walk up through ancestors
    current = scope
    visited: set[str] = {scope}
    while True:
        current_entry = _load_scope(project_dir, current)
        if current_entry is None:
            break
        parent = current_entry.get("parent")
        if parent is None:
            break
        if parent in visited:
            break
        visited.add(parent)
        parent_entry = _load_scope(project_dir, parent)
        if parent_entry is not None:
            parent_entry["file_hashes"] = {}
            _save_scope(project_dir, parent, parent_entry)
            if parent not in marked:
                marked.append(parent)
        current = parent

    return {"marked": marked}
