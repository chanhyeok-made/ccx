"""
MCP Server for ccx.
Provides project context and session management tools to Claude Code.
"""

from mcp.server.fastmcp import FastMCP

from ccx.config import load_base_context
from ccx.session import load_session, save_record, get_context_summary
from ccx.agent_config import (
    get_agent_config as _get_agent_config,
    save_agent_config as _save_agent_config,
    delete_agent_config as _delete_agent_config,
    list_agent_configs as _list_agent_configs,
)
from ccx.token_tracker import (
    get_session_usage as _get_session_usage,
    list_session_usages as _list_session_usages,
)
from ccx.context_tracker import (
    get_context_usage as _get_context_usage,
    list_context_usages as _list_context_usages,
)
from ccx.analysis_cache import (
    get_analysis_cache as _get_cache,
    save_analysis_cache as _save_cache,
    invalidate_cache as _invalidate_cache,
    list_cached_scopes as _list_scopes,
    build_scope_tree as _build_scope_tree,
    get_scope_with_children as _get_scope_children,
    mark_stale_cascade as _mark_stale,
    get_pending_scopes as _get_pending,
    get_pending_summary as _get_pending_summary,
    get_annotations as _get_annotations,
    add_annotation as _add_annotation,
    resolve_ambiguity as _resolve_ambiguity,
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
def get_token_usage(project_dir: str, session_id: str = "") -> dict:
    """Get token usage statistics for a session or list recent sessions.

    If session_id is provided, returns detailed per-agent token breakdown.
    If session_id is empty, returns summary of recent sessions.

    Args:
        project_dir: Project root directory path.
        session_id: Specific session ID. Empty string lists recent sessions.

    Returns:
        Dict with session token usage details or list of recent sessions.
    """
    if session_id:
        return _get_session_usage(project_dir, session_id)
    return _list_session_usages(project_dir)


@mcp.tool()
def get_context_usage(project_dir: str, session_id: str = "") -> dict:
    """Get context window usage statistics for a session or list recent sessions.

    Tracks per-turn context fill (input + cache tokens), max/avg fill rates,
    and compaction events where context was compressed.

    Args:
        project_dir: Project root directory path.
        session_id: Specific session ID. Empty string lists recent sessions.

    Returns:
        Dict with context usage details or list of recent sessions.
    """
    if session_id:
        return _get_context_usage(project_dir, session_id)
    return _list_context_usages(project_dir)


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
    file_hashes: dict[str, str] | None = None,
    children: list[str] | None = None,
    parent: str | None = None,
    scope_tree: dict[str, list[str]] | None = None,
    annotations: list[dict] | None = None,
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
        file_hashes: Mapping of relative file paths to git blob hashes for staleness detection.
        children: List of child scope keys in the scope hierarchy.
        parent: Parent scope key in the scope hierarchy.
        scope_tree: Full scope tree mapping to store in cache metadata.
        annotations: Typed annotations. Each: {type, content, added_by, added_at, question?, answer?}.

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
        file_hashes=file_hashes,
        children=children,
        parent=parent,
        scope_tree=scope_tree,
        annotations=annotations,
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


@mcp.tool()
def list_cached_scopes(project_dir: str) -> dict:
    """List all cached analysis scopes with brief info.

    Args:
        project_dir: Project root directory path.

    Returns:
        Dict with scopes list and count.
    """
    return _list_scopes(project_dir)


@mcp.tool()
def trigger_index(project_dir: str) -> dict:
    """Discover project scopes and build hierarchical scope tree.

    Scans the project for modules/packages, builds parent-children relationships,
    and identifies new/stale scopes. Does NOT perform code analysis —
    returns compact counts for the caller to decide what to analyze.

    Use get_pending_scopes() to retrieve the actual scopes needing analysis, paginated.

    Args:
        project_dir: Project root directory path.

    Returns:
        Dict with total_scopes, packages, modules, new_scope_count, stale_scope_count.
    """
    return _build_scope_tree(project_dir)


@mcp.tool()
def get_pending_scopes(
    project_dir: str,
    scope_type: str = "all",
    offset: int = 0,
    limit: int = 50,
    prefix: str = "",
) -> dict:
    """Get scopes needing analysis (empty summary), paginated.

    Returns scopes sorted: modules first, then packages by depth (deepest first).
    Use after trigger_index() to get work batches for indexing.

    Args:
        project_dir: Project root directory path.
        scope_type: Filter — "module", "package", or "all" (default).
        offset: Skip this many scopes (for pagination).
        limit: Max scopes per page (default 50).
        prefix: Filter scopes whose key starts with this prefix (e.g. "src/api").

    Returns:
        Dict with total_pending, offset, limit, has_more, scopes list.
    """
    return _get_pending(project_dir, scope_type, offset, limit, prefix)


@mcp.tool()
def get_pending_summary(project_dir: str, group_depth: int = 1) -> dict:
    """Get a compact summary of pending scopes grouped by top-level directory.

    Use after trigger_index() to assess the scale of indexing work
    and present groups to the user for selection.

    Args:
        project_dir: Project root directory path.
        group_depth: Number of path segments for grouping (default 1).

    Returns:
        Dict with total_pending, module_count, package_count, groups list.
    """
    return _get_pending_summary(project_dir, group_depth)


@mcp.tool()
def get_annotations(
    project_dir: str,
    scope: str = "",
    annotation_type: str = "all",
    unresolved_only: bool = False,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """Get annotations across scopes with optional filters.

    Annotation types: "domain" (business context), "architecture" (design rationale),
    "usage" (how-to, gotchas), "ambiguity" (AI questions needing user answers).

    Args:
        project_dir: Project root directory path.
        scope: Filter to a specific scope (empty = all scopes).
        annotation_type: Filter by type — "domain", "architecture", "usage", "ambiguity", or "all".
        unresolved_only: If True, only return ambiguity annotations without answers.
        offset: Pagination offset.
        limit: Max items per page (default 20).

    Returns:
        Dict with total, offset, limit, has_more, items list.
    """
    return _get_annotations(project_dir, scope, annotation_type, unresolved_only, offset, limit)


@mcp.tool()
def add_annotation(
    project_dir: str,
    scope: str,
    annotation_type: str,
    content: str,
    added_by: str = "user",
    question: str = "",
    answer: str = "",
) -> dict:
    """Add an annotation to a scope's cached analysis.

    Use to enrich cached analysis with domain knowledge, architecture rationale,
    usage guides, or ambiguity questions.

    Args:
        project_dir: Project root directory path.
        scope: Scope key to annotate.
        annotation_type: One of "domain", "architecture", "usage", "ambiguity".
        content: Main content of the annotation.
        added_by: Who added — "ai" or "user".
        question: Question text (for ambiguity type only).
        answer: Answer text (for ambiguity type; empty = unresolved).

    Returns:
        Dict with status (added/not_found), scope.
    """
    return _add_annotation(project_dir, scope, annotation_type, content, added_by, question, answer)


@mcp.tool()
def resolve_ambiguity(
    project_dir: str, scope: str, question: str, answer: str
) -> dict:
    """Resolve an ambiguity-type annotation by saving the user's answer.

    Matches by exact question text within the scope's annotations.

    Args:
        project_dir: Project root directory path.
        scope: Scope key containing the ambiguity.
        question: The exact question text to match.
        answer: The user's answer to save.

    Returns:
        Dict with status (resolved/not_found), scope, question.
    """
    return _resolve_ambiguity(project_dir, scope, question, answer)


@mcp.tool()
def get_scope_with_children(project_dir: str, scope: str, check_staleness: bool = True) -> dict:
    """Get a scope's cached analysis with summaries of all descendant scopes.

    Args:
        project_dir: Project root directory path.
        scope: Scope key (e.g., "src/ccx/scanner").
        check_staleness: Whether to check if scopes are stale.
    """
    return _get_scope_children(project_dir, scope, check_staleness)


@mcp.tool()
def mark_stale_cascade(project_dir: str, scope: str) -> dict:
    """Mark a scope and all its ancestor scopes as stale.

    Use after modifying files to ensure parent scopes are re-analyzed.

    Args:
        project_dir: Project root directory path.
        scope: Scope key to mark as stale.
    """
    return _mark_stale(project_dir, scope)


@mcp.tool()
def get_agent_config(project_dir: str, agent_name: str) -> dict:
    """Get agent-specific rules, context, and disabled rules.

    Args:
        project_dir: Project root directory path.
        agent_name: Agent name (e.g. "implementer", "reviewer").

    Returns:
        Dict with agent, rules, context, disabled_rules, exists.
    """
    return _get_agent_config(project_dir, agent_name)


@mcp.tool()
def save_agent_config(
    project_dir: str,
    agent_name: str,
    rules: list[str] | None = None,
    context: str | None = None,
    disabled_rules: list[str] | None = None,
) -> dict:
    """Save or update agent-specific configuration.

    Args:
        project_dir: Project root directory path.
        agent_name: Agent name.
        rules: Custom rules for this agent.
        context: Additional context string.
        disabled_rules: Base-context rules to disable for this agent.

    Returns:
        Dict with status and agent name.
    """
    return _save_agent_config(project_dir, agent_name, rules, context, disabled_rules)


@mcp.tool()
def delete_agent_config(project_dir: str, agent_name: str) -> dict:
    """Delete agent-specific configuration file.

    Args:
        project_dir: Project root directory path.
        agent_name: Agent name.

    Returns:
        Dict with status (deleted/not_found) and agent name.
    """
    return _delete_agent_config(project_dir, agent_name)


@mcp.tool()
def list_agent_configs(project_dir: str) -> dict:
    """List all agents and their configuration status.

    Args:
        project_dir: Project root directory path.

    Returns:
        Dict with agents list and configured_count.
    """
    return _list_agent_configs(project_dir)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
