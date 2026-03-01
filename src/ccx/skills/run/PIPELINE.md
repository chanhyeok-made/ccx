# ccx Pipeline — Detailed Instructions

## IMPORTANT: Progress & Confirmation Rules

1. **Always show progress**: At the start of each phase, output a status line like `## [Phase N/6] Phase Name`. The user must be able to see where the pipeline is at all times.
2. **Mandatory checkpoints**: You MUST get user confirmation with `AskUserQuestion` after Phase 2 (Analyze) and Phase 3 (Plan) before proceeding. Do NOT skip these.
3. **Show your work**: When presenting analysis or plan, show the full details — not just a summary. The user needs enough information to make a decision.

---

## [Phase 1/6] Load Context

1. Call `mcp__ccx__load_project_context` with the current project directory.
2. Call `mcp__ccx__get_session` with the current project directory.
3. Store these as context for all subsequent phases.

Output: `Context loaded: {project_name}, {stack summary}, {N} previous records`

---

## [Phase 2/6] Analyze Request

Convert the user's raw request into structured requirements.

**Rules:**
- Do NOT assume anything ambiguous. If the request is unclear, use `AskUserQuestion` to clarify BEFORE producing the analysis.
- Keep the intent to ONE sentence.
- Scope should be module/layer/feature level, not file level.
- If there is previous session context, incorporate it.

**Output to user:**

```
## Analysis

- **Intent**: [one sentence summary]
- **Scope**: [list of affected modules/layers/features]
- **Constraints**: [any constraints, including relevant exception rules]
```

### >>> CHECKPOINT: Confirm Analysis

Use `AskUserQuestion` to ask the user:
- "Is this analysis correct? Should I proceed with planning?"
- Options: "Proceed" / "Modify" / "Cancel"

If "Modify": ask what to change, update the analysis, and re-confirm.
If "Cancel": stop the pipeline and record as cancelled.
Only proceed to Phase 3 after user confirms.

---

## [Phase 3/6] Plan

Decompose the analyzed requirements into executable tasks.

**Rules:**
- Each task must be independently implementable.
- Specify dependencies explicitly.
- One logical change per task.
- If a task touches multiple modules, split it.

**Actions:**
1. Create tasks using `TaskCreate` for each planned task.
2. Set up dependencies between tasks using `TaskUpdate` (addBlockedBy/addBlocks).

**Output to user — show the full plan:**

```
## Execution Plan

| # | Task | Target | Complexity | Depends On |
|---|------|--------|------------|------------|
| T1 | ... | ... | small/medium/large | - |
| T2 | ... | ... | ... | T1 |

Execution order: [T1] → [T2, T3] → [T4]
```

### >>> CHECKPOINT: Confirm Plan

Use `AskUserQuestion` to ask the user:
- "Should I proceed with this plan?"
- Options: "Proceed" / "Modify" / "Cancel"

If "Modify": ask what to change, update the tasks, and re-confirm.
If "Cancel": stop the pipeline and record as cancelled.
Only proceed to Phase 4 after user confirms.

---

## [Phase 4/6] Execute Tasks

For each task (in dependency order), execute this loop:

Output at start of each task: `### Executing T{N}: {description}`

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

Output after each task: `Task T{N} complete: {brief summary of changes}`

---

## [Phase 5/6] Commit & Push

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

---

## [Phase 6/6] Record

Call `mcp__ccx__record_execution` with:
- `project_dir`: current project directory
- `request`: the original user request
- `success`: whether the pipeline succeeded
- `summary`: brief summary of what was done
- `changes`: list of file changes (each with path, type, intent)

Output: `Pipeline complete. {summary}`

---

## Error Handling

- If any phase fails critically, record the failure via `record_execution` and report to the user.
- For non-critical warnings during review, fix if possible and continue.
- Always leave the codebase in a clean state — if implementation fails partway, consider reverting incomplete changes.
