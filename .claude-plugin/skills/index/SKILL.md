---
name: index
description: "Perform code-level analysis on all project scopes and cache results"
disable-model-invocation: true
argument-hint: "[--force to re-analyze all scopes, or leave empty for incremental]"
allowed-tools: Read, Grep, Glob, Bash, Agent, mcp__ccx__load_project_context, mcp__ccx__trigger_index, mcp__ccx__get_analysis_cache, mcp__ccx__save_analysis_cache, mcp__ccx__list_cached_scopes
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
- `stale_scopes`: scopes in cache but NO LONGER in project (orphaned — ignore these)
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

### 4. Analyze Each Scope

For each target scope in topological order, output progress:

```
### [N/M] Analyzing: {scope_key}
```

#### For module scopes (type = "module"):

Launch `Agent` with `subagent_type: "general-purpose"`. Prompt:

> You are a Code Analyzer. Your job is to analyze a single module and save the results.
>
> **Scope**: `{scope_key}`
> **Files**: {files list from trigger_index}
> **Project dir**: `{project_dir}`
>
> Steps:
> 1. Read all files in this scope.
> 2. Run `git ls-files -s -- {files}` in `{project_dir}` to get current file hashes.
> 3. Analyze and produce:
>    - **summary**: 1-2 sentence description of this module's role
>    - **interfaces**: list of public functions/classes/exports with brief descriptions
>    - **dependencies**: list of imports (internal and external)
>    - **patterns**: notable code patterns or conventions used
>    - **known_issues**: any obvious issues (empty list if none)
>    - **key_files**: the file paths in this scope
> 4. Call `mcp__ccx__save_analysis_cache` with:
>    - `project_dir`, `scope` = `"{scope_key}"`
>    - All analysis fields above
>    - `file_hashes` = `{path: blob_hash}` from step 2
>    - `children` = `[]`, `parent` = `"{parent_key}"` (or null)
>    - `cached_by_request` = `"ccx:index"`
>
> Return: `STATUS: COMPLETE` with a one-line summary of the module.

#### For package scopes (type = "package"):

Launch `Agent` with `subagent_type: "general-purpose"`. Prompt:

> You are a Code Analyzer. Your job is to analyze a package scope by synthesizing its children's cached analyses.
>
> **Scope**: `{scope_key}`
> **Children**: {children scope keys}
> **Package files**: {direct files like __init__.py if any}
> **Project dir**: `{project_dir}`
>
> Steps:
> 1. For each child scope, call `mcp__ccx__get_analysis_cache("{project_dir}", "{child_key}")` to load cached analysis.
> 2. If the package has its own files (e.g., `__init__.py`), read them.
> 3. Run `git ls-files -s -- {package_files}` in `{project_dir}` for file hashes of direct files.
> 4. Synthesize a package-level analysis:
>    - **summary**: 1-2 sentences describing the package's overall role, synthesized from children
>    - **interfaces**: key public interfaces across the package (aggregated from children)
>    - **dependencies**: external dependencies of the package (union of children's external deps)
>    - **patterns**: common patterns across the package
>    - **known_issues**: aggregated issues
>    - **key_files**: direct package files only (e.g., `__init__.py`)
> 5. Call `mcp__ccx__save_analysis_cache` with:
>    - All fields above
>    - `file_hashes` = hashes of direct package files only
>    - `children` = `[{child_keys}]`, `parent` = `"{parent_key}"` (or null)
>    - `cached_by_request` = `"ccx:index"`
>
> Return: `STATUS: COMPLETE` with a one-line summary of the package.

### 5. Summary

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

- Do NOT ask the user any questions during indexing — this is a fully automated process.
- If a subagent fails on a scope, log the error and continue with the next scope. Do not abort the entire process.
- Scope naming: project-root-relative path, no extension, lowercase, forward slashes.
- Parallel processing: You MAY launch multiple module-scope subagents in parallel (up to 3 at a time) since they are independent. Package scopes must wait for their children.

## Pipeline Integration

When called from `/ccx:run` (Phase 0), this skill receives no `$ARGUMENTS`. It runs in incremental mode and returns its summary to the orchestrator. The orchestrator can then proceed to Phase 1 (Analyze) with a fully warmed cache.

Arguments: $ARGUMENTS
