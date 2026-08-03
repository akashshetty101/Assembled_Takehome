from datetime import datetime, timezone

import pytest

from app.domain.facts import MISSING
from app.domain.subjects import AgentState, SubjectType, ViolationStartSource
from app.evaluation.facts_agent import AGENT_FACTS

NOW = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)


def _registry_dict():
    return {spec.name: spec for spec in AGENT_FACTS}


def test_exactly_four_agent_facts_registered():
    assert len(AGENT_FACTS) == 4
    assert {s.name for s in AGENT_FACTS} == {
        "current_state", "current_state_duration_sec",
        "in_adherence_violation", "adherence_violation_duration_sec",
    }


def test_all_facts_are_agent_subject_type():
    assert all(spec.subject_type == SubjectType.AGENT for spec in AGENT_FACTS)


def test_current_state_extracts_known_value():
    state = AgentState(agent_id="a_11", current_state="on_call", last_event_ts=NOW)
    assert _registry_dict()["current_state"].extractor(state, NOW) == "on_call"


def test_current_state_missing_before_any_state_change():
    state = AgentState(agent_id="a_11", last_event_ts=NOW)
    assert _registry_dict()["current_state"].extractor(state, NOW) is MISSING


def test_current_state_duration_sec_computed_from_state_entered_at():
    """a_11: on_call since 09:10, evaluated at 09:55 -> 2700s exactly."""
    state = AgentState(
        agent_id="a_11", current_state="on_call",
        state_entered_at=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc),
        last_event_ts=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc),
    )
    now = datetime(2026, 5, 26, 9, 55, tzinfo=timezone.utc)
    assert _registry_dict()["current_state_duration_sec"].extractor(state, now) == 2700.0


def test_current_state_duration_sec_missing_when_never_entered():
    state = AgentState(agent_id="a_11", last_event_ts=NOW)
    assert _registry_dict()["current_state_duration_sec"].extractor(state, NOW) is MISSING


def test_in_adherence_violation_is_a_real_bool_never_missing():
    active = AgentState(agent_id="a_19", violation_active=True, last_event_ts=NOW)
    inactive = AgentState(agent_id="a_19", violation_active=False, last_event_ts=NOW)
    spec = _registry_dict()["in_adherence_violation"]
    assert spec.extractor(active, NOW) is True
    assert spec.extractor(inactive, NOW) is False


def test_adherence_violation_duration_sec_computed_when_known():
    """a_19: violation started 09:35 (event-sourced), evaluated at 09:45:30 -> 630s."""
    state = AgentState(
        agent_id="a_19", violation_active=True,
        violation_started_at=datetime(2026, 5, 26, 9, 35, tzinfo=timezone.utc),
        violation_start_source=ViolationStartSource.EVENT,
        last_event_ts=NOW,
    )
    now = datetime(2026, 5, 26, 9, 45, 30, tzinfo=timezone.utc)
    assert _registry_dict()["adherence_violation_duration_sec"].extractor(state, now) == 630.0


def test_adherence_violation_duration_sec_missing_when_source_unknown():
    """a_23, evt_01HXYZ086: in_violation=true, violation_started_at=null ->
    violation_start_source='unknown'. The fact must be MISSING, not a
    duration computed from a substituted event.ts (that would manufacture a
    number out of nothing -- the exact bug PLAN.md 1.7/R2 exist to prevent)."""
    state = AgentState(
        agent_id="a_23", violation_active=True, violation_started_at=None,
        violation_start_source=ViolationStartSource.UNKNOWN, last_event_ts=NOW,
    )
    assert _registry_dict()["adherence_violation_duration_sec"].extractor(state, NOW) is MISSING


def test_adherence_violation_duration_sec_missing_when_not_in_violation():
    state = AgentState(agent_id="a_19", violation_active=False, last_event_ts=NOW)
    assert _registry_dict()["adherence_violation_duration_sec"].extractor(state, NOW) is MISSING
