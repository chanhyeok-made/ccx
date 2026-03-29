#!/usr/bin/env bash
# ccx hook — send macOS system notifications for Claude Code events.
# Thin wrapper that delegates to notify_macos.py (no external dependencies).
# Must ALWAYS exit 0 to avoid blocking Claude Code.

# Resolve script directory — works whether invoked directly or via plugin root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# If CLAUDE_PLUGIN_ROOT is set, prefer it for reliable path resolution
if [ -n "$CLAUDE_PLUGIN_ROOT" ]; then
    HANDLER="${CLAUDE_PLUGIN_ROOT}/hooks/notify_macos.py"
else
    HANDLER="${SCRIPT_DIR}/notify_macos.py"
fi

# Find python3
if command -v python3 >/dev/null 2>&1; then
    python3 "$HANDLER"
elif command -v python >/dev/null 2>&1; then
    python "$HANDLER"
fi

exit 0
