---
name: index
description: "Perform code-level analysis on all project scopes and cache results"
argument-hint: "[--force to re-analyze all scopes, or leave empty for incremental]"
allowed-tools: Read, Grep, Glob, Bash, Agent, mcp__ccx__load_project_context, mcp__ccx__trigger_index, mcp__ccx__get_analysis_cache, mcp__ccx__save_analysis_cache, mcp__ccx__invalidate_analysis_cache, mcp__ccx__list_cached_scopes, mcp__ccx__get_agent_config
---

# Project Code-Level Indexing

Perform code-level analysis (role, interfaces, dependencies) on all project scopes without requiring a user request. Results are cached for faster future analysis.

## Mode

- **Incremental** (default): Only analyze `new` and content-stale scopes.
- **Force** (`--force` in `$ARGUMENTS`): Re-analyze all scopes regardless of cache freshness.

## Steps

### 1. Discover Scopes

Call `mcp__ccx__trigger_index(project_dir)` to discover all modules/packages and build the scope tree.

The result contains:
- `scope_tree`: `{parent_key: [child_keys]}` — hierarchical structure
- `new_scopes`: scopes discovered but not yet in cache
- `stale_scopes`: scopes in cache but NO LONGER in project (orphaned — automatically cleaned up during indexing)
- `total_scopes`, `packages`, `modules`: counts
- Each scope entry has: `key`, `path`, `type` ("module"/"package"), `files`, `parent`, `language`

Note: `trigger_index` already saves the scope tree to cache `_meta`.

### 2. Detect Content-Stale Scopes

For each **existing** (non-new) scope, call `mcp__ccx__get_analysis_cache(project_dir, scope_key, check_staleness=True)`.
- If `stale=true`: the scope's file hashes have changed — needs re-analysis.
- If `stale=false` and `hit=true`: fresh — skip unless `--force`.

Combine `new_scopes` + content-stale scopes = **target scopes**.
If `$ARGUMENTS` contains `--force`, treat ALL scopes as targets.

### 3. Determine Order

From the targets, compute **topological order** (leaf-first):

1. Separate scopes into **modules** (leaf, no children) and **packages** (have children).
2. Process order: all target modules first, then packages bottom-up (deepest packages before their parents).
3. This ensures that when analyzing a package, all its children already have cached summaries.

If zero targets exist, output "All scopes are up to date." and stop.

### 4. Permission Pre-flight

Before launching subagents, verify MCP write access by calling:

```
mcp__ccx__save_analysis_cache(project_dir, scope="_preflight", summary="permission check", interfaces=[], dependencies=[], patterns=[], known_issues=[], key_files=[], annotations=[], file_hashes={}, children=[], parent=null, cached_by_request="ccx:index")
```

- **Approved** → call `mcp__ccx__invalidate_analysis_cache(project_dir, scope="_preflight")` to clean up. Proceed.
- **Denied** → output `⚠️ save_analysis_cache 권한이 필요합니다. 도구를 허용 후 다시 실행해주세요.` and stop.

### 5. Analyze Each Scope

**Agent Config Injection** — Before launching each subagent (module-analyzer, package-synthesizer), call `mcp__ccx__get_agent_config(project_dir, agent_name)` where `agent_name` matches the subagent type (e.g. `"module-analyzer"`, `"package-synthesizer"`). If the config exists (non-null response), append an `## Agent Config` block to the subagent prompt:

```
## Agent Config
rules: {rules from get_agent_config}
context: {context from get_agent_config}
disabled_rules: {disabled_rules from get_agent_config}
```

For each target scope in topological order, output progress:

```
### [N/M] Analyzing: {scope_key}
```

#### For module scopes (type = "module"):

Launch `Agent` with `subagent_type: "ccx:module-analyzer"`. Prompt:

> project_dir="{project_dir}", current_depth=1
> scope_key="{scope_key}", files={files list}, parent_key="{parent_key}"

#### For package scopes (type = "package"):

Launch `Agent` with `subagent_type: "ccx:package-synthesizer"`. Prompt:

> project_dir="{project_dir}", current_depth=1
> scope_key="{scope_key}", children={children scope keys}, package_files={direct files}, parent_key="{parent_key}"

### 6. Summary

After all scopes are analyzed, output a summary table:

```
## Indexing Complete

| Metric | Count |
|--------|-------|
| Total scopes | {N} |
| Analyzed | {targets} |
| Skipped (fresh) | {N - targets} |

All scopes are now cached and available for future analysis.
```

## Rules

**Schema compliance** — Every subagent MUST produce output that includes all required fields defined in its Output Schema. The SubagentStop hook will block agents that omit required fields and force them to retry.

- Do NOT ask the user any questions during indexing — this is a fully automated process.
- If a subagent fails on a scope, log the error and continue with the next scope. Do not abort the entire process.
- Scope naming: project-root-relative path, no extension, lowercase, forward slashes.
- Parallel processing: You MAY launch multiple module-scope subagents in parallel (up to 3 at a time) since they are independent. Package scopes must wait for their children.

## Pipeline Integration

This skill is invoked as a background subagent by the planner (when `trigger_index` finds new/stale scopes) or standalone via `/ccx:index`. When called without `$ARGUMENTS`, it runs in incremental mode. Background invocation uses `run_in_background: true` — the caller does not wait for the result.

Arguments: $ARGUMENTS
