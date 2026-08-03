from app.domain.subjects import SubjectType
from app.evaluation.registry import build_registry


def test_build_registry_has_all_eight_queue_facts():
    registry = build_registry()
    assert len(registry.names_for(SubjectType.QUEUE)) == 8


def test_build_registry_has_all_four_agent_facts():
    registry = build_registry()
    assert len(registry.names_for(SubjectType.AGENT)) == 4


def test_build_registry_returns_a_fresh_instance_each_call():
    """Eager and explicit, not a shared mutable global -- callers (tests,
    the API layer) can each build their own without cross-contamination."""
    a = build_registry()
    b = build_registry()
    assert a is not b
    assert a.names_for(SubjectType.QUEUE) == b.names_for(SubjectType.QUEUE)


def test_build_registry_facts_are_independently_extractable():
    """Registry completeness (Phase 2 Tests): every registered fact must be
    extractable from a fully-populated state fixture without raising."""
    from datetime import datetime, timezone

    from app.domain.subjects import AgentState, QueueState

    registry = build_registry()
    now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)

    queue_state = QueueState(
        queue_id="billing", tickets_waiting=1, longest_wait_sec=1, sla_target_sec=120,
        agents_available=1, agents_on_call=1, volume_last_15m=1, volume_forecast_next_15m=1,
        last_event_ts=now,
    )
    for name in registry.names_for(SubjectType.QUEUE):
        registry.get(SubjectType.QUEUE, name).extractor(queue_state, now)

    agent_state = AgentState(
        agent_id="a_11", current_state="on_call", state_entered_at=now,
        violation_active=False, last_event_ts=now,
    )
    for name in registry.names_for(SubjectType.AGENT):
        registry.get(SubjectType.AGENT, name).extractor(agent_state, now)
