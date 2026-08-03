from datetime import datetime, timezone

import pytest

from app.domain.facts import MISSING
from app.domain.subjects import QueueState, SubjectType
from app.evaluation.facts_queue import QUEUE_FACTS

NOW = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)

FULLY_POPULATED = QueueState(
    queue_id="billing",
    tickets_waiting=14,
    longest_wait_sec=200,
    sla_target_sec=120,
    agents_available=1,
    agents_on_call=3,
    volume_last_15m=30,
    volume_forecast_next_15m=None,  # billing 10:00, the real null-forecast case
    last_event_ts=datetime(2026, 5, 26, 9, 55, tzinfo=timezone.utc),
)


def _registry_dict():
    return {spec.name: spec for spec in QUEUE_FACTS}


def test_exactly_eight_queue_facts_registered():
    assert len(QUEUE_FACTS) == 8
    assert {spec.name for spec in QUEUE_FACTS} == {
        "tickets_waiting", "longest_wait_sec", "sla_target_sec", "agents_available",
        "agents_on_call", "volume_last_15m", "volume_forecast_next_15m", "snapshot_age_sec",
    }


def test_all_facts_are_queue_subject_type():
    assert all(spec.subject_type == SubjectType.QUEUE for spec in QUEUE_FACTS)


@pytest.mark.parametrize(
    "fact_name,expected",
    [
        ("tickets_waiting", 14),
        ("longest_wait_sec", 200),
        ("sla_target_sec", 120),
        ("agents_available", 1),
        ("agents_on_call", 3),
        ("volume_last_15m", 30),
    ],
)
def test_facts_extract_from_fully_populated_state(fact_name, expected):
    spec = _registry_dict()[fact_name]
    assert spec.extractor(FULLY_POPULATED, NOW) == expected


def test_null_forecast_extracts_as_missing():
    """billing 10:00, evt_01HXYZ068: volume_forecast_next_15m is null in the
    real sample data -- an untrusted input, correctly MISSING not None/0."""
    spec = _registry_dict()["volume_forecast_next_15m"]
    assert spec.extractor(FULLY_POPULATED, NOW) is MISSING


def test_snapshot_age_sec_is_now_minus_last_event_ts():
    """R14: this fact changes with no new event arriving -- it is a
    time-derived fact on a QUEUE, easy to forget when Phase 4's due_time.py
    decides whether a queue rule is time-sensitive."""
    spec = _registry_dict()["snapshot_age_sec"]
    assert spec.extractor(FULLY_POPULATED, NOW) == 300.0  # 09:55 -> 10:00


def test_snapshot_age_sec_is_zero_at_the_instant_of_the_snapshot():
    spec = _registry_dict()["snapshot_age_sec"]
    assert spec.extractor(FULLY_POPULATED, FULLY_POPULATED.last_event_ts) == 0.0


@pytest.mark.parametrize(
    "fact_name",
    ["tickets_waiting", "longest_wait_sec", "sla_target_sec", "agents_available",
     "agents_on_call", "volume_last_15m"],
)
def test_null_field_extracts_as_missing_for_every_nullable_fact(fact_name):
    state = QueueState(queue_id="billing", last_event_ts=NOW)  # all optional fields None
    spec = _registry_dict()[fact_name]
    assert spec.extractor(state, NOW) is MISSING
