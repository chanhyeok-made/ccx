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
- For relevant scopes, call `mcp__ccx__get_scope_with_children(project_dir, scope)`.
- `fresh` → use as-is. `stale` → re-analyze changed files. `new` → full analysis.
- After implementation, call `mcp__ccx__mark_stale_cascade` on modified scopes.
- Indexing is handled by subagents (planner triggers background `ccx:index` for new/stale scopes, researcher fires background indexer for analyzed scopes). No explicit indexing phase in the pipeline.

---

## [Phase 0/4] Worktree Setup

Create an isolated git worktree for this session to enable concurrent work on the same project from multiple Claude sessions.

1. **Capture current state** before creating the worktree:
   - `original_dir` = current working directory (the original repository path)
   - `base_branch` = `git branch --show-current` (the branch checked out in the original repo; used as the PR target in Phase 3)
2. Call `EnterWorktree` to create a worktree. EnterWorktree creates a new branch based on the current HEAD (i.e., whatever `base_branch` points to) and sets up the worktree at a separate path. This changes the session's working directory to the worktree path.
3. After setup, use the new working directory as `project_dir` for all subsequent phases and subagent launches.
4. All MCP tool calls, git operations, and `.ccx/` storage operate within the worktree, isolated from the main repository.
5. Retain `base_branch` — it is needed in Phase 3 to set the PR target.

This step is automatic and requires no user interaction.

---

## [Phase 0.5/4] Purpose Clarification

Distill the raw user request into a clear purpose statement and task title before planning begins.

1. Launch `ccx:clarifier` Agent:
   > project_dir="{project_dir}"
   > request="{user_request}"
2. Handle per handling loop. Extract `task_title` and `purpose` from the result.
3. Call `mcp__ccx__set_task_title(project_dir, task_title, session_id)` to persist the title for notification context (e.g., macOS notifications can display what the pipeline is working on).
4. Retain `task_title` and `purpose` for Phase 1:
   - Pass `purpose` (enriched request) to the planner instead of the raw `user_request`.
   - Display `task_title` in the Phase 1 checkpoint so the user sees the distilled intent.

This step is automatic and requires no user interaction.

---

## [Phase 1/4] Adaptive Plan

Launch `ccx:planner` Agent:

> project_dir="{project_dir}"
> request="{purpose}"

The planner performs analysis (formerly a separate agent) AND task decomposition in one pass:
1. Loads project context, session, and scope cache via MCP tools
2. Determines intent, scope, constraints
3. Classifies complexity: `simple`, `medium`, or `complex`
4. Decomposes into ordered tasks

Handle per handling loop. Show result. Create tasks with `TaskCreate`.

>>> CHECKPOINT("[{task_title}] 분석 및 계획이 맞나요?", "계획 확인", ["Proceed", "Modify", "Cancel"])

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

### Verdict routing (즉시 실행, 숙고 금지)

| Verdict | Action |
|---------|--------|
| approve | Mark task done, proceed to next task |
| request_changes | CHECKPOINT로 사용자에게 변경 요청 표시 → 사용자 승인 시 `git checkout -- {changed_files}` → re-implement with reviewer feedback → re-review |
| reject | 즉시 `git checkout -- {changed_files}` → re-implement with reviewer feedback appended → re-review (max 3 cycles) |

**reject 즉시 재실행 템플릿** (implementer 재호출 시 task_description에 append):
> 이전 구현이 리뷰어에 의해 reject되었습니다.
> 리뷰어 피드백: {reviewer_issues}
> git checkout으로 파일을 복원했습니다. 피드백을 반영하여 재구현하세요.

**Per-task checkpoint** (medium/complex only, approve verdict에서만 표시):

>>> CHECKPOINT("T{N} 구현 결과를 확인해주세요.\n\n{changed_files_summary}", "코드 확인", ["Approve", "Request changes"])

- "Approve" → proceed.
- "Request changes" → ask what to change (AskUserQuestion with options from context) → re-implement with user feedback → re-review → show again. Max 3 rounds.

After approval, call `mcp__ccx__mark_stale_cascade` for affected scopes. Mark done via `TaskUpdate`.

---

## [Phase 3/4] Commit & Create PR

1. Run `git diff --stat`
2. Generate Conventional Commits message: `type(scope): description` + body

>>> CHECKPOINT("이 메시지로 커밋할까요?\n\n{commit_message}", "커밋 확인", ["Commit & Create PR", "Edit message", "Skip commit"])

3. If confirmed, stage + commit + push the worktree branch, then create a pull request targeting `base_branch` (captured in Phase 0). This ensures the PR targets the branch the user was on when they started the pipeline, not a hardcoded default.
4. **Worktree cleanup** (only after successful commit & PR):
   1. `cd {original_dir}` — return to the original repository path saved in Phase 0. This is required because a worktree cannot remove itself from within its own directory.
   2. `git worktree remove {worktree_path}` — remove the worktree and its working directory.
   - If the user chose **"Skip commit"**, do NOT remove the worktree. The user may want to continue working in it manually or resume later.

---

## [Phase 4/4] Record

Call `mcp__ccx__record_execution(project_dir, request, success, summary, changes)`.
Output: `Pipeline complete. {summary}`

---

## Error Handling

Critical failure → record via `mcp__ccx__record_execution` + report to user. Non-critical → fix + continue. Always leave codebase clean.
