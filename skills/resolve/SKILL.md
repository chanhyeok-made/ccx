---
name: resolve
description: "Review and answer ambiguities, or add annotations to enrich cached analysis"
disable-model-invocation: true
argument-hint: "[no arguments, or --add to add a new annotation]"
allowed-tools: AskUserQuestion, mcp__ccx__get_annotations, mcp__ccx__add_annotation, mcp__ccx__resolve_ambiguity
---

# Resolve & Annotate

Interactive skill to review annotations and resolve ambiguities recorded during `/index`.
Resolved ambiguities and user annotations enrich cached analysis, improving future `/run` quality.

## Mode

- **Resolve** (default): Show unresolved ambiguities for user to answer.
- **Add** (`--add` in `$ARGUMENTS`): Prompt user to add a new annotation to a specific scope.

## Resolve Flow

### 1. Load Unresolved

Call `mcp__ccx__get_annotations(project_dir, annotation_type="ambiguity", unresolved_only=True, limit=20)`.

If `total == 0`: Output `No unresolved ambiguities.` and stop.

### 2. Present

```
## Unresolved Ambiguities ({total} total)

1. **{scope}**: {question}
   Context: {content}

2. ...
```

### 3. Interactive Resolution

Use `AskUserQuestion` for each ambiguity one at a time:
- question: the annotation's `question` text
- header: first 12 chars of scope
- options: 2 suggested answers (infer from context) + "Skip"

If answered (not "Skip"):
- Call `mcp__ccx__resolve_ambiguity(project_dir, scope, question, answer)`.

Continue until done or user stops.

### 4. Summary

```
Resolved: {N} | Remaining: {total - N}
```

## Add Flow

### 1. Ask Scope

Use `AskUserQuestion`: "Which scope do you want to annotate?"
- Options: suggest recent or top-level scopes, or let user type.

### 2. Ask Type

Use `AskUserQuestion`: "What type of annotation?"
- Options:
  - **"domain"** — Business context, domain knowledge
  - **"architecture"** — Design rationale, system role
  - **"usage"** — How to use, gotchas, constraints

### 3. Ask Content

Use `AskUserQuestion`: "What's the annotation content?"
- Let user type freely.

### 4. Save

Call `mcp__ccx__add_annotation(project_dir, scope, annotation_type, content, added_by="user")`.
Output: `Added {type} annotation to {scope}.`

## Rules

- Every answer comes from the user — do NOT fabricate answers.
- Present one ambiguity at a time.
- Annotations persist in cache across sessions.

Arguments: $ARGUMENTS
