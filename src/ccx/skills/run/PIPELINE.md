# ccx Pipeline

## Role

You are a **pure orchestrator**. You hold only: `project_dir`, user request, analysis summary, task list, per-task status. You do NOT read files, load context, or make implementation decisions — subagents do all heavy work via MCP.

## Rules

**User interaction:** Only main agent talks to the user. Every subagent prompt MUST include: `"Do NOT use AskUserQuestion. Return questions to the main agent."`

**AskUserQuestion protocol:**
1. ALWAYS call with `questions` array containing `question`, `header` (≤12 chars), `options` (2-4 items with `label` + `description`), `multiSelect: false`
2. After every call, check response. If empty → output `⚠️ 사용자 응답 없음. 파이프라인을 중단합니다.` → record failure → exit. NEVER fabricate answers. NEVER proceed without explicit user input.

**Checkpoint shorthand:** `>>> CHECKPOINT("질문", "header", ["Option1", "Option2", "Option3"])` means: call AskUserQuestion with those values. Each option label needs a description you generate from context. Standard checkpoint behavior: "Proceed" → next phase, "Modify" → ask what to change (with AskUserQuestion + options) then re-confirm, "Cancel" → record cancelled status → exit.

**No-guess principle:** When the user's intent, target behavior, or design choice is unclear, subagents MUST NOT guess by referencing similar code. "Similar existing code" is a pattern reference, not a specification. Blocking unknowns (what to build) → use `NEEDS_CONTEXT`. Non-blocking defaults (how to build) → use `ASSUMPTIONS` section. See subagent response protocol below.

**Subagent response protocol:** Every subagent MUST end its response with one of two formats:

FORMAT A — Complete:
```
=== STATUS: COMPLETE ===
[phase-specific results]
=== ASSUMPTIONS (if any) ===
1. [assumption] — reason — alternatives: [opt1, opt2]
=== END ===
```

FORMAT B — Needs additional context:
```
=== STATUS: NEEDS_CONTEXT ===
=== PARTIAL ===
[work done so far]
=== QUESTIONS ===
1. {question: "...", suggested_answers: ["a", "b", "c"]}
=== END ===
```

Decision guide for subagents:
- **NEEDS_CONTEXT**: The answer changes *what* to build (different behavior, API, data model). Cannot proceed without it.
- **COMPLETE + ASSUMPTIONS**: The answer changes *how* to build (implementation detail). Pick a reasonable default and explain.

**Main agent handling loop:** Apply to every subagent launch:
```
context = {task, phase_inputs}
for round in 1..3:
    result = launch_subagent(prompt + context)
    if COMPLETE → break
    if NEEDS_CONTEXT →
        questions → AskUserQuestion (suggested_answers → options)
        context += {partial, user_answers}
    if no STATUS marker → treat as COMPLETE, break
round > 3 → CHECKPOINT("3회 시도 후에도 추가 맥락이 필요합니다.", "루프 초과", ["부분 결과로 진행", "추가 입력 제공", "취소"])
```

**Analysis cache protocol:**
- **Scope naming rule:** scope = project-root-relative file path, no extension, lowercase, forward slashes. Examples: `src/ccx/mcp_server.py` → `"src/ccx/mcp_server"`, `src/ccx/skills/` → `"src/ccx/skills"`. The server auto-normalizes, but subagents should follow this convention for clarity.
- **Index-first workflow:** Always start with `mcp__ccx__trigger_index(project_dir)` to discover all scopes and their stale/new status. This returns the full scope tree with hierarchy and staleness info.
- For relevant scopes, call `mcp__ccx__get_scope_with_children(project_dir, scope)` to load cached analysis with full hierarchy (parent + children).
- `fresh` scopes → use cached analysis as-is, skip reading code.
- `stale` scopes → re-analyze only changed files, then save via `mcp__ccx__save_analysis_cache` with `file_hashes`, `children`, `parent`.
- `new` (uncached) scopes → full analysis, then save via `mcp__ccx__save_analysis_cache` with `file_hashes`, `children`, `parent`.
- Use `mcp__ccx__list_cached_scopes(project_dir)` to inspect existing cache entries when needed.
- After implementation changes files, call `mcp__ccx__mark_stale_cascade(project_dir, scope)` on each modified scope to propagate staleness up the hierarchy.

---

## [Phase 0] Index (Optional)

Before analysis, ensure the analysis cache is warmed.

1. Call `mcp__ccx__trigger_index("{project_dir}")` to discover scopes and check for `new_scopes`.
2. If `new_scopes` is non-empty OR many scopes lack cached analysis:
   - Use the `Skill` tool to invoke `/project:index` (no arguments) in incremental mode to analyze all stale/new scopes.
   - This is automatic — no user checkpoint needed.
3. If all scopes are already cached and fresh, skip this phase with: `Index: all scopes up to date.`

This phase ensures Phase 1 (Analyze) can rely on cached analysis for most scopes, reducing redundant code reading.

---

## [Phase 1/5] Analyze

Launch `general-purpose` Agent:

> You are an Analyzer. Call `mcp__ccx__load_project_context("{project_dir}")` and `mcp__ccx__get_session("{project_dir}")`.
> Analyze: "{user_request}"
> 1. **Index first:** Call `mcp__ccx__trigger_index("{project_dir}")` to discover all scopes with stale/new status.
> 2. **Load relevant scopes:** For each scope relevant to the request, call `mcp__ccx__get_scope_with_children("{project_dir}", scope)` to get cached analysis with hierarchy.
>    - Fresh → use cached analysis, skip reading code.
>    - Stale → re-analyze only changed files, then save via `mcp__ccx__save_analysis_cache` with `file_hashes`, `children`, `parent`.
>    - New (uncached) → full analysis, then save via `mcp__ccx__save_analysis_cache` with `file_hashes`, `children`, `parent`.
> 3. **Synthesize:** Intent: one sentence. Scope: module/layer level. Include session context.
> - Do NOT use AskUserQuestion. Return results to the main agent.
>
> **Response format — you MUST end your response with one of:**
> ```
> === STATUS: COMPLETE ===
> Intent: ...
> Scope: ...
> Constraints: ...
> === ASSUMPTIONS (if any) ===
> 1. [assumption] — reason — alternatives: [opt1, opt2]
> === END ===
> ```
> OR if you cannot proceed without user input:
> ```
> === STATUS: NEEDS_CONTEXT ===
> === PARTIAL ===
> [analysis done so far]
> === QUESTIONS ===
> 1. {question: "...", suggested_answers: ["a", "b", "c"]}
> === END ===
> ```
> Use NEEDS_CONTEXT only when the answer changes *what* to build. Use ASSUMPTIONS for *how* to build.

Handle per main agent handling loop. Show final result.

>>> CHECKPOINT("분석 결과가 맞나요?", "분석 확인", ["Proceed", "Modify", "Cancel"])

---

## [Phase 2/5] Plan

Launch `general-purpose` Agent:

> You are a Planner. Call `mcp__ccx__load_project_context("{project_dir}")`.
> Analysis: Intent={intent}, Scope={scope}, Constraints={constraints}
> - Each task: independently implementable, one logical change, explicit dependencies.
> - Do NOT use AskUserQuestion. Return results to the main agent.
>
> **Response format — you MUST end your response with one of:**
> ```
> === STATUS: COMPLETE ===
> | # | Task | Target modules | Complexity | Depends On |
> ...
> Execution order: ...
> === ASSUMPTIONS (if any) ===
> 1. [assumption] — reason — alternatives: [opt1, opt2]
> === END ===
> ```
> OR if you cannot proceed without user input:
> ```
> === STATUS: NEEDS_CONTEXT ===
> === PARTIAL ===
> [planning done so far]
> === QUESTIONS ===
> 1. {question: "...", suggested_answers: ["a", "b", "c"]}
> === END ===
> ```
> Use NEEDS_CONTEXT only when the answer changes *what* to build. Use ASSUMPTIONS for *how* to build.

Handle per main agent handling loop. Show plan. Create tasks with `TaskCreate`, set dependencies with `TaskUpdate`.

>>> CHECKPOINT("이 계획대로 진행할까요?", "계획 확인", ["Proceed", "Modify", "Cancel"])

---

## [Phase 3/5] Execute

For each task in dependency order, output `### Executing T{N}: {description}`:

**3a. Research** — Launch `Explore` Agent:
> Task: {task_description}. Project dir: {project_dir}.
> Find relevant files. Do NOT use AskUserQuestion. Return results to the main agent.
>
> **Response format — you MUST end your response with one of:**
> ```
> === STATUS: COMPLETE ===
> Files: [path — reason, ...]
> Dependencies: ...
> Impact zone: ...
> === END ===
> ```
> OR if you cannot proceed:
> ```
> === STATUS: NEEDS_CONTEXT ===
> === PARTIAL ===
> [research done so far]
> === QUESTIONS ===
> 1. {question: "...", suggested_answers: ["a", "b", "c"]}
> === END ===
> ```

Handle per main agent handling loop.

**3b. Implement** — Launch `general-purpose` Agent:
> Task: {task_description}. Project dir: {project_dir}.
> Files: {from research}. Impact zone: {from research}.
> Call `mcp__ccx__load_project_context`. Read files, implement. Follow existing code style and conventions.
> Do NOT use AskUserQuestion. Return results to the main agent.
>
> **Response format — you MUST end your response with one of:**
> ```
> === STATUS: COMPLETE ===
> Changed files:
> - path (type): intent
> === ASSUMPTIONS (if any) ===
> 1. [assumption] — reason — alternatives: [opt1, opt2]
> === END ===
> ```
> OR if a decision blocks implementation (changes *what* to build):
> ```
> === STATUS: NEEDS_CONTEXT ===
> === PARTIAL ===
> [implementation done so far]
> === QUESTIONS ===
> 1. {question: "...", suggested_answers: ["a", "b", "c"]}
> === END ===
> ```
> Use NEEDS_CONTEXT for blocking unknowns (what to build). Use ASSUMPTIONS for non-blocking defaults (how to build).

Handle per main agent handling loop.

**3c. Review** — Launch `general-purpose` Agent:
> Task: {task_description}. Changed: {from implement}. Impact: {from research}. Project dir: {project_dir}.
> Call `mcp__ccx__check_rules`. Verify: correctness, side effects, rules, patterns, edge cases.
> Do NOT use AskUserQuestion. Return results to the main agent.
>
> **Response format — you MUST end your response with one of:**
> ```
> === STATUS: COMPLETE ===
> Verdict: approve | reject | request_changes
> Issues: ...
> Summary: ...
> === END ===
> ```
> OR if you need clarification to complete the review:
> ```
> === STATUS: NEEDS_CONTEXT ===
> === PARTIAL ===
> [review done so far]
> === QUESTIONS ===
> 1. {question: "...", suggested_answers: ["a", "b", "c"]}
> === END ===
> ```

Handle per main agent handling loop. If implementer returned COMPLETE with non-trivial assumptions → present to user via AskUserQuestion with alternatives as options before review.
On reject/request_changes → re-implement with issues → re-review. Max 3 retries.

**3d. User checkpoint** — After review approves, show the user: changed files list + one-line summary per file + assumptions made.

>>> CHECKPOINT("T{N} 구현 결과를 확인해주세요.\n\n{changed_files_summary}", "코드 확인", ["Approve", "Request changes", "Reject & redo"])

- "Approve" → proceed.
- "Request changes" → ask what to change (AskUserQuestion with options from context) → re-implement with user feedback → re-review → show again. Max 3 rounds.
- "Reject & redo" → ask for new direction (AskUserQuestion) → restart from 3b with user's input.

After user approves, call `mcp__ccx__mark_stale_cascade("{project_dir}", scope)` for each scope affected by the changes to propagate staleness up the hierarchy.
Mark completed with `TaskUpdate`. Output: `Task T{N} complete: {summary}`

---

## [Phase 4/5] Commit & Push

1. Run `git diff --stat`
2. Generate Conventional Commits message: `type(scope): description` + body

>>> CHECKPOINT("이 메시지로 커밋할까요?\n\n{commit_message}", "커밋 확인", ["Commit & Push", "Edit message", "Skip commit"])

3. If confirmed, stage + commit + push.

---

## [Phase 5/5] Record

Call `mcp__ccx__record_execution(project_dir, request, success, summary, changes)`.
Output: `Pipeline complete. {summary}`

---

## Error Handling

Critical failure → record via `mcp__ccx__record_execution` + report to user. Non-critical → fix + continue. Always leave codebase clean.
