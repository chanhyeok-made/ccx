#!/usr/bin/env bash
# ccx hook — log all Claude Code events to .ccx/logs/ in JSONL format.
# Reads hook JSON from stdin, appends a structured entry to {session_id}.jsonl.
# Must ALWAYS exit 0 to avoid blocking Claude Code.

main() {
    # Read full stdin into variable
    local input
    input="$(cat)" || true

    # Bail if empty or jq not available
    [[ -z "$input" ]] && exit 0
    command -v jq >/dev/null 2>&1 || exit 0

    # Extract key fields
    local hook_event session_id tool_name cwd
    hook_event="$(echo "$input" | jq -r '.hook_event_name // empty')" || true
    session_id="$(echo "$input" | jq -r '.session_id // empty')" || true
    tool_name="$(echo "$input" | jq -r '.tool_name // empty')" || true
    cwd="$(echo "$input" | jq -r '.cwd // empty')" || true

    # Need at minimum an event name and session id
    [[ -z "$hook_event" || -z "$session_id" ]] && exit 0

    # Resolve log directory: prefer CLAUDE_PROJECT_DIR, fall back to cwd
    local project_dir="${CLAUDE_PROJECT_DIR:-$cwd}"
    [[ -z "$project_dir" ]] && exit 0

    local log_dir="${project_dir}/.ccx/logs"
    mkdir -p "$log_dir" 2>/dev/null || exit 0

    local log_file="${log_dir}/${session_id}.jsonl"
    local timestamp
    timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

    # Build the JSONL entry based on event type
    local entry
    case "$hook_event" in
        PreToolUse)
            entry="$(echo "$input" | jq -c \
                --arg ts "$timestamp" \
                '{
                    timestamp: $ts,
                    session_id: .session_id,
                    event: .hook_event_name,
                    tool_name: .tool_name,
                    tool_input: (.tool_input | tostring | .[0:2000])
                }')" || true
            ;;
        PostToolUse)
            entry="$(echo "$input" | jq -c \
                --arg ts "$timestamp" \
                '{
                    timestamp: $ts,
                    session_id: .session_id,
                    event: .hook_event_name,
                    tool_name: .tool_name,
                    tool_input: (.tool_input | tostring | .[0:2000]),
                    tool_response: (.tool_response | tostring | .[0:2000]),
                    duration_ms: .duration_ms
                }')" || true
            ;;
        PostToolUseFailure)
            entry="$(echo "$input" | jq -c \
                --arg ts "$timestamp" \
                '{
                    timestamp: $ts,
                    session_id: .session_id,
                    event: .hook_event_name,
                    tool_name: .tool_name,
                    tool_input: (.tool_input | tostring | .[0:2000]),
                    error: .error,
                    duration_ms: .duration_ms
                }')" || true
            ;;
        UserPromptSubmit)
            entry="$(echo "$input" | jq -c \
                --arg ts "$timestamp" \
                '{
                    timestamp: $ts,
                    session_id: .session_id,
                    event: .hook_event_name,
                    prompt: .prompt
                }')" || true
            ;;
        *)
            # Generic fallback — capture everything
            entry="$(echo "$input" | jq -c \
                --arg ts "$timestamp" \
                '. + {timestamp: $ts, event: .hook_event_name}')" || true
            ;;
    esac

    # Append to log file
    [[ -n "$entry" ]] && echo "$entry" >> "$log_file" 2>/dev/null || true
}

main "$@"
exit 0
