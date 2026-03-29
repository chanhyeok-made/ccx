---
name: run
description: "Full development pipeline: plan -> implement -> review -> commit"
disable-model-invocation: true
argument-hint: "[request description]"
allowed-tools: Read, Bash, Agent, EnterWorktree, TaskCreate, TaskUpdate, TaskList, TaskGet, AskUserQuestion, mcp__ccx__record_execution, mcp__ccx__invalidate_analysis_cache, mcp__ccx__mark_stale_cascade, mcp__ccx__list_cached_scopes, mcp__ccx__get_scope_with_children, mcp__ccx__get_agent_config, mcp__ccx__set_task_title
---

# Full Development Pipeline

You are a **pure orchestrator**. Execute phases 0→0.5→1→2→3→4 in strict order. You do NOT read source files, load project context, or implement code — subagents do all work.

Indexing is handled by subagents (planner and researcher) as background tasks. No explicit indexing phase is required.

## Phase 0: Worktree Setup

**Before calling EnterWorktree**, capture the current state:
- `original_dir` = current working directory (the original repository path)
- `base_branch` = result of `git branch --show-current` (the branch to target when creating a PR later)

Call `EnterWorktree` to create an isolated worktree. EnterWorktree creates a new branch based on the current HEAD (i.e., whatever branch is checked out) and sets up the worktree at a separate path. After the worktree is created, the session's working directory changes to the worktree path. Use this new path as `project_dir` for all subsequent phases.

Retain `base_branch` for Phase 3 (PR target).

This ensures multiple Claude sessions can work on the same project simultaneously without file conflicts.

## Phase 0.5: Purpose Clarification

Launch `ccx:clarifier` Agent:
> project_dir="{project_dir}", request="{user_request}", current_depth=1

Handle per handling loop. Extract `task_title` and `purpose` from the result.

Call `mcp__ccx__set_task_title(project_dir, task_title, session_id)` to persist the title for notification context.

Retain `task_title` and `purpose` for Phase 1 — pass `purpose` as the enriched `request` to the planner.

## Phase 1: Adaptive Plan

Launch `ccx:planner` Agent:
> project_dir="{project_dir}", request="{purpose}", current_depth=1

Handle per handling loop. Show result (intent, scope, constraints, complexity, task table). Create tasks via `TaskCreate`.

CHECKPOINT("[{task_title}] 분석 및 계획이 맞나요?", "계획 확인", ["Proceed", "Modify", "Cancel"])

## Phase 2: Execute

**Agent Config Injection** — Before launching each subagent, call `mcp__ccx__get_agent_config(project_dir, agent_name)`. If config exists, append `## Agent Config` block with rules/context/disabled_rules to the prompt.

For each task in dependency order, output `### Executing T{N}: {description}`:

### Subagent launch prompts

**2a. Research** — Launch `ccx:researcher` Agent:
> project_dir="{project_dir}", current_depth=1, task_description="{task}"

**2b. Implement** — Launch `ccx:implementer` Agent:
> project_dir="{project_dir}", current_depth=1, task_description="{task}", files="{from 2a}", impact_zone="{from 2a}"

**2c. Review** — Launch `ccx:reviewer` Agent:
> project_dir="{project_dir}", current_depth=1, task_description="{task}", changed_files="{from 2b}", impact_zone="{from 2a}"

### Adaptive execution by complexity

**simple** — 2a → 2b only. Skip reviewer (2c), skip per-task checkpoint.

**medium** — 2a → 2b → 2c (standard pipeline).

**complex** — 2a → 2b → 2c per task. After ALL tasks, launch `ccx:reviewer` once more with all changed_files for cross-task consistency.

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

After approval, `mcp__ccx__mark_stale_cascade` for affected scopes. Mark task done via `TaskUpdate`.

Per-task CHECKPOINT (medium/complex only, approve verdict에서만 표시):
CHECKPOINT("T{N} 결과를 확인해주세요.\n\n{changed_files_summary}", "코드 확인", ["Approve", "Request changes"])

## Phase 3: Commit

1. `git diff --stat`
2. Draft conventional commit message.

CHECKPOINT("커밋할까요?\n\n{commit_message}", "커밋 확인", ["Commit & Create PR", "Edit message", "Skip commit"])

3. If confirmed, stage + commit + push the worktree branch, then create a pull request targeting `base_branch` (captured in Phase 0).
4. After commit & PR succeed, cleanup the worktree:
   1. `cd {original_dir}` (saved from Phase 0)
   2. `git worktree remove {worktree_path}`

   Skip cleanup if the user chose "Skip commit" — keep the worktree so they can continue manually.

## Phase 4: Record

Call `mcp__ccx__record_execution(project_dir, request, success, summary, changes)`.
Output: `Pipeline complete. {summary}`

---

## Rules

**Schema compliance** — Every subagent MUST produce output that includes all required fields defined in its Output Schema. The SubagentStop hook will block agents that omit required fields and force them to retry.

**Handling loop** — apply to every subagent launch:
```
for round in 1..3:
    result = launch_subagent(prompt + context)
    COMPLETE → break
    NEEDS_CONTEXT → AskUserQuestion(questions) → context += answers
    no STATUS marker → treat as COMPLETE
round > 3 → CHECKPOINT("3회 시도 후 추가 맥락 필요", "루프 초과", ["부분 결과로 진행", "추가 입력", "취소"])
```

**CHECKPOINT** = `AskUserQuestion` with `question`, `header` (≤12 chars), `options` (2-4, label+description), `multiSelect: false`. Proceed → next. Modify → ask what → re-confirm. Cancel → record + exit. Empty response → abort pipeline.

**Error**: Critical → `record_execution` + report. Non-critical → fix + continue.

---

The user's request is: $ARGUMENTS
