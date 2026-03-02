"""
MCP Server for ccx.
Provides project context and session management tools to Claude Code.
"""

from mcp.server.fastmcp import FastMCP

from ccx.config import load_base_context
from ccx.session import load_session, save_record, get_context_summary
from ccx.analysis_cache import (
    get_analysis_cache as _get_cache,
    save_analysis_cache as _save_cache,
    invalidate_cache as _invalidate_cache,
    list_cached_scopes as _list_scopes,
)

mcp = FastMCP("ccx")


@mcp.tool()
def load_project_context(project_dir: str) -> dict:
    """Load project base context (stack, architecture, structure, exception rules) from base-context.yaml."""
    return load_base_context(project_dir)


@mcp.tool()
def check_rules(changes_description: str, project_dir: str) -> dict:
    """Check if described changes violate any project exception rules.

    Args:
        changes_description: Natural language description of changes made.
        project_dir: Project root directory path.

    Returns:
        Dict with exception_rules and a reminder to verify each rule against the changes.
    """
    ctx = load_base_context(project_dir)
    rules = ctx.get("exception_rules", {})

    forbidden = rules.get("forbidden", [])
    required = rules.get("required", [])
    gotchas = rules.get("gotchas", [])

    checklist = []
    for rule in forbidden:
        r = rule if isinstance(rule, str) else rule.get("rule", "")
        reason = "" if isinstance(rule, str) else rule.get("reason", "")
        checklist.append({"type": "FORBIDDEN", "rule": r, "reason": reason})

    for rule in required:
        r = rule if isinstance(rule, str) else rule.get("rule", "")
        reason = "" if isinstance(rule, str) else rule.get("reason", "")
        checklist.append({"type": "REQUIRED", "rule": r, "reason": reason})

    for gotcha in gotchas:
        checklist.append({"type": "GOTCHA", "rule": gotcha, "reason": ""})

    return {
        "checklist": checklist,
        "changes_description": changes_description,
        "instruction": "Verify each rule against the changes. Report any violations.",
    }


@mcp.tool()
def get_session(project_dir: str, limit: int = 10) -> dict:
    """Get recent execution history and context summary.

    Args:
        project_dir: Project root directory path.
        limit: Maximum number of recent records to return.
    """
    records = load_session(project_dir, limit=limit)
    summary = get_context_summary(project_dir)
    return {
        "records": records,
        "context_summary": summary,
    }


@mcp.tool()
def record_execution(
    project_dir: str,
    request: str,
    success: bool,
    summary: str = "",
    changes: list | None = None,
) -> dict:
    """Record a pipeline execution result for future session context.

    Args:
        project_dir: Project root directory path.
        request: The original user request.
        success: Whether the execution succeeded.
        summary: Brief summary of what was done.
        changes: List of file changes (each with path, type, intent).
    """
    record = save_record(
        project_dir=project_dir,
        request=request,
        success=success,
        summary=summary,
        changes=changes,
    )
    return {"status": "recorded", "record": record}


@mcp.tool()
def get_analysis_cache(
    project_dir: str, scope: str, check_staleness: bool = True
) -> dict:
    """Look up cached analysis for a scope before re-analyzing.

    Args:
        project_dir: Project root directory path.
        scope: File-path-based scope key (e.g. "src/ccx/mcp_server", "src/api/routes"). Auto-normalized: lowercase, no extension, forward slashes.
        check_staleness: Whether to check if cached data is stale via git/mtime.

    Returns:
        Dict with hit (bool), stale (bool), entry (cached data or None), stale_reason.
    """
    return _get_cache(project_dir, scope, check_staleness)


@mcp.tool()
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
    """Save analysis results for a scope to cache for future reuse.

    Args:
        project_dir: Project root directory path.
        scope: File-path-based scope key (e.g. "src/ccx/mcp_server", "src/api/routes"). Auto-normalized.
        summary: Concise summary of what this scope does.
        key_files: List of key file paths in this scope.
        interfaces: Public interfaces / exports.
        known_issues: Known issues or tech debt.
        patterns: Design patterns used.
        dependencies: Dependencies on other scopes.
        cached_by_request: The user request that triggered this analysis.
        extra: Additional structured data for future extensions.

    Returns:
        Dict with status and scope.
    """
    return _save_cache(
        project_dir=project_dir,
        scope=scope,
        summary=summary,
        key_files=key_files,
        interfaces=interfaces,
        known_issues=known_issues,
        patterns=patterns,
        dependencies=dependencies,
        cached_by_request=cached_by_request,
        extra=extra,
    )


@mcp.tool()
def invalidate_analysis_cache(project_dir: str, scope: str) -> dict:
    """Invalidate cached analysis for a scope after implementation changes it.

    Args:
        project_dir: Project root directory path.
        scope: File-path-based scope key to invalidate (e.g. "src/ccx/mcp_server"). Auto-normalized.

    Returns:
        Dict with status (invalidated/not_found) and scope.
    """
    return _invalidate_cache(project_dir, scope)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
