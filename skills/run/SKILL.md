---
name: run
description: "Full development pipeline: analyze -> plan -> implement -> review -> commit"
disable-model-invocation: true
argument-hint: "[request description]"
allowed-tools: Read, Bash, Agent, Skill, TaskCreate, TaskUpdate, TaskList, TaskGet, AskUserQuestion, mcp__ccx__record_execution, mcp__ccx__invalidate_analysis_cache, mcp__ccx__trigger_index, mcp__ccx__mark_stale_cascade, mcp__ccx__list_cached_scopes, mcp__ccx__get_scope_with_children, mcp__ccx__get_agent_config
---

# Full Development Pipeline

You are a **pure orchestrator**. Execute phases 0→5 in strict order. You do NOT read source files, load project context, or implement code — subagents do all work.

Your FIRST tool call MUST be `mcp__ccx__trigger_index`. Do NOT skip ahead.

## Phase 0: Index

1. Call `mcp__ccx__trigger_index(project_dir)`.
2. `new_scopes` non-empty → invoke `/ccx:index` via `Skill` tool. No checkpoint.
3. All fresh → output `Index: all scopes up to date.`

## Phase 1: Analyze

Launch `ccx:analyzer` Agent:
> project_dir="{project_dir}", request="{user_request}"

Handle per handling loop. Show result.

CHECKPOINT("분석 결과가 맞나요?", "분석 확인", ["Proceed", "Modify", "Cancel"])

## Phase 2: Plan

Launch `ccx:planner` Agent:
> project_dir="{project_dir}", intent="{intent}", scope="{scope}", constraints="{constraints}"

Handle per handling loop. Show plan. Create tasks via `TaskCreate`.

CHECKPOINT("이 계획대로 진행할까요?", "계획 확인", ["Proceed", "Modify", "Cancel"])

## Phase 3: Execute

**Agent Config Injection** — Before launching each subagent (researcher, implementer, reviewer), call `mcp__ccx__get_agent_config(project_dir, agent_name)` where `agent_name` matches the subagent type (e.g. `"researcher"`, `"implementer"`, `"reviewer"`). If the config exists (non-null response), append an `## Agent Config` block to the subagent prompt:

```
## Agent Config
rules: {rules from get_agent_config}
context: {context from get_agent_config}
disabled_rules: {disabled_rules from get_agent_config}
```

For each task in dependency order, output `### Executing T{N}: {description}`:

**3a. Research** — Launch `ccx:researcher` Agent:
> project_dir="{project_dir}", current_depth=1, task_description="{task}"

**3b. Implement** — Launch `ccx:implementer` Agent:
> project_dir="{project_dir}", current_depth=1, task_description="{task}", files="{from 3a}", impact_zone="{from 3a}"

**3c. Review** — Launch `ccx:reviewer` Agent:
> project_dir="{project_dir}", current_depth=1, task_description="{task}", changed_files="{from 3b}", impact_zone="{from 3a}"

On reject → re-implement → re-review (max 3). After approval, `mcp__ccx__mark_stale_cascade` for affected scopes. Mark task done via `TaskUpdate`.

CHECKPOINT("T{N} 결과를 확인해주세요.\n\n{changed_files_summary}", "코드 확인", ["Approve", "Request changes", "Reject & redo"])

## Phase 4: Commit

1. `git diff --stat`
2. Draft conventional commit message.

CHECKPOINT("커밋할까요?\n\n{commit_message}", "커밋 확인", ["Commit & Push", "Edit message", "Skip commit"])

## Phase 5: Record

Call `mcp__ccx__record_execution(project_dir, request, success, summary, changes)`.
Output: `Pipeline complete. {summary}`

---

## Rules

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
