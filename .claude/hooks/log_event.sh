#!/usr/bin/env bash
# ccx hook — log all Claude Code events to .ccx/logs/ in JSONL format.
# Thin wrapper that delegates to log_event.py (no external dependencies).
# Must ALWAYS exit 0 to avoid blocking Claude Code.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HANDLER="${SCRIPT_DIR}/log_event.py"

# Find python3
if command -v python3 >/dev/null 2>&1; then
    python3 "$HANDLER"
elif command -v python >/dev/null 2>&1; then
    python "$HANDLER"
fi

exit 0
