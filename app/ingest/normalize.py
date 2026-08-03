from enum import Enum


class AgentStateValue(str, Enum):
    AVAILABLE = "available"
    ON_CALL = "on_call"
    ON_BREAK = "on_break"
    IN_MEETING = "in_meeting"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


def normalize_queue_ids(raw: list[str] | None) -> list[str]:
    return raw if raw is not None else []


def normalize_agent_state(raw: str | None) -> AgentStateValue | None:
    """None passes through (an agent's first-ever event has no previous
    state -- distinct from an unrecognized string). Any non-None value not
    in the known vocabulary falls back to UNKNOWN rather than raising."""
    if raw is None:
        return None
    try:
        return AgentStateValue(raw)
    except ValueError:
        return AgentStateValue.UNKNOWN
