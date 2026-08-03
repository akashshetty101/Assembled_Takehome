from datetime import datetime

from app.domain.facts import MISSING, FactSpec
from app.domain.subjects import AgentState, SubjectType, ViolationStartSource


def _current_state(state: AgentState, now: datetime):
    return state.current_state if state.current_state is not None else MISSING


def _current_state_duration_sec(state: AgentState, now: datetime):
    if state.state_entered_at is None:
        return MISSING
    return (now - state.state_entered_at).total_seconds()


def _in_adherence_violation(state: AgentState, now: datetime):
    return state.violation_active


def _adherence_violation_duration_sec(state: AgentState, now: datetime):
    """MISSING whenever violation_start_source is 'unknown' -- never falls
    back to event.ts or any other substitute (R2/§1.7): the unknowable
    duration case (a_23, evt_01HXYZ086) must stay unknowable, not become a
    manufactured number."""
    if state.violation_start_source == ViolationStartSource.UNKNOWN or state.violation_started_at is None:
        return MISSING
    return (now - state.violation_started_at).total_seconds()


AGENT_FACTS: list[FactSpec] = [
    FactSpec("current_state", SubjectType.AGENT, "str", _current_state),
    FactSpec("current_state_duration_sec", SubjectType.AGENT, "number", _current_state_duration_sec),
    FactSpec("in_adherence_violation", SubjectType.AGENT, "bool", _in_adherence_violation),
    FactSpec(
        "adherence_violation_duration_sec", SubjectType.AGENT, "number",
        _adherence_violation_duration_sec,
    ),
]
