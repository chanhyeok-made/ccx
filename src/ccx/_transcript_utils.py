"""Shared transcript parsing utilities for token_tracker and context_tracker.

Provides deduplication, agent info inference, and assistant message extraction
from Claude Code transcript JSONL files.
"""

import json
from pathlib import Path


def _deduplicate_messages(entries: list[dict]) -> list[dict]:
    """Deduplicate streaming assistant messages by message ID.

    Claude Code transcripts emit multiple JSONL lines for the same message
    during streaming.  For each unique message ID, keep the entry whose
    ``stop_reason`` is not ``None``.  If no such entry exists, keep the one
    with the highest ``output_tokens``.
    """
    by_id: dict[str, dict] = {}

    for entry in entries:
        msg = entry.get("message", {})
        msg_id = msg.get("id", "")
        if not msg_id:
            continue

        existing = by_id.get(msg_id)
        if existing is None:
            by_id[msg_id] = entry
            continue

        existing_stop = existing.get("message", {}).get("stop_reason")
        current_stop = msg.get("stop_reason")

        if current_stop is not None and existing_stop is None:
            by_id[msg_id] = entry
        elif current_stop is None and existing_stop is not None:
            pass  # keep existing
        else:
            # Both have stop_reason or both lack it -- pick higher output_tokens
            existing_out = existing.get("message", {}).get("usage", {}).get("output_tokens", 0)
            current_out = msg.get("usage", {}).get("output_tokens", 0)
            if current_out > existing_out:
                by_id[msg_id] = entry

    return list(by_id.values())


def infer_agent_info(path: Path) -> tuple[str, str]:
    """Infer agent_id and agent_type from a transcript file path.

    Main transcript:      ``{session_id}.jsonl``  -> ("main", "main")
    Subagent transcript:  ``subagents/agent-{id}.jsonl``
                          -> ("agent-{id}", type from .meta.json or "unknown")
    """
    name = path.stem

    if path.parent.name == "subagents" and name.startswith("agent-"):
        agent_id = name
        meta_path = path.with_suffix(".meta.json")
        agent_type = "unknown"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                agent_type = meta.get("agentType", "unknown")
            except (json.JSONDecodeError, OSError):
                pass
        return agent_id, agent_type

    return "main", "main"


def parse_assistant_messages(transcript_path: str) -> list[dict]:
    """Parse a transcript JSONL file and return deduplicated assistant entries.

    Reads the file at *transcript_path*, keeps only ``type == "assistant"``
    lines that carry a ``message.usage`` object, deduplicates streaming
    entries, and returns the resulting list.

    Returns an empty list if the file does not exist or cannot be read.
    """
    path = Path(transcript_path)
    if not path.exists():
        return []

    assistant_entries: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                msg = entry.get("message", {})
                if not msg.get("usage"):
                    continue
                assistant_entries.append(entry)
    except OSError:
        return []

    return _deduplicate_messages(assistant_entries)
