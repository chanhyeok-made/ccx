# ccx Pipeline — Detailed Reference

> This file is a **reference document** for humans. The model executes from `SKILL.md` directly.
> Do NOT instruct the model to "Read PIPELINE.md" — all execution flow is in SKILL.md.

## Rules

**User interaction:** Only main agent talks to the user.

**AskUserQuestion protocol:**
1. ALWAYS call with `questions` array containing `question`, `header` (≤12 chars), `options` (2-4 items with `label` + `description`), `multiSelect: false`
2. After every call, check response. If empty → output `⚠️ 사용자 응답 없음. 파이프라인을 중단합니다.` → record failure → exit. NEVER fabricate answers. NEVER proceed without explicit user input.

**Checkpoint shorthand:** `>>> CHECKPOINT("질문", "header", ["Option1", "Option2", "Option3"])` means: call AskUserQuestion with those values. Each option label needs a description you generate from context. Standard checkpoint behavior: "Proceed" → next phase, "Modify" → ask what to change (with AskUserQuestion + options) then re-confirm, "Cancel" → record cancelled status → exit.

**Subagent response protocol:** Defined in `{agents_dir}/_protocol.md`. Subagents read it themselves. The orchestrator only needs to check for `STATUS: COMPLETE` or `STATUS: NEEDS_CONTEXT` in the response.

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
   - Use the `Skill` tool to invoke `/ccx:index` (no arguments) in incremental mode to analyze all stale/new scopes.
   - This is automatic — no user checkpoint needed.
3. If all scopes are already cached and fresh, skip this phase with: `Index: all scopes up to date.`

This phase ensures Phase 1 (Analyze) can rely on cached analysis for most scopes, reducing redundant code reading.

---

## [Phase 1/5] Analyze

Launch `ccx:analyzer` Agent:

> project_dir="{project_dir}"
> request="{user_request}"

Handle per main agent handling loop. Show final result.

>>> CHECKPOINT("분석 결과가 맞나요?", "분석 확인", ["Proceed", "Modify", "Cancel"])

---

## [Phase 2/5] Plan

Launch `ccx:planner` Agent:

> project_dir="{project_dir}"
> intent="{intent}", scope="{scope}", constraints="{constraints}"

Handle per main agent handling loop. Show plan. Create tasks with `TaskCreate`, set dependencies with `TaskUpdate`.

>>> CHECKPOINT("이 계획대로 진행할까요?", "계획 확인", ["Proceed", "Modify", "Cancel"])

---

## [Phase 3/5] Execute

For each task in dependency order, output `### Executing T{N}: {description}`:

**3a. Research** — Launch `ccx:researcher` Agent:
> project_dir="{project_dir}"
> task_description="{task_description}"

Handle per main agent handling loop.

**3b. Implement** — Launch `ccx:implementer` Agent:
> project_dir="{project_dir}"
> task_description="{task_description}"
> files="{from research}", impact_zone="{from research}"

Handle per main agent handling loop.

**3c. Review** — Launch `ccx:reviewer` Agent:
> project_dir="{project_dir}"
> task_description="{task_description}"
> changed_files="{from implement}", impact_zone="{from research}"

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
