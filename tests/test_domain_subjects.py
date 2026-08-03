from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domain.events import parse_event
from app.domain.subjects import (
    AgentState,
    QueueState,
    SubjectRef,
    SubjectType,
    ViolationStartSource,
    subject_ref_for_event,
)


def test_queue_state_is_frozen():
    state = QueueState(queue_id="billing", last_event_ts=datetime(2026, 5, 26, 9, tzinfo=timezone.utc))
    with pytest.raises(ValidationError):
        state.tickets_waiting = 5


def test_agent_state_is_frozen():
    state = AgentState(agent_id="a_31", last_event_ts=datetime(2026, 5, 26, 9, tzinfo=timezone.utc))
    with pytest.raises(ValidationError):
        state.current_state = "on_call"


def test_agent_state_defaults():
    state = AgentState(agent_id="a_31", last_event_ts=datetime(2026, 5, 26, 9, tzinfo=timezone.utc))
    assert state.current_state is None
    assert state.queue_ids == []
    assert state.violation_active is False
    assert state.violation_started_at is None
    assert state.violation_start_source == ViolationStartSource.UNKNOWN


def test_subject_ref_for_queue_snapshot():
    raw = {
        "event_id": "evt_1", "ts": "2026-05-26T09:00:00Z", "type": "queue_snapshot",
        "queue_id": "billing", "tickets_waiting": 0, "longest_wait_sec": 0,
        "sla_target_sec": 120, "agents_available": 0, "agents_on_call": 0,
        "volume_last_15m": 0, "volume_forecast_next_15m": 0,
    }
    ref = subject_ref_for_event(parse_event(raw))
    assert ref == SubjectRef(subject_type=SubjectType.QUEUE, subject_id="billing")


def test_subject_ref_for_agent_state_change():
    raw = {
        "event_id": "evt_2", "ts": "2026-05-26T09:00:00Z", "type": "agent_state_change",
        "agent_id": "a_31", "queue_ids": None, "previous_state": None,
        "previous_state_duration_sec": None, "new_state": "available",
    }
    ref = subject_ref_for_event(parse_event(raw))
    assert ref == SubjectRef(subject_type=SubjectType.AGENT, subject_id="a_31")


def test_subject_ref_for_adherence_check():
    raw = {
        "event_id": "evt_3", "ts": "2026-05-26T09:00:00Z", "type": "adherence_check",
        "agent_id": "a_19", "queue_ids": None, "scheduled_state": "available",
        "actual_state": "available", "in_violation": False, "violation_started_at": None,
    }
    ref = subject_ref_for_event(parse_event(raw))
    assert ref == SubjectRef(subject_type=SubjectType.AGENT, subject_id="a_19")
