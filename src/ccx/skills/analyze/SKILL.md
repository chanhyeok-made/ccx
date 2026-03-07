---
name: analyze
description: "Standalone analysis: analyze a request and produce structured requirements"
disable-model-invocation: true
argument-hint: "[request to analyze]"
allowed-tools: Read, Grep, Glob, AskUserQuestion, mcp__ccx__load_project_context, mcp__ccx__get_session, mcp__ccx__get_analysis_cache, mcp__ccx__save_analysis_cache, mcp__ccx__trigger_index, mcp__ccx__get_scope_with_children, mcp__ccx__list_cached_scopes, mcp__ccx__mark_stale_cascade
---

# Analyze Request (Standalone)

This is the **standalone** analysis skill (`/project:analyze`). You interact directly with the user.

> **Note:** When used inside `/project:run`, the pipeline's own Analyze phase (PIPELINE.md Phase 1) takes precedence. That version runs as a subagent and does NOT use AskUserQuestion.

## Steps

1. Call `mcp__ccx__load_project_context` with the current project directory.
2. Call `mcp__ccx__get_session` to check for previous context.
3. **Index first:** Call `mcp__ccx__trigger_index(project_dir)` to discover all scopes and identify stale/new ones.
4. For each relevant scope, call `mcp__ccx__get_scope_with_children(project_dir, scope)` to load cached analysis with hierarchy.
   - Fresh → use cached analysis, skip reading code.
   - Stale → re-analyze only changed files, then save via `mcp__ccx__save_analysis_cache` with `file_hashes`, `children`, `parent`.
   - New (uncached) → full analysis, then save via `mcp__ccx__save_analysis_cache` with `file_hashes`, `children`, `parent`.
   - Scope naming: project-root-relative file path, no extension, lowercase (e.g. `"src/ccx/mcp_server"`).
5. Analyze the request against the project context.

## Rules

- Do NOT assume anything that is ambiguous. Use `AskUserQuestion` to clarify (with options, 2-4 items).
- Do NOT reference any code. Work only with the user's intent.
- If the request is clear enough, proceed without questions.
- Keep intent to ONE sentence.
- Scope should be module/layer/feature level, not file level.

## Output

Present the analysis to the user in this format:

### Analysis Result

- **Intent**: [one sentence summary]
- **Scope**: [list of affected modules/layers/features]
- **Constraints**: [any constraints, including relevant exception rules]

If there were ambiguities resolved via questions, note what was clarified.

The user's request is: $ARGUMENTS
