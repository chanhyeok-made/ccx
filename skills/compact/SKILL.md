---
name: compact
description: Summarize current session context and save compaction summary for session continuity
disable-model-invocation: true
argument-hint: "[transcript_path (optional)]"
allowed-tools: mcp__ccx__get_compaction_context, mcp__ccx__save_compaction_summary
---

# Compact Session Context

Summarize the current session's conversation into a structured compaction summary. The summary is designed to be read by a future agent continuing work on this project, preserving essential context across session boundaries.

## Steps

### 1. Gather Context

Call `mcp__ccx__get_compaction_context(transcript_path)` to retrieve:
- `fill_pct`: how full the current context window is (0.0 to 1.0)
- `model`: the model name in use
- `conversation_text`: recent conversation text from the session
- `session_id`: current session identifier

Parameters:
- `transcript_path`: path to the transcript file (passed by the hook, or current session)

### 2. Analyze and Summarize

Read the `conversation_text` carefully and extract the following fields:

- **`summary`** (string): A concise 2-3 sentence summary of what was accomplished in this session. Capture the intent and progress of the work, not just a list of actions.

- **`changed_files`** (list of strings): File paths that were actually modified or created during the session. Only include files that were changed via Edit, Write, or NotebookEdit tool calls. Do NOT include files that were merely read or searched.

- **`key_decisions`** (list of strings): Important decisions the user explicitly made during the session. Only include choices the user actively confirmed or directed. Do NOT infer decisions that were not clearly stated.

- **`pending_tasks`** (list of strings): Tasks that are clearly unfinished or explicitly deferred. Only include items where there is concrete evidence of incompleteness. Do NOT speculate about what might still need to be done.

### 3. Save Summary

Call `mcp__ccx__save_compaction_summary` with the extracted fields plus the context from step 1:

```
mcp__ccx__save_compaction_summary(
  project_dir="{project_dir}",
  summary="{summary}",
  changed_files=[...],
  key_decisions=[...],
  pending_tasks=[...],
  context_fill_pct={fill_pct from step 1},
  model="{model from step 1}",
  session_id="{session_id from step 1}"
)
```

### 4. Report

Present a brief confirmation to the user:

```
## Compaction Saved

**Summary**: {summary}

**Changed files**: {count} files
**Key decisions**: {count} items
**Pending tasks**: {count} items
```

## Rules

- Read the transcript thoroughly before summarizing. Understand the narrative arc of the session: what the user wanted, what was done, and where things stand.
- Produce a summary that gives a new agent enough context to continue seamlessly. Include relevant module names, design choices, and unresolved issues.
- `changed_files`: only files modified via Edit, Write, or NotebookEdit. Exclude files only accessed via Read, Grep, or Glob.
- `key_decisions`: only explicit user choices. If the user said "use approach A instead of B" or confirmed a design, include it. Do NOT include decisions made autonomously by the agent.
- `pending_tasks`: only items with clear evidence of being incomplete. An unfinished TODO mentioned by the user counts. A vague "we might also want X" does not.
- Do NOT ask the user any questions. This is a fully automated process.

Arguments: $ARGUMENTS
