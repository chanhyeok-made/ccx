"""
Project scanner.
Auto-detects project stack, framework, and structure from filesystem.
"""

import json
from pathlib import Path

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


def _generate_tree(root: Path, max_depth: int = 3) -> str:
    """Generate a directory tree string."""
    lines = []
    _walk_tree(root, lines, prefix="", depth=0, max_depth=max_depth)
    return "\n".join(lines)


def _walk_tree(path: Path, lines: list, prefix: str, depth: int, max_depth: int):
    """Recursive tree walker."""
    if depth > max_depth:
        return

    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return

    # Filter out ignored dirs and hidden files (except important ones)
    entries = [
        e for e in entries
        if e.name not in IGNORE_DIRS
        and not (e.name.startswith(".") and e.name not in {".github", ".env.example"})
        and not e.name.endswith(".pyc")
        and not e.name.endswith(".egg-info")
    ]

    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")

        if entry.is_dir():
            extension = "    " if is_last else "│   "
            _walk_tree(entry, lines, prefix + extension, depth + 1, max_depth)
