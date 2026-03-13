"""
Context compaction logic for Claude Code sessions.
Monitors context window fill level, extracts key information from
transcripts, and saves compaction summaries for continuity.

Storage layout:
    .ccx/compaction-summary.json
"""

import json
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

from ccx._transcript_utils import parse_assistant_messages
from ccx.context_tracker import parse_context_usage
from ccx.session import get_context_summary
from ccx.storage import resolve_storage_dir

_CCX_DIR = ".ccx"
_COMPACTION_FILE = "compaction-summary.json"

# Default threshold: trigger compaction when context fill exceeds 50 %
_DEFAULT_THRESHOLD = 0.5

# Model -> context window size mapping (tokens).
# Values represent the maximum input context window for each model.
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-4-5-20250514": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-opus-4-0": 200_000,
    "claude-sonnet-4-0": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "claude-3-opus-20240229": 200_000,
    "claude-3-sonnet-20240229": 200_000,
    "claude-3-haiku-20240307": 200_000,
}
_DEFAULT_CONTEXT_WINDOW = 200_000

# Tool names whose file_path parameter indicates a file change
_FILE_CHANGE_TOOLS = {"Edit", "Write", "NotebookEdit"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CompactionSummary:
    """Summary of key session information for context compaction."""

    timestamp: str
    context_fill_pct: float
    model: str
    summary: str
    changed_files: list[str] = field(default_factory=list)
    key_decisions: list[str] = field(default_factory=list)
    pending_tasks: list[str] = field(default_factory=list)
    session_id: str = ""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _compaction_path(project_dir: str | Path) -> Path:
    """Return .ccx/compaction-summary.json path."""
    return Path(resolve_storage_dir(str(project_dir))) / _CCX_DIR / _COMPACTION_FILE


def _ensure_dir(project_dir: str | Path) -> None:
    """Create .ccx directory if needed."""
    (Path(resolve_storage_dir(str(project_dir))) / _CCX_DIR).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Context fill calculation
# ---------------------------------------------------------------------------

def _get_context_window(model: str) -> int:
    """Return the context window size for a model name.

    Tries exact match first, then prefix match for versioned model names.
    Falls back to ``_DEFAULT_CONTEXT_WINDOW``.
    """
    if model in _MODEL_CONTEXT_WINDOWS:
        return _MODEL_CONTEXT_WINDOWS[model]

    # Prefix match: "claude-sonnet-4-5-20250514" -> "claude-sonnet-4-5"
    for key, value in _MODEL_CONTEXT_WINDOWS.items():
        if model.startswith(key):
            return value

    return _DEFAULT_CONTEXT_WINDOW


def _extract_model(entries: list[dict]) -> str:
    """Extract the model name from deduplicated assistant entries.

    Takes the model from the last entry (most recent), falling back to
    ``"unknown"`` if not available.
    """
    for entry in reversed(entries):
        model = entry.get("message", {}).get("model", "")
        if model:
            return model
    return "unknown"


def check_context_fill(transcript_path: Path | str) -> tuple[float, str]:
    """Return the current context fill ratio and model name from a transcript.

    The fill ratio is computed as ``final_context_fill / context_window``,
    where ``final_context_fill`` comes from the last assistant turn and
    ``context_window`` is looked up from the model name.

    Returns ``(0.0, "unknown")`` when the transcript is empty or unreadable.
    """
    entries = parse_assistant_messages(str(transcript_path))
    if not entries:
        return 0.0, "unknown"

    model = _extract_model(entries)
    context_window = _get_context_window(model)

    # Use parse_context_usage to get the final fill value
    usage = parse_context_usage(str(transcript_path))
    if usage.final_context_fill == 0:
        return 0.0, model

    fill_pct = usage.final_context_fill / context_window
    return fill_pct, model


# ---------------------------------------------------------------------------
# Key information extraction
# ---------------------------------------------------------------------------

def _parse_all_entries(transcript_path: Path | str) -> list[dict]:
    """Parse all JSONL entries from a transcript (not just assistant).

    Returns an empty list if the file does not exist or cannot be read.
    """
    path = Path(transcript_path)
    if not path.exists():
        return []

    entries: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []

    return entries


def get_recent_conversation_text(
    transcript_path: Path | str,
    max_chars: int = 50_000,
) -> str:
    """Extract recent conversation text from a transcript JSONL file.

    Collects ``text`` content blocks from *assistant* and *user* entries
    in reverse chronological order until *max_chars* is reached, then
    returns them re-ordered chronologically.

    Returns an empty string if the transcript is empty or unreadable.
    """
    entries = _parse_all_entries(transcript_path)
    if not entries:
        return ""

    # Walk backwards, collecting text blocks until we hit the char budget.
    fragments: list[str] = []
    total = 0

    for entry in reversed(entries):
        entry_type = entry.get("type", "")
        if entry_type not in ("assistant", "user"):
            continue

        content = entry.get("message", {}).get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue

        entry_texts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    entry_texts.append(text)
            elif isinstance(block, str):
                entry_texts.append(block)

        if not entry_texts:
            continue

        combined = "\n".join(entry_texts)
        if total + len(combined) > max_chars:
            remaining = max_chars - total
            if remaining > 0:
                fragments.append(combined[:remaining])
                total += remaining
            break

        fragments.append(combined)
        total += len(combined)

    if not fragments:
        return ""

    # Reverse to restore chronological order.
    fragments.reverse()
    return "\n\n".join(fragments)


def _extract_changed_files(entries: list[dict]) -> list[str]:
    """Extract unique file paths from tool_use content blocks.

    Scans assistant message content blocks for ``tool_use`` invocations
    of file-modifying tools (Edit, Write, NotebookEdit) and collects
    their ``file_path`` input parameters.
    """
    files: dict[str, None] = {}  # ordered set

    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            tool_name = block.get("name", "")
            if tool_name not in _FILE_CHANGE_TOOLS:
                continue
            inp = block.get("input", {})
            file_path = inp.get("file_path", "")
            if file_path:
                files[file_path] = None

    return list(files.keys())


def _extract_key_decisions(entries: list[dict]) -> list[str]:
    """Extract user decisions from tool_result blocks.

    Looks for ``tool_result`` content blocks following ``AskUserQuestion``
    tool uses.  The user's response text is captured as a key decision.
    """
    # First pass: collect tool_use_ids for AskUserQuestion invocations
    ask_ids: set[str] = set()
    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
                tool_id = block.get("id", "")
                if tool_id:
                    ask_ids.add(tool_id)

    if not ask_ids:
        return []

    # Second pass: find tool_result responses in "user" entries
    decisions: list[str] = []
    for entry in entries:
        if entry.get("type") != "user":
            continue
        content = entry.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            if block.get("tool_use_id", "") not in ask_ids:
                continue
            # Extract the text from nested content
            inner = block.get("content", "")
            if isinstance(inner, str) and inner.strip():
                decisions.append(inner.strip())
            elif isinstance(inner, list):
                for sub in inner:
                    if isinstance(sub, dict) and sub.get("type") == "text":
                        text = sub.get("text", "").strip()
                        if text:
                            decisions.append(text)

    return decisions


def _extract_pending_tasks(entries: list[dict], tail: int = 5) -> list[str]:
    """Extract pending/incomplete task mentions from recent assistant messages.

    Scans the last *tail* assistant text blocks for lines containing
    task-like patterns (TODO, FIXME, pending, remaining, next step, etc.).
    """
    _TASK_PATTERN = re.compile(
        r"(?:TODO|FIXME|HACK|XXX|pending|remaining|next step|need to|"
        r"still need|not yet|incomplete|to do)",
        re.IGNORECASE,
    )

    # Collect recent assistant text blocks
    texts: list[str] = []
    for entry in reversed(entries):
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    texts.append(text)
        if len(texts) >= tail:
            break

    # Extract matching lines
    tasks: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for line in text.splitlines():
            line = line.strip()
            if not line or len(line) < 10:
                continue
            if _TASK_PATTERN.search(line):
                # Normalize: strip list markers
                cleaned = re.sub(r"^[-*]\s*", "", line).strip()
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    tasks.append(cleaned)

    return tasks


def extract_key_info(
    transcript_path: Path | str,
    project_dir: Path | str,
    session_id: str = "",
) -> CompactionSummary:
    """Extract key information from a transcript into a CompactionSummary.

    Combines transcript analysis with session context to produce a summary
    suitable for restoring context after compaction.
    """
    transcript_path = Path(transcript_path)
    project_dir_str = str(project_dir)

    # Context fill and model
    fill_pct, model = check_context_fill(transcript_path)

    # Parse all entries for content extraction
    all_entries = _parse_all_entries(transcript_path)

    # Extract structured information
    changed_files = _extract_changed_files(all_entries)
    key_decisions = _extract_key_decisions(all_entries)
    pending_tasks = _extract_pending_tasks(all_entries)

    # Build summary text from session context
    context_summary = get_context_summary(project_dir_str)
    summary_parts: list[str] = []
    if context_summary:
        summary_parts.append(context_summary)
    if changed_files:
        summary_parts.append(f"Changed files: {', '.join(changed_files[:10])}")
    if pending_tasks:
        summary_parts.append(f"Pending: {'; '.join(pending_tasks[:5])}")

    summary = "\n".join(summary_parts) if summary_parts else "No context available."

    return CompactionSummary(
        timestamp=datetime.now(timezone.utc).isoformat(),
        context_fill_pct=round(fill_pct, 4),
        model=model,
        summary=summary,
        changed_files=changed_files,
        key_decisions=key_decisions,
        pending_tasks=pending_tasks,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Threshold check
# ---------------------------------------------------------------------------

def should_compact(
    transcript_path: Path | str,
    threshold: float = _DEFAULT_THRESHOLD,
) -> bool:
    """Return ``True`` if the context fill ratio exceeds *threshold*."""
    fill_pct, _ = check_context_fill(transcript_path)
    return fill_pct > threshold


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_compaction_summary(
    summary: CompactionSummary,
    project_dir: Path | str,
) -> Path:
    """Save a CompactionSummary to ``.ccx/compaction-summary.json``.

    Overwrites any existing file.  Returns the path to the saved file.
    """
    _ensure_dir(project_dir)
    path = _compaction_path(project_dir)
    path.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_compaction_summary(project_dir: Path | str) -> CompactionSummary | None:
    """Load the most recent CompactionSummary from disk.

    Returns ``None`` if the file does not exist or cannot be parsed.
    """
    path = _compaction_path(project_dir)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    try:
        return CompactionSummary(**data)
    except TypeError:
        return None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_compaction(
    transcript_path: Path | str,
    project_dir: Path | str,
    threshold: float = _DEFAULT_THRESHOLD,
    session_id: str = "",
) -> CompactionSummary | None:
    """Run the full compaction pipeline: check -> extract -> save.

    Returns the saved :class:`CompactionSummary` if the context fill ratio
    exceeds *threshold*, or ``None`` if compaction is not needed.
    """
    if not should_compact(transcript_path, threshold):
        return None

    summary = extract_key_info(transcript_path, project_dir, session_id)
    save_compaction_summary(summary, project_dir)
    return summary
