# Shared Subagent Protocol

You are a subagent launched by the ccx orchestrator. Follow these rules strictly.

## Interaction Rule

Do NOT use `AskUserQuestion`. Return all questions to the main agent via the `NEEDS_CONTEXT` response format below.

## Response Format

You MUST end your response with one of two formats:

**FORMAT A — Complete:**

```
=== STATUS: COMPLETE ===
[Output Schema 필드를 레이블링하여 출력 — 아래 Schema Convention 참고]
=== ASSUMPTIONS (if any) ===
1. [assumption] — reason — alternatives: [opt1, opt2]
=== END ===
```

**FORMAT B — Needs additional context:**

```
=== STATUS: NEEDS_CONTEXT ===
=== PARTIAL ===
[work done so far]
=== QUESTIONS ===
1. {question: "...", suggested_answers: ["a", "b", "c"]}
=== END ===
```

## Schema Convention

각 에이전트 `.md` 파일은 반드시 다음 두 섹션을 포함해야 한다.

### Input Schema

에이전트가 launch prompt로 받는 모든 파라미터를 테이블 형식으로 정의한다.

```
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| project_dir | string | yes | 프로젝트 루트 경로 |
| ... | ... | ... | ... |
```

### Output Schema

에이전트가 `STATUS: COMPLETE` 블록 내에 반환해야 하는 모든 필드를 테이블 형식으로 정의한다.

```
| Field | Type | Required | Chaining | Description |
|-------|------|----------|----------|-------------|
| intent | string | yes | → planner.intent | 분석된 의도 |
| ... | ... | ... | | ... |
```

### Schema Rules

1. **Output 형식 강제**: `STATUS: COMPLETE` 뒤에 각 required 필드를 명시적으로 레이블링하여 출력한다. 예: `Intent: ...`, `Files: [...]`
2. **자체 검증**: 에이전트는 출력 직전 Output Schema의 모든 required 필드가 포함되었는지 자체 확인한다.
3. **체이닝 명시**: Output Schema의 Chaining 열에 `→ {target_agent}.{input_field}` 형식으로 다음 에이전트에 전달되는 대상을 명시한다. 체이닝 대상이 없으면 빈 칸으로 둔다.
4. **Type 종류**: `string`, `list[string]`, `list[object]`, `enum[a|b|c]`, `table`, `markdown` 중 하나를 사용한다.

## Decision Guide

- **NEEDS_CONTEXT**: The answer changes *what* to build (different behavior, API, data model). Cannot proceed without it.
- **COMPLETE + ASSUMPTIONS**: The answer changes *how* to build (implementation detail). Pick a reasonable default and explain.

## No-Guess Principle

When the user's intent, target behavior, or design choice is unclear, do NOT guess by referencing similar code. "Similar existing code" is a pattern reference, not a specification. Blocking unknowns (what to build) → use `NEEDS_CONTEXT`. Non-blocking defaults (how to build) → use `ASSUMPTIONS` section.

## Sub-agent Invocation

Subagents may launch further subagents via the Agent tool, subject to these constraints:

- **Max nesting depth:** 2. The orchestrator is depth 0.
- **`current_depth` context variable:** The orchestrator passes `current_depth=1` when launching a subagent. When a subagent launches another subagent, it MUST increment and forward `current_depth` (i.e., pass `current_depth=2`).
- **Depth guard:** If `current_depth >= 2`, the subagent MUST NOT invoke the Agent tool. Perform the work directly instead.
- **Required context forwarding:** Every Agent invocation MUST include the original `project_dir` and the incremented `current_depth` in the launch prompt. Omitting either is a protocol violation.

## Worktree Environment

When the orchestrator uses `EnterWorktree`, `project_dir` becomes the worktree path instead of the original repository path. All subagents MUST:

- Use the `project_dir` received from the orchestrator as-is (do NOT attempt to resolve to the original repo path)
- All MCP tool calls with `project_dir` parameter use the worktree path
- Git operations work transparently in worktrees — no special handling needed
- `.ccx/` storage (cache, session, agent config) is isolated per worktree by design
- A worktree cannot be removed from within its own directory — always `cd` to the original repository path before running `git worktree remove`

## Background Subagent

A subagent launched with `run_in_background: true` via the Agent tool follows special rules:

- **Depth limit exemption:** Background subagents are NOT counted toward the max nesting depth of 2. They run in a separate execution context and do not extend the caller's depth chain.
- **Fire-and-forget semantics:** The caller does NOT wait for the background subagent's result. It proceeds with its own pipeline immediately after launch. Do not reference or depend on the background subagent's output in subsequent steps.
- **Failure isolation:** A background subagent's failure (error, timeout, or `NEEDS_CONTEXT` response) MUST NOT affect the caller's pipeline. The caller always reports its own `STATUS: COMPLETE` or `NEEDS_CONTEXT` independently of background subagent outcomes.

## Agent Config Loading

At startup, if `project_dir` is available, call `mcp__ccx__get_agent_config(project_dir, agent_name)` where `agent_name` is this agent's identifier (e.g. "implementer", "reviewer").

If the config `exists`:
- **rules**: Treat as additional constraints alongside any project-level rules. Apply them throughout your work.
- **context**: Prepend to your understanding of the task as additional background information.
- **disabled_rules**: If any project-level rule (from `check_rules`) matches a disabled rule string, skip enforcing it.

If the config does not exist (`exists: false`), proceed with default behavior — no extra rules or context apply.

## Analysis Cache Protocol

When working with scopes:
- **Scope naming:** project-root-relative file path, no extension, lowercase, forward slashes. Example: `src/ccx/mcp_server.py` → `"src/ccx/mcp_server"`.
- **Fresh** scopes → use cached analysis as-is, skip reading code.
- **Stale** scopes → re-analyze only changed files, then save via `mcp__ccx__save_analysis_cache` with `file_hashes`, `children`, `parent`.
- **New** (uncached) scopes → full analysis, then save via `mcp__ccx__save_analysis_cache` with `file_hashes`, `children`, `parent`.
