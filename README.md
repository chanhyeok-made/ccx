# ccx (Claude Code eXtension)

**Claude Code plugin for project-aware development pipelines.**

ccx extends Claude Code with structured skills, MCP tools, and multi-agent workflows. It provides analysis caching, session management, project scanning, and an adaptive pipeline that adjusts depth based on request complexity.

## Quick Start

### Install

```bash
# 1. Clone
git clone https://github.com/chanhyeok-made/ccx.git
cd ccx

# 2. Install Python dependencies
pip install -e .          # or: poetry install

# 3. Register as Claude Code plugin
claude plugin marketplace add chanhyeok-made/claude-plugins
claude plugin install ccx@chanhyeok-plugins
```

> **Try without installing:** `claude --plugin-dir ~/ccx` loads the plugin for one session only.

### Use in your project

```bash
cd ~/my-project
ccx init .                    # scan project, generate base-context.yaml
claude                        # start Claude Code
# then: /ccx:run [your request]
```

`ccx init` scans your project and creates `base-context.yaml` (stack, architecture, rules), `.ccx/` (session data, analysis cache), and `.claude/settings.local.json` (auto-approves all ccx tools so you never see permission prompts). Skills and MCP tools are provided by the plugin.

## Prerequisites

- **Python 3.11+**
- **Claude Code CLI** (`npm install -g @anthropic-ai/claude-code`)

## CLI

| Command | Description | Flags |
|---------|-------------|-------|
| `ccx init [dir]` | Generate `base-context.yaml`, `.ccx/`, and auto-approve permissions | `--force` |
| `ccx update [dir]` | Upgrade ccx to latest version | |
| `ccx status [dir]` | Check installation status | |
| `ccx index [dir]` | Index project scopes for analysis cache | `--reset`, `-v` |
| `ccx usage [dir]` | Show token usage statistics | `-n`, `-d` |
| `ccx context [dir]` | Show context window usage statistics | `-n`, `-d` |

## Skills

Invoke inside Claude Code with `/ccx:` prefix.

| Skill | Description |
|-------|-------------|
| `/ccx:run [request]` | Full pipeline: adaptive plan, implement, review, commit |
| `/ccx:analyze [request]` | Standalone analysis: structured requirements from a request |
| `/ccx:review [scope]` | Review code changes against project rules |
| `/ccx:commit [context]` | Generate conventional commit for current changes |
| `/ccx:index [--force]` | Analyze all project scopes and cache results |
| `/ccx:resolve` | Manage annotations and resolve ambiguities from indexing |

## Architecture

```
Claude Code (orchestrator)
    |
    |-- Plugin (auto-discovered at repo root)
    |       |-- skills/         pipeline logic
    |       |-- hooks/          event logging, schema validation
    |       |-- agents/         subagent definitions
    |       +-- .mcp.json       MCP server config
    |
    |-- MCP Server (ccx.mcp_server)
    |       project context, session, analysis cache
    |
    +-- Subagents (spawned per phase)
            |
            +-- MCP calls (load context directly)
```

**Pipeline:** Index -> Adaptive Plan -> Execute (Research, Implement, Review) -> Commit -> Record

**Adaptive depth:** The planner classifies each request as `simple`, `medium`, or `complex` and adjusts the pipeline accordingly -- simple requests skip the reviewer, complex requests add a final cross-task synthesis review.

**Key design:**
- Main agent is a pure orchestrator (no file reads, no context loading)
- All heavy work delegated to subagents with structured contracts
- Subagents never interact with the user directly

## MCP Tools

The MCP server exposes 22 tools:

| Tool | Description |
|------|-------------|
| `load_project_context` | Load project base context from YAML |
| `check_rules` | Check changes against project rules |
| `get_session` | Get recent execution history |
| `record_execution` | Record pipeline execution result |
| `get_token_usage` | Get token usage stats for a session |
| `get_context_usage` | Get context window usage stats for a session |
| `get_analysis_cache` | Look up cached scope analysis |
| `save_analysis_cache` | Save scope analysis to cache |
| `invalidate_analysis_cache` | Invalidate cached scope analysis |
| `list_cached_scopes` | List all cached scopes |
| `trigger_index` | Discover scopes and build scope tree |
| `get_scope_with_children` | Get scope analysis with descendants |
| `mark_stale_cascade` | Mark scope and ancestors as stale |
| `get_pending_scopes` | List scopes needing analysis |
| `get_pending_summary` | Grouped counts of unanalyzed scopes |
| `get_annotations` | Query annotations by scope/type |
| `add_annotation` | Add annotation to a scope |
| `resolve_ambiguity` | Resolve an ambiguity annotation |
| `get_agent_config` | Get agent-specific configuration |
| `save_agent_config` | Save agent-specific configuration |
| `delete_agent_config` | Delete agent-specific configuration |
| `list_agent_configs` | List all agents and their config status |

<details>
<summary>MCP tool namespace mapping</summary>

Skill and agent files use the canonical prefix `mcp__ccx__`. The runtime prefix depends on how ccx is loaded:

| Method | Prefix |
|--------|--------|
| Local (`.mcp.json`) | `mcp__ccx__*` |
| Plugin install | `mcp__plugin_ccx_ccx__*` |

Claude Code resolves the canonical prefix automatically.
</details>

## Permissions

`ccx init` auto-configures `.claude/settings.local.json` with permissions for all tools (Bash, Edit, Read, Write, Grep, Glob, Agent, MCP tools, etc.) so you never see "Allow?" prompts during ccx workflows.

- **First setup:** `ccx init .` creates the file. Existing settings are preserved (permissions are merged, not overwritten).
- **Worktrees:** A `SessionStart` hook automatically ensures `settings.local.json` exists when Claude Code starts in a worktree. It copies from the main repo or generates a default.
- **Re-generate:** `ccx init --force` replaces the permission list entirely.

## Agent Configuration

Per-project agent behavior can be customized via `.ccx/agents/{agent-name}.yaml`:

```yaml
# .ccx/agents/implementer.yaml
rules:
  - "Always add type hints to new functions."
context:
  - "This project uses the repository pattern."
disabled_rules:
  - "no-inline-styles"
```

The orchestrator injects these configs into subagent prompts automatically.

## Nested Sub-agents

Agents can spawn sub-agents (max depth: 2).

| Parent | Sub-agent | Purpose |
|--------|-----------|---------|
| Planner | Researcher | Deep codebase exploration when cache is insufficient |
| Implementer | Researcher | Context about unfamiliar modules |

## Directory Structure

```
.claude-plugin/plugin.json      Plugin manifest
.mcp.json                       MCP server config

skills/
    run/SKILL.md                /ccx:run entry point
    run/PIPELINE.md             Pipeline reference
    analyze/SKILL.md            /ccx:analyze
    review/SKILL.md             /ccx:review
    commit/SKILL.md             /ccx:commit
    index/SKILL.md              /ccx:index
    resolve/SKILL.md            /ccx:resolve

hooks/
    hooks.json                  Hook configuration
    log_event.sh                Event logging wrapper (bash)
    log_event.py                Event logging handler
    validate_schema.py          Agent output schema validation
    ensure_settings.sh          Auto-create settings.local.json (worktree support)
    ensure_settings.py          Permission settings handler

agents/
    _protocol.md                Shared agent protocol
    planner.md                  Adaptive Planner (analysis + planning)
    researcher.md               Codebase researcher
    implementer.md              Code implementer
    reviewer.md                 Code reviewer
    module-analyzer.md          Module-level indexing
    package-synthesizer.md      Package-level indexing

src/ccx/
    cli.py                      CLI (init, update, status, index)
    mcp_server.py               FastMCP server
    config.py                   base-context.yaml loader
    scanner.py                  Project auto-scanner
    session.py                  Session persistence
    analysis_cache.py           Scope-based analysis cache
    agent_config.py             Per-agent YAML config
    _transcript_utils.py        Shared transcript parsing utilities
    token_tracker.py            Token usage tracking per agent/session
    context_tracker.py          Context window usage tracking
    base-context.example.yaml   Template for project context
```

## Dependencies

| Package | Version |
|---------|---------|
| Python | ^3.11 |
| PyYAML | ^6.0 |
| Click | ^8.0 |
| mcp[cli] | >=1.0 |
| pathspec | ^0.12 |
