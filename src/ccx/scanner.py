"""
Project scanner.
Auto-detects project stack, framework, and structure from filesystem.
"""

import json
from pathlib import Path

import pathspec

# (marker file, runtime, common frameworks detected from deps)
RUNTIME_MARKERS = [
    # Python
    ("pyproject.toml", "python", ["django", "flask", "fastapi", "starlette", "celery", "sqlalchemy"]),
    ("setup.py", "python", ["django", "flask", "fastapi"]),
    ("requirements.txt", "python", ["django", "flask", "fastapi", "starlette", "celery", "sqlalchemy"]),
    ("Pipfile", "python", []),
    # Node.js
    ("package.json", "node", ["next", "react", "vue", "nuxt", "express", "nestjs", "svelte", "angular"]),
    # Go
    ("go.mod", "go", ["gin", "echo", "fiber", "chi"]),
    # Rust
    ("Cargo.toml", "rust", ["actix", "axum", "rocket", "tokio"]),
    # Java/Kotlin
    ("pom.xml", "java", ["spring"]),
    ("build.gradle", "java/kotlin", ["spring"]),
    ("build.gradle.kts", "kotlin", ["spring", "ktor"]),
    # Ruby
    ("Gemfile", "ruby", ["rails", "sinatra"]),
    # PHP
    ("composer.json", "php", ["laravel", "symfony"]),
    # Swift
    ("Package.swift", "swift", []),
    # Dart/Flutter
    ("pubspec.yaml", "dart/flutter", []),
]

DB_MARKERS = {
    "prisma": ["prisma/schema.prisma", "node_modules/.prisma"],
    "postgresql": [],  # detected from deps
    "mysql": [],
    "mongodb": [],
    "sqlite": [],
    "redis": [],
}

DB_DEP_KEYWORDS = {
    "prisma": "prisma",
    "pg": "postgresql",
    "psycopg": "postgresql",
    "asyncpg": "postgresql",
    "mysql": "mysql",
    "pymysql": "mysql",
    "pymongo": "mongodb",
    "mongoose": "mongodb",
    "sqlite": "sqlite",
    "redis": "redis",
    "sqlalchemy": "sqlalchemy",
    "typeorm": "typeorm",
    "sequelize": "sequelize",
    "drizzle": "drizzle",
}

IGNORE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".idea",
    ".vscode", ".ccx", ".claude", "dist", "build", ".next", ".nuxt",
    "target", ".gradle", "vendor", ".tox", ".mypy_cache", ".pytest_cache",
    "egg-info", ".eggs", "coverage", ".nyc_output",
}


def scan_project(project_dir: str) -> dict:
    """Scan a project directory and return detected context."""
    root = Path(project_dir).resolve()

    runtime, runtime_version = _detect_runtime(root)
    frameworks = _detect_frameworks(root, runtime)
    databases = _detect_databases(root, runtime)
    structure = _generate_tree(root, max_depth=3)

    stack = {}
    if runtime:
        stack["runtime"] = f"{runtime} {runtime_version}" if runtime_version else runtime
    if frameworks:
        stack["framework"] = ", ".join(frameworks)
    if databases:
        stack["database"] = ", ".join(databases)

    return {
        "project_name": root.name,
        "stack": stack,
        "architecture": "",
        "structure": structure,
        "exception_rules": {
            "forbidden": [],
            "required": [],
            "gotchas": [],
        },
    }


def _detect_runtime(root: Path) -> tuple[str, str]:
    """Detect primary runtime and version."""
    for marker_file, runtime, _ in RUNTIME_MARKERS:
        marker = root / marker_file
        if marker.exists():
            version = _extract_version(marker, runtime)
            return runtime, version
    return "", ""


def _extract_version(marker: Path, runtime: str) -> str:
    """Try to extract runtime version from config file."""
    try:
        text = marker.read_text(encoding="utf-8", errors="ignore")

        if runtime == "python" and marker.name == "pyproject.toml":
            for line in text.splitlines():
                if "python" in line and ("=" in line or ">" in line):
                    # e.g. python = "^3.11"
                    parts = line.split('"')
                    if len(parts) >= 2:
                        return parts[1].lstrip("^~>=<")
                    break

        if runtime == "node" and marker.name == "package.json":
            data = json.loads(text)
            engines = data.get("engines", {})
            if "node" in engines:
                return engines["node"].lstrip("^~>=<")

        if runtime == "go" and marker.name == "go.mod":
            for line in text.splitlines():
                if line.startswith("go "):
                    return line.split()[1]
    except Exception:
        pass
    return ""


def _detect_frameworks(root: Path, runtime: str) -> list[str]:
    """Detect frameworks from dependency files."""
    deps_text = _read_deps(root, runtime)
    if not deps_text:
        return []

    detected = []
    for marker_file, rt, frameworks in RUNTIME_MARKERS:
        if rt == runtime:
            for fw in frameworks:
                if fw.lower() in deps_text.lower():
                    detected.append(fw)

    return list(dict.fromkeys(detected))  # dedupe preserving order


def _detect_databases(root: Path, runtime: str) -> list[str]:
    """Detect databases from markers and dependencies."""
    detected = []

    # Check file markers
    for db, markers in DB_MARKERS.items():
        for m in markers:
            if (root / m).exists():
                detected.append(db)
                break

    # Check deps
    deps_text = _read_deps(root, runtime)
    if deps_text:
        for keyword, db in DB_DEP_KEYWORDS.items():
            if keyword.lower() in deps_text.lower() and db not in detected:
                detected.append(db)

    return detected


def _read_deps(root: Path, runtime: str) -> str:
    """Read dependency file contents as a single string for keyword matching."""
    candidates = []
    if runtime == "python":
        candidates = ["pyproject.toml", "requirements.txt", "Pipfile"]
    elif runtime == "node":
        candidates = ["package.json"]
    elif runtime == "go":
        candidates = ["go.mod"]
    elif runtime == "rust":
        candidates = ["Cargo.toml"]
    elif runtime == "ruby":
        candidates = ["Gemfile"]
    elif runtime == "php":
        candidates = ["composer.json"]

    parts = []
    for c in candidates:
        p = root / c
        if p.exists():
            try:
                parts.append(p.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                pass
    return "\n".join(parts)


def _load_gitignore(root: Path) -> pathspec.PathSpec | None:
    """Load .gitignore patterns from the project root."""
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        return None
    try:
        text = gitignore.read_text(encoding="utf-8", errors="ignore")
        return pathspec.PathSpec.from_lines("gitwildmatch", text.splitlines())
    except Exception:
        return None


def _generate_tree(root: Path, max_depth: int = 3) -> str:
    """Generate a directory tree string."""
    spec = _load_gitignore(root)
    lines = []
    _walk_tree(root, lines, prefix="", depth=0, max_depth=max_depth, root=root, spec=spec)
    return "\n".join(lines)


def _walk_tree(
    path: Path,
    lines: list,
    prefix: str,
    depth: int,
    max_depth: int,
    root: Path,
    spec: pathspec.PathSpec | None,
):
    """Recursive tree walker."""
    if depth > max_depth:
        return

    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return

    # Filter out ignored dirs and hidden files (except important ones)
    filtered = []
    for e in entries:
        if e.name in IGNORE_DIRS:
            continue
        if e.name.startswith(".") and e.name not in {".github", ".env.example"}:
            continue
        if e.name.endswith(".pyc") or e.name.endswith(".egg-info"):
            continue
        if spec is not None:
            rel = str(e.relative_to(root))
            if e.is_dir():
                rel += "/"
            if spec.match_file(rel):
                continue
        filtered.append(e)

    for i, entry in enumerate(filtered):
        is_last = i == len(filtered) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")

        if entry.is_dir():
            extension = "    " if is_last else "│   "
            _walk_tree(entry, lines, prefix + extension, depth + 1, max_depth, root, spec)


# ---------------------------------------------------------------------------
# Scope discovery
# ---------------------------------------------------------------------------

# File extensions per language
_LANG_EXTENSIONS = {
    "python": {".py"},
    "node": {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"},
    "go": {".go"},
    "rust": {".rs"},
    "java": {".java"},
    "java/kotlin": {".java", ".kt"},
    "kotlin": {".kt", ".kts"},
    "ruby": {".rb"},
    "php": {".php"},
}


def discover_scopes(project_dir: str) -> list[dict]:
    """Discover analyzable scopes (modules/packages) in a project.

    Returns list of dicts:
    [
        {
            "key": "src/ccx/scanner",        # normalized scope key
            "path": "src/ccx/scanner.py",    # actual relative path
            "type": "module" | "package",    # file vs directory
            "files": ["src/ccx/scanner.py"], # included files
            "parent": "src/ccx" | None,      # parent package key
            "language": "python"
        },
        ...
    ]
    """
    root = Path(project_dir).resolve()
    runtime, _ = _detect_runtime(root)
    if not runtime:
        return []

    language = runtime
    spec = _load_gitignore(root)
    extensions = _LANG_EXTENSIONS.get(language, set())

    scopes: list[dict] = []
    # Track discovered package dirs for parent resolution
    package_dirs: set[str] = set()

    _discover_walk(root, root, language, extensions, spec, scopes, package_dirs)

    # Resolve parent keys
    for scope in scopes:
        scope["parent"] = _find_parent(scope["key"], package_dirs)

    # Sort by key for stable output
    scopes.sort(key=lambda s: s["key"])
    return scopes


def _discover_walk(
    path: Path,
    root: Path,
    language: str,
    extensions: set[str],
    spec: pathspec.PathSpec | None,
    scopes: list[dict],
    package_dirs: set[str],
) -> None:
    """Recursively walk directories to discover scopes."""
    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except (PermissionError, OSError):
        return

    dirs: list[Path] = []
    files: list[Path] = []

    for e in entries:
        if e.name in IGNORE_DIRS:
            continue
        if e.name.startswith("."):
            continue
        if spec is not None:
            rel = str(e.relative_to(root))
            if e.is_dir():
                rel += "/"
            if spec.match_file(rel):
                continue
        if e.is_dir() and not e.is_symlink():
            dirs.append(e)
        elif e.is_file():
            files.append(e)

    # Check if current directory is a package
    is_package = _is_package_dir(path, language)
    if is_package and path != root:
        rel_dir = str(path.relative_to(root))
        key = rel_dir.replace("\\", "/").lower()
        pkg_files = _collect_files(path, root, extensions, spec)
        if pkg_files:
            scopes.append({
                "key": key,
                "path": rel_dir.replace("\\", "/"),
                "type": "package",
                "files": pkg_files,
                "parent": None,  # resolved later
                "language": language,
            })
            package_dirs.add(key)

    # Discover standalone module files (only in non-package dirs or at root)
    if not is_package or path == root:
        for f in files:
            if f.suffix in extensions:
                rel_path = str(f.relative_to(root)).replace("\\", "/")
                key = rel_path.lower()
                # Strip extension for key normalization
                for ext in sorted(extensions, key=len, reverse=True):
                    if key.endswith(ext):
                        key = key[: -len(ext)]
                        break
                scopes.append({
                    "key": key,
                    "path": rel_path,
                    "type": "module",
                    "files": [rel_path],
                    "parent": None,  # resolved later
                    "language": language,
                })

    # Recurse into subdirectories
    for d in dirs:
        _discover_walk(d, root, language, extensions, spec, scopes, package_dirs)


def _is_package_dir(path: Path, language: str) -> bool:
    """Check if a directory qualifies as a package for the given language."""
    if language == "python":
        return (path / "__init__.py").exists()

    if language == "node":
        return (path / "package.json").exists()

    if language == "go":
        # Any directory with .go files under a go.mod project
        try:
            return any(f.suffix == ".go" for f in path.iterdir() if f.is_file())
        except (PermissionError, OSError):
            return False

    if language == "rust":
        return (
            (path / "Cargo.toml").exists()
            or (path / "mod.rs").exists()
            or (path / "lib.rs").exists()
        )

    if language in ("java", "java/kotlin", "kotlin"):
        # src/main/java subdirectory structure or build file presence
        if (path / "pom.xml").exists() or (path / "build.gradle").exists() or (path / "build.gradle.kts").exists():
            return True
        # Any dir under src/main/java with .java/.kt files
        rel = str(path)
        if "src/main/java" in rel or "src/main/kotlin" in rel:
            try:
                return any(
                    f.suffix in (".java", ".kt")
                    for f in path.iterdir()
                    if f.is_file()
                )
            except (PermissionError, OSError):
                return False

    return False


def _collect_files(
    pkg_dir: Path,
    root: Path,
    extensions: set[str],
    spec: pathspec.PathSpec | None,
) -> list[str]:
    """Collect all matching source files within a package directory (non-recursive)."""
    result = []
    try:
        for f in sorted(pkg_dir.iterdir(), key=lambda e: e.name.lower()):
            if not f.is_file():
                continue
            if f.suffix not in extensions:
                continue
            if f.name.startswith("."):
                continue
            rel = str(f.relative_to(root)).replace("\\", "/")
            if spec is not None and spec.match_file(rel):
                continue
            result.append(rel)
    except (PermissionError, OSError):
        pass
    return result


def _find_parent(key: str, package_dirs: set[str]) -> str | None:
    """Find the closest parent package key for a given scope key."""
    parts = key.rsplit("/", 1)
    while len(parts) == 2:
        candidate = parts[0]
        if candidate in package_dirs:
            return candidate
        parts = candidate.rsplit("/", 1)
    return None
