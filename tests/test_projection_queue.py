from datetime import datetime, timezone

from app.domain.events import parse_event
from app.projection.queue import apply_snapshot


def _snapshot(**overrides):
    raw = {
        "event_id": "evt_1", "ts": "2026-05-26T09:00:00Z", "type": "queue_snapshot",
        "queue_id": "billing", "tickets_waiting": 0, "longest_wait_sec": 0,
        "sla_target_sec": 120, "agents_available": 0, "agents_on_call": 0,
        "volume_last_15m": 6, "volume_forecast_next_15m": 22,
    }
    raw.update(overrides)
    return parse_event(raw)


def test_apply_snapshot_from_no_prior_state():
    event = _snapshot()
    state = apply_snapshot(None, event)
    assert state.queue_id == "billing"
    assert state.tickets_waiting == 0
    assert state.last_event_ts == datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc)


def test_apply_snapshot_replaces_prior_state_entirely():
    """A queue snapshot fully replaces state -- it is not a partial update."""
    event1 = _snapshot(tickets_waiting=18, longest_wait_sec=100)
    prev = apply_snapshot(None, event1)

    event2 = _snapshot(
        event_id="evt_2", ts="2026-05-26T09:36:00Z", tickets_waiting=22, longest_wait_sec=130,
    )
    new_state = apply_snapshot(prev, event2)
    assert new_state.tickets_waiting == 22
    assert new_state.longest_wait_sec == 130
    assert new_state.last_event_ts == datetime(2026, 5, 26, 9, 36, tzinfo=timezone.utc)


def test_apply_snapshot_null_forecast_does_not_raise():
    event = _snapshot(volume_forecast_next_15m=None)
    state = apply_snapshot(None, event)
    assert state.volume_forecast_next_15m is None
