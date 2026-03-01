# ccx Pipeline — Detailed Instructions

## IMPORTANT: Main Agent Role

You are a **pure orchestrator**. You do NOT do any heavy thinking or file reading yourself.

**What you hold:**
- `project_dir` path
- User's original request
- Analysis summary (a few lines — produced by subagent)
- Task list (IDs + one-line descriptions)
- Per-task status (one line each: success/fail + file list)

**What you do NOT do:**
- Read file contents
- Load project context into your own context
- Analyze code yourself
- Make implementation decisions

**Everything else is delegated to subagents.** Each subagent loads what it needs via MCP tools (`mcp__ccx__load_project_context`, `mcp__ccx__get_session`, `mcp__ccx__check_rules`).

## IMPORTANT: Progress & Confirmation Rules

1. **Always show progress**: At the start of each phase, output a status line like `## [Phase N/6] Phase Name`.
2. **Mandatory checkpoints**: You MUST get user confirmation with `AskUserQuestion` after Phase 2 (Analyze) and Phase 3 (Plan). Do NOT skip these.
3. **Show your work**: Present analysis and plan in full detail so the user can make informed decisions.

---

## [Phase 1/6] Analyze Request

Launch `Agent` with `subagent_type: "general-purpose"`. Prompt:

> You are an Analyzer. Load project context by calling `mcp__ccx__load_project_context("{project_dir}")` and `mcp__ccx__get_session("{project_dir}")`.
>
> Then analyze this request: "{user_request}"
>
> Rules:
> - If anything is ambiguous, list it as a question — do NOT assume.
> - Keep intent to ONE sentence.
> - Scope = module/layer/feature level, not file level.
> - Incorporate any previous session context.
>
> Return EXACTLY this format:
> - Intent: [one sentence]
> - Scope: [comma-separated list]
> - Constraints: [list, including relevant project exception rules]
> - Ambiguities: [questions if any, or "none"]

**Show the result to the user.**

If ambiguities were listed, use `AskUserQuestion` to resolve them, then update the analysis.

### >>> CHECKPOINT: Confirm Analysis

Use `AskUserQuestion`:
- "Is this analysis correct?"
- Options: "Proceed" / "Modify" / "Cancel"

If "Modify": ask what to change, update, re-confirm.
If "Cancel": jump to Phase 5 (Record) with cancelled status.

---

## [Phase 2/6] Plan

Launch `Agent` with `subagent_type: "general-purpose"`. Prompt:

> You are a Planner. Load project context by calling `mcp__ccx__load_project_context("{project_dir}")`.
>
> Based on this analysis:
> - Intent: {intent}
> - Scope: {scope}
> - Constraints: {constraints}
>
> Decompose into executable tasks.
>
> Rules:
> - Each task must be independently implementable.
> - Specify dependencies explicitly.
> - One logical change per task.
> - If a task touches multiple modules, split it.
>
> Return a table:
> | # | Task | Target modules | Complexity | Depends On |
> And an execution order like: [T1] → [T2, T3] → [T4]

**Show the plan to the user.**

Create tasks with `TaskCreate` and set dependencies with `TaskUpdate`.

### >>> CHECKPOINT: Confirm Plan

Use `AskUserQuestion`:
- "Should I proceed with this plan?"
- Options: "Proceed" / "Modify" / "Cancel"

If "Modify": ask what to change, update tasks, re-confirm.
If "Cancel": jump to Phase 5 (Record) with cancelled status.

---

## [Phase 3/6] Execute Tasks

For each task (in dependency order):

Output: `### Executing T{N}: {description}`

### Step 3a: Research (subagent — read-only)

Launch `Agent` with `subagent_type: "Explore"`. Prompt:

> Task: {task_description}
> Project dir: {project_dir}
>
> Find files relevant to this task. Load project context via `mcp__ccx__load_project_context("{project_dir}")` if needed.
>
> Return ONLY:
> - Relevant file paths with one-line reasons
> - Key dependency relationships (imports/imported-by)
> - Impact zone (files that could break)
>
> Do NOT return file contents.

### Step 3b: Implement (subagent — read/write)

Launch `Agent` with `subagent_type: "general-purpose"`. Prompt:

> Task: {task_description}
> Project dir: {project_dir}
> Relevant files: {file paths from research}
> Impact zone: {impact zone from research}
>
> Load project rules via `mcp__ccx__load_project_context("{project_dir}")`.
> Read the relevant files, implement the changes.
> Follow existing code patterns. Respect ALL exception rules.
> Produce minimal, focused changes.
>
> Return ONLY:
> - List of changed files (path, type: create/modify/delete, one-line intent)
> - Assumptions made (if any)

### Step 3c: Review (subagent — read-only)

Launch `Agent` with `subagent_type: "general-purpose"`. Prompt:

> Task: {task_description}
> Changed files: {list from implementation}
> Impact zone: {from research}
> Project dir: {project_dir}
>
> Call `mcp__ccx__check_rules` to get the project rule checklist.
> Read all changed files and verify:
> 1. CORRECTNESS — does it achieve the stated intent?
> 2. SIDE EFFECTS — does it break anything in the impact zone?
> 3. RULES — does it respect all project rules?
> 4. PATTERNS — does it follow existing code patterns?
> 5. EDGE CASES — are obvious edge cases handled?
>
> Return ONLY:
> - Verdict: approve / reject / request_changes
> - Issues: [{severity, file, description, fix_suggestion}] (empty if approved)
> - Summary: one line

**On reject or request_changes:**
- Launch a new implementation subagent with the issues
- Re-review with a new review subagent
- Maximum 3 retries per task

Mark task completed with `TaskUpdate`.

Output: `Task T{N} complete: {one-line summary}`

---

## [Phase 4/6] Commit & Push

After all tasks are completed:

1. Run `git diff --stat` (NOT full diff).
2. Generate a Conventional Commits message from the accumulated task summaries:
   - Format: `type(scope): description`
   - Types: feat, fix, refactor, docs, test, chore
   - Body: what changed and why
3. Present to user with `AskUserQuestion` for confirmation.
4. If confirmed, stage, commit, and push.

---

## [Phase 5/6] Record

Call `mcp__ccx__record_execution` with:
- `project_dir`: current project directory
- `request`: original user request
- `success`: whether the pipeline succeeded
- `summary`: brief summary
- `changes`: list of file changes

Output: `Pipeline complete. {summary}`

---

## Error Handling

- If any phase fails critically, record via `mcp__ccx__record_execution` and report to the user.
- For non-critical review warnings, fix if possible and continue.
- Always leave the codebase in a clean state.
