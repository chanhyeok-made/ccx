"""
Per-agent YAML configuration loader and writer.
Manages .ccx/agents/{agent_name}.yaml files for agent-specific rules, context,
and disabled base-context rules.
"""

import yaml
from pathlib import Path

VALID_AGENTS = [
    "analyzer",
    "planner",
    "researcher",
    "implementer",
    "reviewer",
    "module-analyzer",
    "package-synthesizer",
]

_AGENTS_DIR = ".ccx/agents"

_DEFAULT_CONFIG = {
    "rules": [],
    "context": "",
    "disabled_rules": [],
}


def _validate_agent(agent_name: str) -> None:
    """Raise ValueError if agent_name is not in VALID_AGENTS."""
    if agent_name not in VALID_AGENTS:
        raise ValueError(
            f"Unknown agent '{agent_name}'. "
            f"Valid agents: {', '.join(VALID_AGENTS)}"
        )


def _agent_config_path(project_dir: str, agent_name: str) -> Path:
    """Return the Path to the agent's YAML config file."""
    return Path(project_dir) / _AGENTS_DIR / f"{agent_name}.yaml"


def get_agent_config(project_dir: str, agent_name: str) -> dict:
    """Load agent-specific config from .ccx/agents/{agent_name}.yaml.

    Returns a dict with keys: agent, rules, context, disabled_rules, exists.
    If the file does not exist, returns defaults with exists=False.
    """
    _validate_agent(agent_name)

    config_path = _agent_config_path(project_dir, agent_name)

    if not config_path.exists():
        return {
            "agent": agent_name,
            "rules": [],
            "context": "",
            "disabled_rules": [],
            "exists": False,
        }

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # Merge with defaults for missing fields
    merged = _DEFAULT_CONFIG.copy()
    merged.update(data)

    return {
        "agent": agent_name,
        "rules": merged["rules"],
        "context": merged["context"],
        "disabled_rules": merged["disabled_rules"],
        "exists": True,
    }


def save_agent_config(
    project_dir: str,
    agent_name: str,
    rules: list[str] | None = None,
    context: str | None = None,
    disabled_rules: list[str] | None = None,
) -> dict:
    """Save or update agent-specific config to .ccx/agents/{agent_name}.yaml.

    Only the provided (non-None) fields are updated; existing values are
    preserved for omitted fields. The agents directory is created if needed.

    Returns {"status": "saved", "agent": agent_name}.
    """
    _validate_agent(agent_name)

    # Load existing config (or defaults)
    current = get_agent_config(project_dir, agent_name)

    # Merge: only override fields that were explicitly passed
    updated = {
        "rules": rules if rules is not None else current["rules"],
        "context": context if context is not None else current["context"],
        "disabled_rules": (
            disabled_rules if disabled_rules is not None else current["disabled_rules"]
        ),
    }

    # Ensure directory exists
    config_path = _agent_config_path(project_dir, agent_name)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(
            updated,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    return {"status": "saved", "agent": agent_name}


def delete_agent_config(project_dir: str, agent_name: str) -> dict:
    """Delete agent-specific config file.

    Returns {"status": "deleted"/"not_found", "agent": agent_name}.
    """
    _validate_agent(agent_name)
    config_path = _agent_config_path(project_dir, agent_name)
    if config_path.exists():
        config_path.unlink()
        return {"status": "deleted", "agent": agent_name}
    return {"status": "not_found", "agent": agent_name}


def list_agent_configs(project_dir: str) -> dict:
    """List all valid agents with their configuration status.

    Returns {"agents": [...], "configured_count": N} where each agent entry
    contains agent name and whether a config file exists.
    """
    agents = []
    configured_count = 0

    for agent_name in VALID_AGENTS:
        config_path = _agent_config_path(project_dir, agent_name)
        exists = config_path.exists()
        if exists:
            configured_count += 1
        agents.append({"agent": agent_name, "exists": exists})

    return {"agents": agents, "configured_count": configured_count}
