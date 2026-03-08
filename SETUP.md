# ccx -- Setup & Usage

## What is ccx

ccx (Claude Code eXtension) is a Claude Code plugin that provides a project-aware development pipeline through Skills and an MCP Server. It is distributed as a Claude Code plugin (`.claude-plugin/`), which bundles skills, hooks, and MCP server configuration into a single installable unit. Claude Code acts as the orchestrator; ccx supplies the skills (pipeline logic) and MCP tools (project context, session, analysis cache) that subagents consume.

## Prerequisites

- **Python 3.11+**
- **Claude Code CLI** installed and authenticated
  ```bash
  npm install -g @anthropic-ai/claude-code
  ```

## Installation

Install ccx as a Claude Code plugin:

```bash
claude plugin install /path/to/ccx
```

This registers the plugin with Claude Code, making all skills, hooks, and MCP tools available in any project where the plugin is active. The plugin reads its configuration from `.claude-plugin/plugin.json`.

### Python Dependencies

The ccx MCP server requires the Python package to be installed:

```bash
cd ccx
pip install -e .
# or with Poetry:
poetry install
```

## Quick Start

```bash
# 1. Install the plugin (one-time)
claude plugin install /path/to/ccx

# 2. Initialize ccx in your project (generates base-context.yaml and .ccx/)
ccx init /path/to/your/project

# 3. (Optional) Review the auto-generated base-context.yaml
#    Edit it to add project-specific exception rules or architecture notes.

# 4. Start Claude Code in your project and run the pipeline
cd /path/to/your/project
claude
# Then use: /ccx:run [your request]
```

`ccx init` performs the following:
- Scans the project and generates `base-context.yaml`
- Creates the `.ccx/` directory (session data, logs, analysis cache)

Skills, hooks, and MCP configuration are provided by the plugin and do not need to be copied into the project.

## CLI Commands

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `ccx init [project_dir]` | Initialize ccx in a project directory (generates `base-context.yaml` and `.ccx/`) | `--force / -f` -- overwrite existing files |
| `ccx update [project_dir]` | Upgrade ccx package to latest version | -- |
| `ccx status [project_dir]` | Check ccx installation status (base-context, MCP config) | -- |
| `ccx index [project_dir]` | Discover and index project scopes for analysis caching | `--reset` -- clear cache before indexing; `--verbose / -v` -- show scope tree |

`project_dir` defaults to `.` (current directory) for all commands.

## Skills

Skills are invoked inside Claude Code with the `/ccx:` prefix.

| Skill | Description |
|-------|-------------|
| `/ccx:run [request]` | Full development pipeline: analyze, plan, implement, review, commit. Orchestrates subagents through all phases with mandatory checkpoints. |
| `/ccx:analyze [request]` | Standalone analysis: analyze a request and produce structured requirements. |
| `/ccx:review [files or scope]` | Review recent code changes against project exception rules. |
| `/ccx:commit [context]` | Generate and create a conventional commit for current changes. |
| `/ccx:index [--force]` | Perform code-level analysis on all project scopes and cache results. Incremental by default; `--force` re-analyzes everything. |
| `/ccx:resolve` | Manage annotations and resolve ambiguities flagged during indexing. |

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
| `get_pending_scopes` | Paginated list of scopes needing analysis (supports prefix filter). |
| `get_pending_summary` | Grouped counts of unanalyzed scopes by directory. |
| `get_annotations` | Query annotations by scope/type (supports unresolved_only filter). |
| `add_annotation` | Add domain/architecture/usage/ambiguity annotation to a scope. |
| `resolve_ambiguity` | Resolve an ambiguity annotation with an answer. |

## Architecture

```
Claude Code (orchestrator)
    |
    |-- Plugin (.claude-plugin/)
    |       |-- Skills              -- pipeline logic, subagent prompts
    |       |-- Hooks               -- event logging
    |       +-- MCP config          -- server connection settings
    |
    |-- MCP Server (ccx.mcp_server) -- project context, session, analysis cache
    |
    +-- Subagents                   -- spawned by orchestrator for each phase
            |
            +-- MCP calls           -- subagents load context directly via MCP
```

Key design decisions:
- **Plugin-based distribution.** Skills, hooks, and MCP configuration are bundled in `.claude-plugin/` and installed via `claude plugin install`. No manual copying of files into `.claude/` is needed.
- **Main agent = pure orchestrator.** It coordinates phases and handles user interaction but does not read files or load project context itself.
- **All heavy work is delegated to subagents.** Each subagent loads context via MCP tools directly.
- **User interaction happens only through the main agent.** Subagents return structured status (COMPLETE / NEEDS_CONTEXT) and never prompt the user.
- **Pipeline phases:** Index (optional) -> Analyze -> Plan -> Execute (Research, Implement, Review) -> Commit & Push -> Record. Mandatory checkpoints after Analyze, Plan, and Execute.

## Directory Structure

```
.claude-plugin/
    plugin.json             -- Plugin manifest (skills, hooks, MCP config)
    mcp.json                -- MCP server connection configuration
    skills/
        run/
            SKILL.md        -- /ccx:run entry point
            PIPELINE.md     -- Detailed pipeline logic
        analyze/
            SKILL.md        -- /ccx:analyze
        review/
            SKILL.md        -- /ccx:review
        commit/
            SKILL.md        -- /ccx:commit
        index/
            SKILL.md        -- /ccx:index
        resolve/
            SKILL.md        -- /ccx:resolve
    hooks/
        hooks.json          -- Hook configuration (event matchers)
        log_event.sh        -- Hook script for event logging
        log_event.py        -- Python handler for event logging

src/ccx/
    __init__.py
    __main__.py
    cli.py                  -- Setup CLI (init, update, status, index)
    mcp_server.py           -- FastMCP server, 16 tools
    config.py               -- base-context.yaml loader
    scanner.py              -- Project auto-scan (runtime, framework, db, tree)
    session.py              -- .ccx/session.json file-based session persistence
    analysis_cache.py       -- Scope-based analysis cache with staleness detection
    logger.py               -- MCP tool call logging
    base-context.example.yaml
```

## Dependencies

| Package | Version |
|---------|---------|
| python | ^3.11 |
| pyyaml | ^6.0 |
| click | ^8.0 |
| mcp[cli] | >=1.0 |
| pathspec | ^0.12 |
