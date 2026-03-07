# ccx -- Setup & Usage

## What is ccx

ccx (Claude Code eXtension) is a Claude Code native extension that provides a project-aware development pipeline through Skills and an MCP Server. Claude Code acts as the orchestrator; ccx supplies the skills (pipeline logic) and MCP tools (project context, session, analysis cache) that subagents consume.

## Prerequisites

- **Python 3.11+**
- **Claude Code CLI** installed and authenticated
  ```bash
  npm install -g @anthropic-ai/claude-code
  ```

## Installation

With pip (editable install):

```bash
cd llmanager
pip install -e .
```

Or with Poetry:

```bash
cd llmanager
poetry install
```

Both methods install the `ccx` CLI command (entry point: `ccx.cli:main`).

## Quick Start

```bash
# 1. Initialize ccx in your project
ccx init /path/to/your/project

# 2. (Optional) Review the auto-generated base-context.yaml
#    Edit it to add project-specific exception rules or architecture notes.

# 3. Start Claude Code in your project and run the pipeline
cd /path/to/your/project
claude
# Then use: /project:run [your request]
```

`ccx init` performs the following:
- Copies skill templates to `.claude/skills/`
- Copies hook scripts to `.claude/hooks/`
- Configures hooks in `.claude/settings.json`
- Creates `.mcp.json` with the ccx MCP server config
- Scans the project and generates `base-context.yaml`
- Creates the `.ccx/` directory (session data, logs, analysis cache)

## CLI Commands

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `ccx init [project_dir]` | Initialize ccx in a project directory | `--force / -f` -- overwrite existing files |
| `ccx update [project_dir]` | Update skill templates and hooks to latest version | -- |
| `ccx status [project_dir]` | Check ccx installation status (skills, hooks, MCP config, base-context) | -- |
| `ccx index [project_dir]` | Discover and index project scopes for analysis caching | `--reset` -- clear cache before indexing; `--verbose / -v` -- show scope tree |

`project_dir` defaults to `.` (current directory) for all commands.

## Skills

Skills are invoked inside Claude Code with the `/project:` prefix.

| Skill | Description |
|-------|-------------|
| `/project:run [request]` | Full development pipeline: analyze, plan, implement, review, commit. Orchestrates subagents through all phases with mandatory checkpoints. |
| `/project:analyze [request]` | Standalone analysis: analyze a request and produce structured requirements. |
| `/project:review [files or scope]` | Review recent code changes against project exception rules. |
| `/project:commit [context]` | Generate and create a conventional commit for current changes. |
| `/project:index [--force]` | Perform code-level analysis on all project scopes and cache results. Incremental by default; `--force` re-analyzes everything. |

## MCP Tools

The ccx MCP server (`ccx.mcp_server`) exposes the following tools:

| Tool | Description |
|------|-------------|
| `load_project_context` | Load project base context (stack, architecture, structure, exception rules) from `base-context.yaml`. |
| `check_rules` | Check if described changes violate any project exception rules. |
| `get_session` | Get recent execution history and context summary. |
| `record_execution` | Record a pipeline execution result for future session context. |
| `get_analysis_cache` | Look up cached analysis for a scope before re-analyzing. |
| `save_analysis_cache` | Save analysis results for a scope to cache for future reuse. |
| `invalidate_analysis_cache` | Invalidate cached analysis for a scope after implementation changes it. |
| `list_cached_scopes` | List all cached analysis scopes with brief info. |
| `trigger_index` | Discover project scopes and build hierarchical scope tree (no code analysis). |
| `get_scope_with_children` | Get a scope's cached analysis with summaries of all descendant scopes. |
| `mark_stale_cascade` | Mark a scope and all its ancestor scopes as stale. |

## Architecture

```
Claude Code (orchestrator)
    |
    |-- Skills (.claude/skills/)      -- pipeline logic, subagent prompts
    |-- MCP Tools (.mcp.json)         -- project context, session, analysis cache
    |
    +-- Subagents                     -- spawned by orchestrator for each phase
            |
            +-- MCP calls             -- subagents load context directly via MCP
```

Key design decisions:
- **Main agent = pure orchestrator.** It coordinates phases and handles user interaction but does not read files or load project context itself.
- **All heavy work is delegated to subagents.** Each subagent loads context via MCP tools directly.
- **User interaction happens only through the main agent.** Subagents return structured status (COMPLETE / NEEDS_CONTEXT) and never prompt the user.
- **Pipeline phases:** Index (optional) -> Analyze -> Plan -> Execute (Research, Implement, Review) -> Commit & Push -> Record. Mandatory checkpoints after Analyze, Plan, and Execute.

## Directory Structure

```
src/ccx/
    __init__.py
    __main__.py
    cli.py                  -- Setup CLI (init, update, status, index)
    mcp_server.py           -- FastMCP server, 11 tools
    config.py               -- base-context.yaml loader
    scanner.py              -- Project auto-scan (runtime, framework, db, tree)
    session.py              -- .ccx/session.json file-based session persistence
    analysis_cache.py       -- Scope-based analysis cache with staleness detection
    logger.py               -- MCP tool call logging
    base-context.example.yaml
    hooks/
        log_event.sh        -- Hook script for event logging
    skills/
        run/
            SKILL.md        -- /project:run entry point
            PIPELINE.md     -- Detailed pipeline logic
        analyze/
            SKILL.md        -- /project:analyze
        review/
            SKILL.md        -- /project:review
        commit/
            SKILL.md        -- /project:commit
        index/
            SKILL.md        -- /project:index
```

## Dependencies

| Package | Version |
|---------|---------|
| python | ^3.11 |
| pyyaml | ^6.0 |
| click | ^8.0 |
| mcp[cli] | >=1.0 |
| pathspec | ^0.12 |
