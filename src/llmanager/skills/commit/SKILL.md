---
name: commit
description: "Generate and create a conventional commit for current changes"
disable-model-invocation: true
argument-hint: "[optional: additional context for commit message]"
allowed-tools: Read, Grep, Glob, Bash, AskUserQuestion, mcp__llmanager__load_project_context, mcp__llmanager__record_execution
---

# Generate Commit

You are the Committer. Generate a commit message and create a git commit for the current changes.

## Steps

1. Run `git status` to see all changed files.
2. Run `git diff` and `git diff --staged` to understand the changes.
3. Call `mcp__llmanager__load_project_context` for project context.
4. Analyze all changes and generate a commit message.
5. Present the commit message to the user for confirmation via `AskUserQuestion`.
6. If confirmed, stage and commit the changes.
7. Call `mcp__llmanager__record_execution` to record the action.

## Commit Message Rules

- Follow **Conventional Commits**: `type(scope): description`
- Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`
- Description: imperative mood, lowercase, no period
- Body: what changed and why (not how)
- If there are breaking changes, include `BREAKING CHANGE:` in the footer

## Output Format

Present to user before committing:

```
type(scope): description

body explaining what and why

[BREAKING CHANGE: description if applicable]
```

**Files to commit:**
- [path]: [modified/created/deleted]

After the user confirms, create the commit and report the result.

Additional context: $ARGUMENTS
