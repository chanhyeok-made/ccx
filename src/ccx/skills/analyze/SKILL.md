---
name: analyze
description: "Standalone analysis: analyze a request and produce structured requirements"
disable-model-invocation: true
argument-hint: "[request to analyze]"
allowed-tools: Read, Grep, Glob, AskUserQuestion, mcp__ccx__load_project_context, mcp__ccx__get_session, mcp__ccx__get_analysis_cache, mcp__ccx__save_analysis_cache
---

# Analyze Request (Standalone)

This is the **standalone** analysis skill (`/project:analyze`). You interact directly with the user.

> **Note:** When used inside `/project:run`, the pipeline's own Analyze phase (PIPELINE.md Phase 1) takes precedence. That version runs as a subagent and does NOT use AskUserQuestion.

## Steps

1. Call `mcp__ccx__load_project_context` with the current project directory.
2. Call `mcp__ccx__get_session` to check for previous context.
3. For each scope in the request, call `mcp__ccx__get_analysis_cache(project_dir, scope)`.
   - Cache hit (not stale) → use cached summary, skip reading code for that scope.
   - Cache miss or stale → read code, analyze, then call `mcp__ccx__save_analysis_cache` to cache results.
   - Scope naming: project-root-relative file path, no extension, lowercase (e.g. `"src/ccx/mcp_server"`).
4. Analyze the request against the project context.

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
