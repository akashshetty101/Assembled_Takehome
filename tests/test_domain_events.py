from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domain.events import (
    AdherenceCheckEvent,
    AgentStateChangeEvent,
    QueueSnapshotEvent,
    parse_event,
)


def test_parses_queue_snapshot():
    raw = {
        "event_id": "evt_01HXYZ001",
        "ts": "2026-05-26T09:00:00Z",
        "type": "queue_snapshot",
        "queue_id": "billing",
        "tickets_waiting": 0,
        "longest_wait_sec": 0,
        "sla_target_sec": 120,
        "agents_available": 0,
        "agents_on_call": 0,
        "volume_last_15m": 6,
        "volume_forecast_next_15m": 22,
    }
    event = parse_event(raw)
    assert isinstance(event, QueueSnapshotEvent)
    assert event.queue_id == "billing"
    assert event.ts == datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc)


def test_parses_agent_state_change_with_null_previous_state():
    raw = {
        "event_id": "evt_01HXYZ004",
        "ts": "2026-05-26T09:00:00Z",
        "type": "agent_state_change",
        "agent_id": "a_31",
        "queue_ids": ["billing", "vip"],
        "previous_state": None,
        "previous_state_duration_sec": None,
        "new_state": "available",
    }
    event = parse_event(raw)
    assert isinstance(event, AgentStateChangeEvent)
    assert event.previous_state is None
    assert event.new_state == "available"


def test_parses_adherence_check_with_violation_started_at():
    raw = {
        "event_id": "evt_01HXYZ050",
        "ts": "2026-05-26T09:35:00Z",
        "type": "adherence_check",
        "agent_id": "a_19",
        "queue_ids": ["billing"],
        "scheduled_state": "available",
        "actual_state": "in_meeting",
        "in_violation": True,
        "violation_started_at": "2026-05-26T09:35:00Z",
    }
    event = parse_event(raw)
    assert isinstance(event, AdherenceCheckEvent)
    assert event.in_violation is True
    assert event.violation_started_at == datetime(2026, 5, 26, 9, 35, tzinfo=timezone.utc)


def test_parses_adherence_check_with_null_violation_started_at():
    """evt_01HXYZ086: in_violation true, violation_started_at null -- the
    unknowable-duration case (unknown-as-false / MISSING territory, Phase 2)."""
    raw = {
        "event_id": "evt_01HXYZ086",
        "ts": "2026-05-26T10:15:30Z",
        "type": "adherence_check",
        "agent_id": "a_23",
        "queue_ids": ["tier_2"],
        "scheduled_state": "available",
        "actual_state": "in_meeting",
        "in_violation": True,
        "violation_started_at": None,
    }
    event = parse_event(raw)
    assert event.violation_started_at is None
    assert event.in_violation is True


def test_queue_ids_null_is_preserved_as_none_at_this_layer():
    """Normalization (None -> []) is ingest/normalize.py's job, not events.py's."""
    raw = {
        "event_id": "evt_01HXYZ073",
        "ts": "2026-05-26T09:15:00Z",
        "type": "agent_state_change",
        "agent_id": "a_05",
        "queue_ids": None,
        "previous_state": "available",
        "previous_state_duration_sec": 100,
        "new_state": "on_break",
    }
    event = parse_event(raw)
    assert event.queue_ids is None


def test_unknown_type_raises_validation_error_not_crash():
    raw = {
        "event_id": "evt_bad",
        "ts": "2026-05-26T09:00:00Z",
        "type": "unknown_event_type",
    }
    with pytest.raises(ValidationError):
        parse_event(raw)


def test_missing_required_field_raises_validation_error():
    raw = {"event_id": "evt_bad", "type": "queue_snapshot"}
    with pytest.raises(ValidationError):
        parse_event(raw)


def test_ts_must_be_tz_aware_utc():
    raw = {
        "event_id": "evt_naive",
        "ts": "2026-05-26T09:00:00",  # no offset
        "type": "queue_snapshot",
        "queue_id": "billing",
        "tickets_waiting": 0,
        "longest_wait_sec": 0,
        "sla_target_sec": 120,
        "agents_available": 0,
        "agents_on_call": 0,
        "volume_last_15m": 0,
        "volume_forecast_next_15m": 0,
    }
    with pytest.raises(ValidationError):
        parse_event(raw)


def test_unexpected_field_is_rejected_not_silently_dropped():
    """A typo'd field name (e.g. volume_forecast_next15m) must not silently
    fall back to the real field's default -- extra='forbid' turns it into a
    counted validation failure instead."""
    raw = {
        "event_id": "evt_typo", "ts": "2026-05-26T09:00:00Z", "type": "queue_snapshot",
        "queue_id": "billing", "tickets_waiting": 0, "longest_wait_sec": 0,
        "sla_target_sec": 120, "agents_available": 0, "agents_on_call": 0,
        "volume_last_15m": 0, "volume_forecast_next15m": 22,  # typo: missing underscore
    }
    with pytest.raises(ValidationError):
        parse_event(raw)


def test_null_volume_forecast_parses_without_error():
    """Billing 10:00 snapshot: volume_forecast_next_15m is null -- must not crash."""
    raw = {
        "event_id": "evt_01HXYZ068",
        "ts": "2026-05-26T10:00:00Z",
        "type": "queue_snapshot",
        "queue_id": "billing",
        "tickets_waiting": 5,
        "longest_wait_sec": 260,
        "sla_target_sec": 120,
        "agents_available": 2,
        "agents_on_call": 3,
        "volume_last_15m": 40,
        "volume_forecast_next_15m": None,
    }
    event = parse_event(raw)
    assert event.volume_forecast_next_15m is None
