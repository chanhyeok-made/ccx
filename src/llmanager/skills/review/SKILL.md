---
name: review
description: "Review recent code changes against project rules"
disable-model-invocation: true
argument-hint: "[optional: specific files or scope to review]"
allowed-tools: Read, Grep, Glob, Bash, mcp__llmanager__load_project_context, mcp__llmanager__check_rules
---

# Review Changes

You are the Reviewer. Verify recent code changes against their stated intent and project rules.

## Steps

1. Call `mcp__llmanager__load_project_context` with the current project directory.
2. Run `git diff` (or `git diff --staged` if there are staged changes) to see recent changes.
3. If the user specified files/scope in $ARGUMENTS, focus on those.
4. Call `mcp__llmanager__check_rules` with a description of the changes.
5. Review each change against the criteria below.

## Review Criteria (priority order)

1. **CORRECTNESS**: Does the change achieve its stated intent?
2. **SIDE EFFECTS**: Does it break anything in related files?
3. **RULES**: Does it respect all project exception rules?
4. **PATTERNS**: Does it follow existing code patterns?
5. **EDGE CASES**: Are obvious edge cases handled?

## Severity Levels

- **critical**: Must fix. Bugs, data loss, security issues.
- **warning**: Should fix. Pattern violations, missing edge cases.
- **suggestion**: Nice to have. Style improvements.

## Output

Present the review as:

### Review Result: [APPROVE / REJECT / REQUEST CHANGES]

**Summary**: [2-3 line summary]

**Issues** (if any):
| Severity | File | Location | Description | Fix Suggestion |
|----------|------|----------|-------------|----------------|
| ... | ... | ... | ... | ... |

**Side Effects** (if any):
- [file]: [description]

**Rule Violations** (if any):
- [rule]: [how it was violated]

### Decision Logic
- Any critical issue -> REJECT
- Only warnings -> REQUEST CHANGES
- Only suggestions or clean -> APPROVE

The scope to review: $ARGUMENTS
