# Agent Orchestrator — Setup & Usage

## Prerequisites

1. **Python 3.11+**
2. **Claude Code CLI** installed and authenticated
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```
3. **Anthropic API key** set as environment variable
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```

## Install

```bash
cd agent-orchestrator
pip install -r requirements.txt
```

## Setup Base Context

1. Copy the example to your project root:
   ```bash
   cp base-context.example.yaml /path/to/your/project/base-context.yaml
   ```

2. Edit it — remember the principle: **일반적인 것은 빼고, 예외만 넣어라**

## Usage

### Basic
```bash
python main.py "유저 프로필에 최근 활동 내역 추가해줘" -p /path/to/project
```

### Dry Run (plan only, no code changes)
```bash
python main.py "인증 로직 리팩토링" -p /path/to/project --dry-run
```

### Verbose (see all agent outputs)
```bash
python main.py "API 에러 핸들링 통합" -p /path/to/project --verbose
```

### Custom model
```bash
python main.py "검색 기능 추가" -p /path/to/project --model claude-opus-4-20250514
```

### Interactive mode
```bash
python main.py -p /path/to/project
# Then type your request
```

## Pipeline Flow

```
$ python main.py "유저 프로필에 최근 활동 내역 추가해줘" -p ~/my-project

🚀 Starting pipeline for: "유저 프로필에 최근 활동 내역 추가해줘"

──────────────────────────────────────────────────
  [1/6] 🤖 Analyzer: Parsing request...
──────────────────────────────────────────────────

❓ Clarification needed:

  1. 최근 활동의 범위는? [default: 최근 20건]
     > (enter to use default)

──────────────────────────────────────────────────
  [2/6] 🤖 Planner: Decomposing tasks...
──────────────────────────────────────────────────

📦 Execution group 1/2: ["T1", "T2"]

──────────────────────────────────────────────────
  [3/6] 🤖 Researcher: [T1] Finding relevant files...
──────────────────────────────────────────────────

──────────────────────────────────────────────────
  [4/6] 🤖 Implementer: [T1] Writing code...
──────────────────────────────────────────────────

──────────────────────────────────────────────────
  [5/6] 🤖 Reviewer: [T1] Reviewing changes...
──────────────────────────────────────────────────

  ... (repeats for T2, then T3)

──────────────────────────────────────────────────
  [6/6] 🤖 Committer: Generating commit message...
──────────────────────────────────────────────────

============================================================
✅ Done!

Activity 모델 신규 추가 (User 1:N 관계).
최근 활동 조회 서비스 함수 및 UI 컴포넌트 작성.
```

## Architecture

```
main.py                     ← CLI entry point
orchestrator.py             ← Pipeline controller
config.py                   ← Base context loader
agents/
  base.py                   ← APIAgent + ClaudeCodeAgent base classes
  analyzer.py               ← API call (lightweight)
  planner.py                ← API call (lightweight)
  researcher.py             ← Claude Code subprocess (read-only)
  implementer.py            ← Claude Code subprocess (read/write)
  reviewer.py               ← API call (lightweight)
  committer.py              ← API call (lightweight)
base-context.example.yaml   ← Template
```

## Cost Optimization

| Agent | Backend | Context Used | Est. Cost |
|-------|---------|-------------|-----------|
| Analyzer | API (Sonnet) | ~500 tokens | $0.002 |
| Planner | API (Sonnet) | ~1K tokens | $0.004 |
| Researcher | Claude Code | Variable | CLI pricing |
| Implementer | Claude Code | Minimized by Researcher | CLI pricing |
| Reviewer | API (Sonnet) | ~2K tokens | $0.008 |
| Committer | API (Sonnet) | ~500 tokens | $0.002 |

The key savings come from Researcher minimizing what Implementer needs.

## Customization

### Swap Researcher to v2 (Python-native file access)

In `orchestrator.py`, replace:
```python
self.researcher = ResearcherAgent(project_dir=project_dir, verbose=verbose)
```
with:
```python
self.researcher = ResearcherAgentV2(project_dir=project_dir, model=model, verbose=verbose)
```

Implement `ResearcherAgentV2` that:
1. Uses Python's pathlib/subprocess to read files and grep
2. Sends collected context to Anthropic API
3. Returns same `AgentResult` schema

### Add retry loop for Reviewer rejections

In `orchestrator.py` → `_execute_task()`, the TODO comment marks where to add retry logic.

### Change models per agent

Modify `Orchestrator.__init__()` to pass different models:
```python
self.analyzer = AnalyzerAgent(model="claude-haiku-4-5-20251001")  # cheap
self.reviewer = ReviewerAgent(model="claude-opus-4-20250514")     # thorough
```
