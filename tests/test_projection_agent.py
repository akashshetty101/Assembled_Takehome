from datetime import datetime, timezone

from app.domain.events import parse_event
from app.domain.subjects import AgentState, ViolationStartSource
from app.ingest.normalize import AgentStateValue
from app.projection.agent import apply_adherence, apply_state_change, state_disagreement


def _state_change(**overrides):
    raw = {
        "event_id": "evt_1", "ts": "2026-05-26T09:00:00Z", "type": "agent_state_change",
        "agent_id": "a_31", "queue_ids": ["billing"], "previous_state": None,
        "previous_state_duration_sec": None, "new_state": "available",
    }
    raw.update(overrides)
    return parse_event(raw)


def _adherence(**overrides):
    raw = {
        "event_id": "evt_2", "ts": "2026-05-26T09:35:00Z", "type": "adherence_check",
        "agent_id": "a_19", "queue_ids": ["billing"], "scheduled_state": "available",
        "actual_state": "in_meeting", "in_violation": True,
        "violation_started_at": "2026-05-26T09:35:00Z",
    }
    raw.update(overrides)
    return parse_event(raw)


# -- apply_state_change ------------------------------------------------------

def test_apply_state_change_sets_state_entered_at_to_event_ts_on_first_event():
    event = _state_change(ts="2026-05-26T09:10:00Z", new_state="on_call")
    state = apply_state_change(None, event)
    assert state.current_state == "on_call"
    assert state.state_entered_at == datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc)


def test_apply_state_change_state_entered_at_is_event_ts_not_derived_from_previous_duration():
    """R6 regression: a_11's exit event (previous_state='on_call',
    previous_state_duration_sec=4200, at 10:20) must set the NEW state's
    state_entered_at to 10:20 (event.ts), never to event.ts - 4200s or any
    other function of previous_state_duration_sec. That number describes the
    state being LEFT, not the one being entered."""
    prior = apply_state_change(
        None, _state_change(ts="2026-05-26T09:10:00Z", new_state="on_call")
    )
    exit_event = _state_change(
        event_id="evt_089",
        ts="2026-05-26T10:20:00Z",
        previous_state="on_call",
        previous_state_duration_sec=4200,
        new_state="available",
    )
    new_state = apply_state_change(prior, exit_event)
    assert new_state.state_entered_at == datetime(2026, 5, 26, 10, 20, tzinfo=timezone.utc)
    assert new_state.current_state == "available"


def test_apply_state_change_normalizes_unrecognized_new_state():
    event = _state_change(new_state="doing_a_backflip")
    state = apply_state_change(None, event)
    assert state.current_state == AgentStateValue.UNKNOWN


def test_apply_state_change_updates_queue_ids_normalizing_null_to_empty_list():
    event = _state_change(queue_ids=None)
    state = apply_state_change(None, event)
    assert state.queue_ids == []


def test_apply_state_change_preserves_prior_violation_fields():
    """A state-change event carries no adherence info; it must not clobber
    violation_active/violation_started_at set by a prior adherence_check."""
    prior = apply_adherence(None, _adherence())
    assert prior.violation_active is True

    moved = apply_state_change(
        prior, _state_change(agent_id="a_19", ts="2026-05-26T09:40:00Z", new_state="on_call")
    )
    assert moved.violation_active is True
    assert moved.violation_started_at == prior.violation_started_at


# -- apply_adherence ----------------------------------------------------------

def test_apply_adherence_records_violation_started_at_from_event():
    state = apply_adherence(None, _adherence())
    assert state.violation_active is True
    assert state.violation_started_at == datetime(2026, 5, 26, 9, 35, tzinfo=timezone.utc)
    assert state.violation_start_source == ViolationStartSource.EVENT


def test_apply_adherence_unknown_start_when_violation_true_but_started_at_null():
    """evt_01HXYZ086: a_23, in_violation=true, violation_started_at=null.
    Must NOT substitute event.ts -- that manufactures a duration out of
    nothing (the tempting bug PLAN.md calls out explicitly)."""
    event = _adherence(
        event_id="evt_086", agent_id="a_23", ts="2026-05-26T10:15:30Z",
        violation_started_at=None,
    )
    state = apply_adherence(None, event)
    assert state.violation_active is True
    assert state.violation_started_at is None
    assert state.violation_start_source == ViolationStartSource.UNKNOWN


def test_apply_adherence_falling_edge_clears_violation_fields():
    prior = apply_adherence(None, _adherence())
    cleared = apply_adherence(
        prior,
        _adherence(
            event_id="evt_076", ts="2026-05-26T10:10:30Z",
            actual_state="available", in_violation=False, violation_started_at=None,
        ),
    )
    assert cleared.violation_active is False
    assert cleared.violation_started_at is None
    assert cleared.violation_start_source == ViolationStartSource.UNKNOWN


def test_apply_adherence_never_mutates_current_state():
    """R7: adherence_check.actual_state feeds adherence facts only. Only
    agent_state_change is authoritative for current_state."""
    after_state_change = apply_state_change(
        None, _state_change(agent_id="a_23", ts="2026-05-26T09:45:00Z", new_state="on_call")
    )
    assert after_state_change.current_state == "on_call"

    after_adherence = apply_adherence(
        after_state_change,
        _adherence(agent_id="a_23", ts="2026-05-26T10:15:30Z", actual_state="in_meeting"),
    )
    assert after_adherence.current_state == "on_call"


# -- state_disagreement (R7 pure detection helper) ----------------------------

def test_state_disagreement_true_when_actual_differs_from_current_state():
    """The exact R7 scenario: a_23's last agent_state_change (09:45) said
    on_call; evt_01HXYZ086 (10:15:30) reports actual_state=in_meeting with no
    transition event in between."""
    prior = apply_state_change(
        None, _state_change(agent_id="a_23", ts="2026-05-26T09:45:00Z", new_state="on_call")
    )
    event = _adherence(agent_id="a_23", ts="2026-05-26T10:15:30Z", actual_state="in_meeting")
    assert state_disagreement(prior, event) is True


def test_state_disagreement_false_when_actual_matches_current_state():
    prior = apply_state_change(
        None, _state_change(agent_id="a_19", ts="2026-05-26T09:00:00Z", new_state="available")
    )
    event = _adherence(agent_id="a_19", actual_state="available", in_violation=False,
                        violation_started_at=None)
    assert state_disagreement(prior, event) is False


def test_state_disagreement_false_when_no_prior_state():
    event = _adherence(agent_id="a_99")
    assert state_disagreement(None, event) is False
    no_state_yet = AgentState(agent_id="a_99", last_event_ts=datetime(2026, 5, 26, 9, tzinfo=timezone.utc))
    assert state_disagreement(no_state_yet, event) is False
