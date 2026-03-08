#!/usr/bin/env bash
# ccx hook — validate subagent output against required schema markers.
# Thin wrapper that delegates to validate_schema.py (no external dependencies).
# Exit code is propagated from the Python script (always 0 by design).

# Resolve script directory — works whether invoked directly or via plugin root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# If CLAUDE_PLUGIN_ROOT is set, prefer it for reliable path resolution
if [ -n "$CLAUDE_PLUGIN_ROOT" ]; then
    HANDLER="${CLAUDE_PLUGIN_ROOT}/hooks/validate_schema.py"
else
    HANDLER="${SCRIPT_DIR}/validate_schema.py"
fi

# Find python3
if command -v python3 >/dev/null 2>&1; then
    python3 "$HANDLER"
elif command -v python >/dev/null 2>&1; then
    python "$HANDLER"
fi

# Always exit 0 — hook failures must never block Claude Code
exit 0
