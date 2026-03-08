# ccx Pipeline — Detailed Reference

> This file is a **reference document** for humans. The model executes from `SKILL.md` directly.
> Do NOT instruct the model to "Read PIPELINE.md" — all execution flow is in SKILL.md.

## Rules

**User interaction:** Only main agent talks to the user.

**AskUserQuestion protocol:**
1. ALWAYS call with `questions` array containing `question`, `header` (≤12 chars), `options` (2-4 items with `label` + `description`), `multiSelect: false`
2. After every call, check response. If empty → output `⚠️ 사용자 응답 없음. 파이프라인을 중단합니다.` → record failure → exit. NEVER fabricate answers.

**Checkpoint shorthand:** `>>> CHECKPOINT("질문", "header", ["Option1", "Option2", "Option3"])` means: call AskUserQuestion with those values. Standard behavior: "Proceed" → next phase, "Modify" → ask what to change → re-confirm, "Cancel" → record cancelled → exit.

**Subagent response protocol:** Defined in `{agents_dir}/_protocol.md`. The orchestrator checks for `STATUS: COMPLETE` or `STATUS: NEEDS_CONTEXT`.

**Main agent handling loop:** Apply to every subagent launch:
```
context = {task, phase_inputs}
for round in 1..3:
    result = launch_subagent(prompt + context)
    if COMPLETE → break
    if NEEDS_CONTEXT → questions → AskUserQuestion → context += {partial, user_answers}
    if no STATUS marker → treat as COMPLETE, break
round > 3 → CHECKPOINT("3회 시도 후에도 추가 맥락이 필요합니다.", "루프 초과", ["부분 결과로 진행", "추가 입력 제공", "취소"])
```

**Analysis cache protocol:**
- **Scope naming:** project-root-relative file path, no extension, lowercase, forward slashes.
- **Index-first:** Always start with `mcp__ccx__trigger_index(project_dir)`.
- For relevant scopes, call `mcp__ccx__get_scope_with_children(project_dir, scope)`.
- `fresh` → use as-is. `stale` → re-analyze changed files. `new` → full analysis.
- After implementation, call `mcp__ccx__mark_stale_cascade` on modified scopes.

---

## [Phase 0] Index (Optional)

1. Call `mcp__ccx__trigger_index("{project_dir}")`.
2. If `new_scopes` non-empty → invoke `/ccx:index` via `Skill` tool. No checkpoint.
3. All fresh → `Index: all scopes up to date.`

---

## [Phase 1/4] Adaptive Plan

Launch `ccx:planner` Agent:

> project_dir="{project_dir}"
> request="{user_request}"

The planner performs analysis (formerly a separate agent) AND task decomposition in one pass:
1. Loads project context, session, and scope cache via MCP tools
2. Determines intent, scope, constraints
3. Classifies complexity: `simple`, `medium`, or `complex`
4. Decomposes into ordered tasks

Handle per handling loop. Show result. Create tasks with `TaskCreate`.

>>> CHECKPOINT("분석 및 계획이 맞나요?", "계획 확인", ["Proceed", "Modify", "Cancel"])

---

## [Phase 2/4] Execute

For each task in dependency order, output `### Executing T{N}: {description}`:

### Adaptive execution by complexity

**simple** — Skip reviewer and per-task checkpoint:
> **2a. Research** → **2b. Implement**

**medium** — Standard pipeline:
> **2a. Research** → **2b. Implement** → **2c. Review**

**complex** — Standard + final synthesis:
> Same as medium per task. After ALL tasks complete, one additional `ccx:reviewer` launch with all changed_files for cross-task consistency.

### Per-task steps

**2a. Research** — Launch `ccx:researcher` Agent:
> project_dir, task_description

**2b. Implement** — Launch `ccx:implementer` Agent:
> project_dir, task_description, files (from research), impact_zone (from research)

**2c. Review** (medium/complex only) — Launch `ccx:reviewer` Agent:
> project_dir, task_description, changed_files (from implement), impact_zone (from research)

If implementer returned COMPLETE with non-trivial assumptions → present to user via AskUserQuestion with alternatives as options before review.

On reject → `git checkout -- {changed_files}` → re-implement with issues → re-review. Max 3 retries.

**Per-task checkpoint** (medium/complex only):

>>> CHECKPOINT("T{N} 구현 결과를 확인해주세요.\n\n{changed_files_summary}", "코드 확인", ["Approve", "Request changes", "Reject & redo"])

- "Approve" → proceed.
- "Request changes" → ask what to change (AskUserQuestion with options from context) → re-implement with user feedback → re-review → show again. Max 3 rounds.
- "Reject & redo" → ask for new direction (AskUserQuestion) → restart from 2b with user's input.

After approval, call `mcp__ccx__mark_stale_cascade` for affected scopes. Mark done via `TaskUpdate`.

---

## [Phase 3/4] Commit & Push

1. Run `git diff --stat`
2. Generate Conventional Commits message: `type(scope): description` + body

>>> CHECKPOINT("이 메시지로 커밋할까요?\n\n{commit_message}", "커밋 확인", ["Commit & Push", "Edit message", "Skip commit"])

3. If confirmed, stage + commit + push.

---

## [Phase 4/4] Record

Call `mcp__ccx__record_execution(project_dir, request, success, summary, changes)`.
Output: `Pipeline complete. {summary}`

---

## Error Handling

Critical failure → record via `mcp__ccx__record_execution` + report to user. Non-critical → fix + continue. Always leave codebase clean.
