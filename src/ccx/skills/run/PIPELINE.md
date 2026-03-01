# ccx Pipeline — Detailed Instructions

## IMPORTANT: Main Agent Role

You are a **pure orchestrator**. You coordinate subagents and handle ALL user interaction.

**What you hold:**
- `project_dir` path
- User's original request
- Analysis summary (a few lines — produced by subagent)
- Task list (IDs + one-line descriptions)
- Per-task status (one line each: success/fail + summary)

**What you do:**
- Launch subagents and receive their results
- ALL user communication: show progress, ask questions, get confirmations
- Call `AskUserQuestion` with **concrete options** whenever user input is needed

**What you do NOT do:**
- Read file contents
- Load project context (subagents do this via MCP)
- Analyze code or make implementation decisions

## CRITICAL: User Interaction Rules

**Only the main agent talks to the user.** Subagents NEVER interact with the user directly.

When a subagent needs user input (e.g., ambiguities found):
1. Subagent returns the questions to main agent
2. Main agent presents them to user via `AskUserQuestion` **with concrete options**
3. Main agent passes answers back by resuming or re-launching the subagent

**AskUserQuestion format rules:**
- ALWAYS provide `options` with 2-4 concrete choices
- NEVER call AskUserQuestion without options — it will auto-submit and skip user input
- Each question must have a `header` (short label) and clear `description` per option
- For ambiguities: convert each into a multiple-choice question with sensible defaults
- For checkpoints: options are always "Proceed" / "Modify" / "Cancel"

**Subagent prompt rules:**
- ALWAYS include this line in every subagent prompt: "Do NOT use AskUserQuestion. Return questions to the main agent."
- Subagents must return ambiguities/questions as structured data, not attempt to ask the user

---

## [Phase 1/5] Analyze Request

Launch `Agent` with `subagent_type: "general-purpose"`. Prompt:

> You are an Analyzer. Load project context by calling `mcp__ccx__load_project_context("{project_dir}")` and `mcp__ccx__get_session("{project_dir}")`.
>
> Then analyze this request: "{user_request}"
>
> Rules:
> - If anything is ambiguous, list it as a question with 2-3 suggested answers — do NOT assume.
> - Keep intent to ONE sentence.
> - Scope = module/layer/feature level, not file level.
> - Incorporate any previous session context.
> - Do NOT use AskUserQuestion. Return questions to the main agent.
>
> Return EXACTLY this format:
> - Intent: [one sentence]
> - Scope: [comma-separated list]
> - Constraints: [list, including relevant project exception rules]
> - Ambiguities: [list of {question, suggested_answers: [option1, option2, ...]} or "none"]

**Show the analysis result to the user.**

### Resolve Ambiguities (if any)

If the analyzer returned ambiguities:
1. For EACH ambiguity, call `AskUserQuestion` with the question and suggested answers as options.
2. Collect all answers.
3. Re-launch the analyzer subagent with the original request + answers to produce a final analysis.
4. Show the updated analysis to the user.

### >>> CHECKPOINT: Confirm Analysis

Call `AskUserQuestion`:
- question: "Is this analysis correct?"
- options: "Proceed", "Modify", "Cancel"

On "Proceed": continue to Phase 2.
On "Modify": ask what to change, update analysis, re-confirm.
On "Cancel": jump to Phase 5 (Record) with cancelled status.

---

## [Phase 2/5] Plan

Launch `Agent` with `subagent_type: "general-purpose"`. Prompt:

> You are a Planner. Load project context by calling `mcp__ccx__load_project_context("{project_dir}")`.
>
> Based on this confirmed analysis:
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
> - Do NOT use AskUserQuestion. Return results to the main agent.
>
> Return a table:
> | # | Task | Target modules | Complexity | Depends On |
> And an execution order like: [T1] → [T2, T3] → [T4]

**Show the plan to the user.**

Create tasks with `TaskCreate` and set dependencies with `TaskUpdate`.

### >>> CHECKPOINT: Confirm Plan

Call `AskUserQuestion`:
- question: "Should I proceed with this plan?"
- options: "Proceed", "Modify", "Cancel"

On "Proceed": continue to Phase 3.
On "Modify": ask what to change, update tasks, re-confirm.
On "Cancel": jump to Phase 5 (Record) with cancelled status.

---

## [Phase 3/5] Execute Tasks

For each task (in dependency order):

Output: `### Executing T{N}: {description}`

### Step 3a: Research (subagent — read-only)

Launch `Agent` with `subagent_type: "Explore"`. Prompt:

> Task: {task_description}
> Project dir: {project_dir}
>
> Find files relevant to this task. Load project context via `mcp__ccx__load_project_context("{project_dir}")` if needed.
> Do NOT use AskUserQuestion. Return results to the main agent.
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
> Do NOT use AskUserQuestion. Return results to the main agent.
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
> Do NOT use AskUserQuestion. Return results to the main agent.
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

## [Phase 4/5] Commit & Push

After all tasks are completed:

1. Run `git diff --stat` (NOT full diff).
2. Generate a Conventional Commits message from the accumulated task summaries:
   - Format: `type(scope): description`
   - Types: feat, fix, refactor, docs, test, chore
   - Body: what changed and why
3. Call `AskUserQuestion` with:
   - question: "Commit with this message?" (show the message)
   - options: "Commit & Push", "Edit message", "Skip commit"
4. If confirmed, stage, commit, and push.

---

## [Phase 5/5] Record

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
