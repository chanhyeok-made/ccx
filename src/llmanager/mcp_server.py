"""
MCP Server for llmanager.
Provides project context and session management tools to Claude Code.
"""

from mcp.server.fastmcp import FastMCP

from llmanager.config import load_base_context
from llmanager.session import load_session, save_record, get_context_summary

mcp = FastMCP("llmanager")


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


def main():
    mcp.run()


if __name__ == "__main__":
    main()
