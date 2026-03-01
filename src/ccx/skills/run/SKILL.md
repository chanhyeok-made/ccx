---
name: run
description: "Full development pipeline: analyze -> plan -> implement -> review -> commit"
disable-model-invocation: true
argument-hint: "[request description]"
allowed-tools: Bash, Agent, TaskCreate, TaskUpdate, TaskList, TaskGet, AskUserQuestion, mcp__ccx__record_execution, mcp__ccx__invalidate_analysis_cache
---

# Full Development Pipeline

You are a **pure orchestrator**. You coordinate subagents but do NOT read files or load project context yourself.

Read the PIPELINE.md file in this skill's directory for detailed instructions, then execute the pipeline.

## Quick Reference

1. **Analyze** (subagent): parse request, load context via MCP, return structured analysis
2. **Plan** (subagent): decompose into tasks, load context via MCP
3. **Execute**: per task — Research (Explore subagent) → Implement (general-purpose subagent) → Review (general-purpose subagent)
4. **Commit & Push**: git commit + push
5. **Record**: MCP `record_execution`

Checkpoints: confirm with user after Analyze and Plan.

The user's request is: $ARGUMENTS
