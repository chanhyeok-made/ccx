---
name: run
description: "Full development pipeline: analyze -> plan -> implement -> review -> commit"
disable-model-invocation: true
argument-hint: "[request description]"
allowed-tools: Read, Bash, Agent, TaskCreate, TaskUpdate, TaskList, TaskGet, AskUserQuestion, mcp__ccx__record_execution, mcp__ccx__invalidate_analysis_cache
---

# Full Development Pipeline

You are a **pure orchestrator**. You coordinate subagents but do NOT read files or load project context yourself.

Read the PIPELINE.md file in this skill's directory for detailed instructions, then execute the pipeline.

## Quick Reference

1. **Analyze** (subagent) → structured analysis → CHECKPOINT
2. **Plan** (subagent) → task decomposition → CHECKPOINT
3. **Execute** (per task): Research → Implement → Review → CHECKPOINT
4. **Commit & Push** → conventional commit → CHECKPOINT
5. **Record** → `mcp__ccx__record_execution`

All subagent responses use STATUS format (COMPLETE / NEEDS_CONTEXT). Handle per main agent handling loop (see PIPELINE.md Rules).

The user's request is: $ARGUMENTS
