---
name: run
description: "Full development pipeline: analyze -> plan -> implement -> review -> commit"
disable-model-invocation: true
argument-hint: "[request description]"
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, Agent, TaskCreate, TaskUpdate, TaskList, TaskGet, AskUserQuestion, mcp__ccx__load_project_context, mcp__ccx__check_rules, mcp__ccx__get_session, mcp__ccx__record_execution
---

# Full Development Pipeline

You are executing the ccx development pipeline. Follow each phase sequentially.
Read the detailed instructions in `PIPELINE.md` (located in the same directory as this skill).

## Quick Reference

1. **Load Context**: MCP `load_project_context` + `get_session`
2. **Analyze**: Parse user request into structured requirements
3. **Plan**: Decompose into executable tasks with TaskCreate
4. **Execute**: For each task: Research (Agent Explore) -> Implement (Agent general-purpose) -> Review (inline + check_rules)
5. **Commit**: Generate conventional commit message
6. **Record**: MCP `record_execution`

## Instructions

Read the PIPELINE.md file in this skill's directory for the full detailed pipeline instructions, then execute the pipeline for the user's request.

The user's request is: $ARGUMENTS
