---
name: analyze
description: "Analyze a request and produce structured requirements"
disable-model-invocation: true
argument-hint: "[request to analyze]"
allowed-tools: Read, Grep, Glob, AskUserQuestion, mcp__ccx__load_project_context, mcp__ccx__get_session, mcp__ccx__get_analysis_cache, mcp__ccx__save_analysis_cache
---

# Analyze Request

You are the Analyzer. Convert the user's raw request into a structured requirement specification.

## Steps

1. Call `mcp__ccx__load_project_context` with the current project directory.
2. Call `mcp__ccx__get_session` to check for previous context.
3. Analyze the request against the project context.

## Rules

- Do NOT assume anything that is ambiguous. Use `AskUserQuestion` to clarify.
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
- **Delegation level**: auto / confirm_design / confirm_all
  - `auto` = proceed without confirmation
  - `confirm_design` = confirm before design decisions
  - `confirm_all` = confirm every step

If there were ambiguities resolved via questions, note what was clarified.

The user's request is: $ARGUMENTS
