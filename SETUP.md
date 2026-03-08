# ccx -- Setup & Usage

## What is ccx

ccx (Claude Code eXtension) is a Claude Code plugin that provides a project-aware development pipeline through Skills and an MCP Server. Skills, hooks, and MCP configuration live at the repository root and are discovered automatically by Claude Code's convention-based plugin system. Claude Code acts as the orchestrator; ccx supplies the skills (pipeline logic) and MCP tools (project context, session, analysis cache) that subagents consume.

## Prerequisites

- **Python 3.11+** -- check with `python3 --version`
- **Claude Code CLI** installed and authenticated
  ```bash
  npm install -g @anthropic-ai/claude-code
  ```

## Installation

### 1. Clone the ccx repository

First, clone ccx to your local machine. You can put it anywhere you like -- your home directory, a tools folder, etc.

```bash
# Example: clone into your home directory
cd ~
git clone https://github.com/chanhyeok-made/ccx.git

# The cloned folder (e.g. ~/ccx) is what we refer to as the "ccx directory" below.
```

### 2. Install Python dependencies

The ccx MCP server requires the Python package to be installed. Open a terminal, navigate to the cloned ccx directory, and run:

```bash
cd ~/ccx          # wherever you cloned ccx
pip install -e .
# or, if you use Poetry:
poetry install
```

> **Tip:** If you have multiple Python versions, make sure you use `pip3` or `python3 -m pip` to install into the correct environment.

### 3. Register ccx as a Claude Code plugin

Claude Code plugins are installed through **marketplaces**.

```bash
# Step A: Register the marketplace (one-time setup)
claude plugin marketplace add chanhyeok-made/claude-plugins

# Step B: Install the ccx plugin
claude plugin install ccx@chanhyeok-plugins
```

> **Quick test without installing:** You can try ccx in a single session without persistent installation:
> ```bash
> claude --plugin-dir ~/ccx    # path to your cloned ccx repo
> ```
> This loads the plugin for that session only — it won't persist when you restart Claude Code.

This registers the plugin with Claude Code, making all skills, hooks, and MCP tools available.

## Quick Start

After installation, here is how to use ccx in your own project:

```bash
# 1. Initialize ccx in your project
#    Navigate to YOUR project (not the ccx repo), then run:
cd ~/my-awesome-project
ccx init .
#    This scans your project and generates base-context.yaml and .ccx/ directory.

# 2. (Optional) Review the auto-generated base-context.yaml
#    Edit it to add project-specific exception rules or architecture notes.

# 3. Start Claude Code in your project and run the pipeline
claude
# Inside Claude Code, use: /ccx:run [your request]
```

> **What does `ccx init` do?**
> - Scans your project and generates `base-context.yaml` (describes your stack, architecture, structure)
> - Creates the `.ccx/` directory (stores session data, logs, and analysis cache)
>
> Skills, hooks, and MCP configuration are provided by the plugin and do not need to be copied into your project.

## CLI Commands

| Command                     | Description                                            | Key Flags                                    |
|-----------------------------|--------------------------------------------------------|----------------------------------------------|
| `ccx init [project_dir]`   | Generate `base-context.yaml` and create `.ccx/`       | `--force / -f` -- overwrite existing files   |
| `ccx update [project_dir]` | Upgrade ccx package to latest version                  | --                                           |
| `ccx status [project_dir]` | Check installation status (base-context, plugin, MCP)  | --                                           |
| `ccx index [project_dir]`  | Discover and index project scopes for analysis caching | `--reset` clear cache; `-v` show scope tree  |

`project_dir` defaults to `.` (current directory) for all commands. You can omit it if you are already inside the project folder.

## Skills

Skills are invoked inside Claude Code with the `/ccx:` prefix.

| Skill                           | Description                                                                    |
|---------------------------------|--------------------------------------------------------------------------------|
| `/ccx:run [request]`           | Full pipeline: adaptive plan, implement, review, commit with checkpoints.      |
| `/ccx:analyze [request]`       | Standalone analysis: produce structured requirements from a request.            |
| `/ccx:review [files or scope]` | Review code changes against project exception rules.                           |
| `/ccx:commit [context]`        | Generate and create a conventional commit for current changes.                 |
| `/ccx:index [--force]`         | Analyze all project scopes and cache results. Incremental or `--force` full.   |
| `/ccx:resolve`                 | Manage annotations and resolve ambiguities flagged during indexing.             |

## MCP Tools

The ccx MCP server (`ccx.mcp_server`) exposes the following tools:

| Tool                         | Description                                                                       |
|------------------------------|-----------------------------------------------------------------------------------|
| `load_project_context`       | Load project base context (stack, architecture, structure, rules) from YAML.      |
| `check_rules`                | Check if described changes violate any project exception rules.                   |
| `get_session`                | Get recent execution history and context summary.                                 |
| `record_execution`           | Record a pipeline execution result for future session context.                    |
| `get_analysis_cache`         | Look up cached analysis for a scope before re-analyzing.                          |
| `save_analysis_cache`        | Save analysis results for a scope to cache for future reuse.                      |
| `invalidate_analysis_cache`  | Invalidate cached analysis for a scope after implementation changes it.           |
| `list_cached_scopes`         | List all cached analysis scopes with brief info.                                  |
| `trigger_index`              | Discover project scopes and build hierarchical scope tree (no code analysis).     |
| `get_scope_with_children`    | Get a scope's cached analysis with summaries of all descendant scopes.            |
| `mark_stale_cascade`         | Mark a scope and all its ancestor scopes as stale.                                |
| `get_pending_scopes`         | Paginated list of scopes needing analysis (supports prefix filter).               |
| `get_pending_summary`        | Grouped counts of unanalyzed scopes by directory.                                 |
| `get_annotations`            | Query annotations by scope/type (supports unresolved_only filter).                |
| `add_annotation`             | Add domain/architecture/usage/ambiguity annotation to a scope.                    |
| `resolve_ambiguity`          | Resolve an ambiguity annotation with an answer.                                   |
| `get_agent_config`           | Get agent-specific rules, context, and disabled rules.                            |
| `save_agent_config`          | Save or update agent-specific configuration.                                      |
| `delete_agent_config`        | Delete agent-specific configuration file.                                         |
| `list_agent_configs`         | List all agents and their configuration status.                                   |

### MCP Tool Namespace Mapping

Skill and agent files in this repository reference MCP tools using the **canonical** short prefix `mcp__ccx__` (e.g. `mcp__ccx__load_project_context`). The actual prefix that appears at runtime depends on how ccx is loaded:

| Loading method | Runtime prefix | Example |
|---|---|---|
| **Local `.mcp.json`** (`claude --plugin-dir ~/ccx`) | `mcp__ccx__*` | `mcp__ccx__load_project_context` |
| **Plugin install** (`claude plugin install ccx@...`) | `mcp__plugin_ccx_ccx__*` | `mcp__plugin_ccx_ccx__load_project_context` |

The longer plugin prefix follows the Claude Code convention `mcp__plugin_{plugin}_{server}__`, where both `{plugin}` and `{server}` happen to be `ccx`.

**You do not need to change skill or agent files.** Claude Code resolves the canonical `mcp__ccx__` references to the correct runtime prefix automatically, so all skill and agent Markdown files keep the short form.

## Nested Sub-agents

Agents can spawn other agents as sub-agents, enabling task decomposition without returning control to the orchestrator. The system enforces a maximum nesting depth of **2** to prevent runaway recursion.

Each agent invocation carries a `current_depth` counter. When an agent spawns a sub-agent it increments the counter by 1. If `current_depth` reaches the limit, the agent must complete the work itself or return partial results instead of delegating further.

**How depth tracking works:**

```
Orchestrator (depth 0)
    -> Implementer (depth 1)
        -> Researcher (depth 2)   # allowed, max depth reached
            -> (blocked)          # depth 3 would exceed limit
```

**Practical examples:**

| Parent agent | Sub-agent | Purpose |
|---|---|---|
| Planner | Researcher | Deep codebase exploration when cached scope analysis is insufficient. |
| Implementer | Researcher | Gather context about unfamiliar modules before making changes. |

Sub-agents follow the same protocol as top-level agents: they receive context via MCP tools, return structured `STATUS: COMPLETE` or `STATUS: NEEDS_CONTEXT` results, and never interact with the user directly.

## Agent Configuration

Each agent can have a per-project configuration file stored at `.ccx/agents/{agent-name}.yaml`. These files let you customize agent behavior without modifying the shared agent definitions in the plugin.

**YAML schema:**

```yaml
# .ccx/agents/implementer.yaml
rules:
  - "Always add type hints to new functions."
  - "Prefer composition over inheritance."

context:
  - "This project uses the repository pattern for data access."
  - "All database queries go through src/db/queries.py."

disabled_rules:
  - "no-inline-styles"   # Not applicable to this backend-only project.
```

| Field            | Type       | Description                                                        |
|------------------|------------|--------------------------------------------------------------------|
| `rules`          | `string[]` | Additional rules the agent must follow in this project.            |
| `context`        | `string[]` | Extra context injected into the agent's prompt.                    |
| `disabled_rules` | `string[]` | Base rules to suppress for this agent in this project.             |

**MCP tool workflow:**

- `list_agent_configs` -- See which agents have configuration and which use defaults.
- `get_agent_config` -- Read a specific agent's configuration.
- `save_agent_config` -- Create or update an agent's configuration.
- `delete_agent_config` -- Remove an agent's configuration (reverts to defaults).

The orchestrator automatically injects the relevant agent configuration into sub-agent prompts when spawning them. If no configuration file exists for an agent, it runs with default behavior.

## Architecture

```
Claude Code (orchestrator)
    |
    |-- Plugin (convention-based discovery at repo root)
    |       |-- skills/             -- pipeline logic, subagent prompts
    |       |-- hooks/              -- event logging
    |       |-- agents/             -- subagent definitions
    |       +-- .mcp.json           -- MCP server connection settings
    |
    |-- MCP Server (ccx.mcp_server) -- project context, session, analysis cache
    |
    +-- Subagents                   -- spawned by orchestrator for each phase
            |
            +-- MCP calls           -- subagents load context directly via MCP
```

Key design decisions:
- **Plugin-based distribution.** Skills (`skills/`), hooks (`hooks/`), agents (`agents/`), and MCP configuration (`.mcp.json`) live at the repository root and are auto-discovered by Claude Code's convention-based plugin system. `.claude-plugin/plugin.json` provides the minimal plugin manifest. No manual copying of files into `.claude/` is needed.
- **Main agent = pure orchestrator.** It coordinates phases and handles user interaction but does not read files or load project context itself.
- **All heavy work is delegated to subagents.** Each subagent loads context via MCP tools directly.
- **User interaction happens only through the main agent.** Subagents return structured status (COMPLETE / NEEDS_CONTEXT) and never prompt the user.
- **Pipeline phases:** Index (optional) -> Adaptive Plan -> Execute (Research, Implement, Review) -> Commit & Push -> Record. Adaptive pipeline depth: simple requests skip reviewer, complex requests add final synthesis review.

## Directory Structure

```
.claude-plugin/
    plugin.json             -- Plugin manifest (name, description, author)

.mcp.json                   -- MCP server connection configuration

skills/
    run/
        SKILL.md            -- /ccx:run entry point
        PIPELINE.md         -- Detailed pipeline logic
    analyze/
        SKILL.md            -- /ccx:analyze
    review/
        SKILL.md            -- /ccx:review
    commit/
        SKILL.md            -- /ccx:commit
    index/
        SKILL.md            -- /ccx:index
    resolve/
        SKILL.md            -- /ccx:resolve

hooks/
    hooks.json              -- Hook configuration (event matchers)
    log_event.sh            -- Hook script for event logging
    log_event.py            -- Python handler for event logging

agents/
    _protocol.md            -- Shared agent protocol (rules, output format)
    implementer.md          -- Implementer agent definition
    module-analyzer.md      -- Module-level analysis agent (indexing)
    package-synthesizer.md  -- Package-level synthesis agent (indexing)
    planner.md              -- Adaptive Planner (analysis + planning)
    researcher.md           -- Researcher agent definition
    reviewer.md             -- Reviewer agent definition

# --- Project-side (created by `ccx init` in your project) ---

.ccx/
    agents/                 -- Per-agent configuration overrides (YAML)
    session.json            -- Session persistence
    analysis_cache/         -- Scope-based analysis cache

src/ccx/
    __init__.py
    __main__.py
    cli.py                  -- Setup CLI (init, update, status, index)
    mcp_server.py           -- FastMCP server, 20 tools
    config.py               -- base-context.yaml loader
    scanner.py              -- Project auto-scan (runtime, framework, db, tree)
    session.py              -- .ccx/session.json file-based session persistence
    analysis_cache.py       -- Scope-based analysis cache with staleness detection
    logger.py               -- MCP tool call logging
    base-context.example.yaml
```

## Dependencies

| Package    | Version |
|------------|---------|
| python     | ^3.11   |
| pyyaml     | ^6.0    |
| click      | ^8.0    |
| mcp[cli]   | >=1.0   |
| pathspec   | ^0.12   |
