# ccx Pipeline — Detailed Instructions

## Phase 1: Load Context

1. Call `mcp__ccx__load_project_context` with the current project directory to get:
   - project_name, stack, architecture, structure, exception_rules
2. Call `mcp__ccx__get_session` with the current project directory to get:
   - Previous execution history (for follow-up context)
3. Store these as context for all subsequent phases.

## Phase 2: Analyze Request

Convert the user's raw request into structured requirements.

**Rules:**
- Do NOT assume anything ambiguous. If the request is unclear, use `AskUserQuestion` to clarify.
- Keep the intent to ONE sentence.
- Scope should be module/layer/feature level, not file level.

**Produce mentally (do not output to user unless verbose):**
- `intent`: One sentence summary of the goal
- `scope`: Affected modules/layers/features
- `constraints`: Any constraints mentioned or implied (include project exception_rules)
- If there is previous session context, incorporate it (e.g., "add tests for the function created in the last request")

## Phase 3: Plan

Decompose the analyzed requirements into executable tasks.

**Rules:**
- Each task must be independently implementable.
- Specify dependencies explicitly.
- One logical change per task.
- If a task touches multiple modules, split it.

**Actions:**
1. Create tasks using `TaskCreate` for each planned task.
2. Set up dependencies between tasks using `TaskUpdate` (addBlockedBy/addBlocks).
3. Briefly show the user the task list with `TaskList`.

## Phase 4: Execute Tasks

For each task (in dependency order), execute this loop:

### Step 4a: Research (read-only)

Use `Agent` with `subagent_type: "Explore"` to:
- Find files relevant to the task
- Understand the codebase structure in the task's scope
- Map dependencies (imports/imported-by)
- Identify the impact zone (what files could be affected by changes)

The research agent should NOT modify any files.

### Step 4b: Implement

Use `Agent` with `subagent_type: "general-purpose"` to:
- Implement the task using the research findings
- Follow patterns shown in existing code
- Respect ALL exception rules without exception
- Produce minimal, focused changes
- Report back: what files were changed, what assumptions were made

The implementation agent prompt should include:
- The task description
- Relevant files and their contents (from research)
- Exception rules that apply
- Dependency map

### Step 4c: Review

After implementation, review the changes inline:

1. Read all modified files to verify correctness.
2. Call `mcp__ccx__check_rules` with a description of the changes to verify exception rule compliance.
3. Evaluate against these criteria (priority order):
   - **CORRECTNESS**: Does the change achieve its stated intent?
   - **SIDE EFFECTS**: Does it break anything in the impact zone?
   - **RULES**: Does it respect all exception rules?
   - **PATTERNS**: Does it follow existing code patterns?
   - **EDGE CASES**: Are obvious edge cases handled?

4. If critical issues found:
   - Fix them directly or delegate back to an implementation agent
   - Re-review after fixes
   - Maximum 3 retry attempts per task

5. Mark the task as completed with `TaskUpdate` when review passes.

## Phase 5: Commit

After all tasks are completed:

1. Run `git diff` to see all changes.
2. Generate a commit message following Conventional Commits:
   - Format: `type(scope): description`
   - Types: feat, fix, refactor, docs, test, chore
   - Description: imperative mood, lowercase, no period
   - Body: what changed and why (not how)
3. Present the commit message to the user and ask for confirmation.
4. If confirmed, create the commit.
5. Push to the remote branch: `git push`.

## Phase 6: Record

Call `mcp__ccx__record_execution` with:
- `project_dir`: current project directory
- `request`: the original user request
- `success`: whether the pipeline succeeded
- `summary`: brief summary of what was done
- `changes`: list of file changes (each with path, type, intent)

## Error Handling

- If any phase fails critically, record the failure via `record_execution` and report to the user.
- For non-critical warnings during review, fix if possible and continue.
- Always leave the codebase in a clean state — if implementation fails partway, consider reverting incomplete changes.
