from datetime import datetime, timezone

from app.domain.subjects import AgentState, QueueState, ViolationStartSource
from app.storage.sqlite.subject_state import (
    SqliteAgentStateRepository,
    SqliteQueueStateRepository,
)

_TS = datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc)


def test_queue_state_put_then_get_round_trips(db_conn):
    repo = SqliteQueueStateRepository(db_conn)
    state = QueueState(
        queue_id="billing", tickets_waiting=5, longest_wait_sec=60, sla_target_sec=120,
        agents_available=3, agents_on_call=2, volume_last_15m=10, volume_forecast_next_15m=15,
        last_event_ts=_TS,
    )
    repo.put(state)
    assert repo.get("billing") == state


def test_queue_state_put_upserts_on_conflict(db_conn):
    repo = SqliteQueueStateRepository(db_conn)
    repo.put(QueueState(queue_id="billing", tickets_waiting=5, last_event_ts=_TS))
    updated = QueueState(
        queue_id="billing", tickets_waiting=9, longest_wait_sec=90,
        last_event_ts=datetime(2026, 5, 26, 9, 30, tzinfo=timezone.utc),
    )
    repo.put(updated)
    assert repo.get("billing") == updated
    assert repo.count() == 1


def test_agent_state_round_trips_with_bool_and_null_fields(db_conn):
    repo = SqliteAgentStateRepository(db_conn)
    state = AgentState(
        agent_id="a_23", current_state="on_call",
        state_entered_at=datetime(2026, 5, 26, 9, 45, tzinfo=timezone.utc),
        queue_ids=["billing"], violation_active=True, violation_started_at=None,
        violation_start_source=ViolationStartSource.UNKNOWN,
        last_event_ts=datetime(2026, 5, 26, 10, 15, 30, tzinfo=timezone.utc),
    )
    repo.put(state)
    assert repo.get("a_23") == state


def test_cardinality_is_one_row_per_subject(db_conn):
    """The scale story (PLAN.md 1.3): projection cardinality is O(subjects)."""
    queue_repo = SqliteQueueStateRepository(db_conn)
    for qid in ("billing", "tier_2", "vip"):
        queue_repo.put(QueueState(queue_id=qid, last_event_ts=_TS))
    assert queue_repo.count() == 3


def test_queue_state_list_returns_every_row(db_conn):
    queue_repo = SqliteQueueStateRepository(db_conn)
    queue_repo.put(QueueState(queue_id="billing", last_event_ts=_TS))
    queue_repo.put(QueueState(queue_id="vip", last_event_ts=_TS))
    assert {s.queue_id for s in queue_repo.list()} == {"billing", "vip"}


def test_agent_state_list_and_count(db_conn):
    agent_repo = SqliteAgentStateRepository(db_conn)
    agent_repo.put(AgentState(agent_id="a_31", current_state="available", last_event_ts=_TS))
    agent_repo.put(AgentState(agent_id="a_23", current_state="on_call", last_event_ts=_TS))
    assert agent_repo.count() == 2
    assert {s.agent_id for s in agent_repo.list()} == {"a_31", "a_23"}
