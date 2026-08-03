from datetime import datetime, timezone

from app.domain.rules import Rule
from app.domain.subjects import AgentState, QueueState, ViolationStartSource
from app.evaluation.evaluator import evaluate
from app.evaluation.registry import build_registry

REGISTRY = build_registry()
CTX = {"registry": REGISTRY}


def _rule(**overrides) -> Rule:
    data = {
        "name": "n", "subject_type": "queue", "selector": {"kind": "all"},
        "conditions": [{"fact": "tickets_waiting", "op": "gt", "value": 20}],
        "recipient": {"kind": "author"}, "template": "{subject_id}",
        "created_by": "lead_sam",
    }
    data.update(overrides)
    return Rule.model_validate(data, context=CTX)


def test_evaluate_matched_true_when_condition_holds():
    rule = _rule()
    now = datetime(2026, 5, 26, 9, 36, tzinfo=timezone.utc)
    state = QueueState(queue_id="billing", tickets_waiting=22, last_event_ts=now)
    result = evaluate(rule, state, now, REGISTRY)
    assert result.matched is True
    assert result.missing_facts == []


def test_evaluate_matched_false_when_condition_fails():
    rule = _rule()
    now = datetime(2026, 5, 26, 9, 40, tzinfo=timezone.utc)
    state = QueueState(queue_id="billing", tickets_waiting=20, last_event_ts=now)
    result = evaluate(rule, state, now, REGISTRY)
    assert result.matched is False


def test_evaluate_ands_all_conditions():
    rule = _rule(
        subject_type="agent",
        conditions=[
            {"fact": "current_state", "op": "eq", "value": "on_call"},
            {"fact": "current_state_duration_sec", "op": "gt", "value": 2700},
        ],
    )
    now = datetime(2026, 5, 26, 9, 55, 1, tzinfo=timezone.utc)  # 2701s -- past the strict > 2700 boundary
    on_call_long_enough = AgentState(
        agent_id="a_11", current_state="on_call",
        state_entered_at=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc), last_event_ts=now,
    )
    assert evaluate(rule, on_call_long_enough, now, REGISTRY).matched is True

    wrong_state = AgentState(
        agent_id="a_11", current_state="available",
        state_entered_at=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc), last_event_ts=now,
    )
    assert evaluate(rule, wrong_state, now, REGISTRY).matched is False


def test_evaluate_facts_snapshot_includes_fact_ref_target():
    """facts_snapshot captures every fact the rule references, including
    fact_ref targets -- Phase 5 renders 'why' with real numbers."""
    rule = _rule(
        conditions=[{"fact": "longest_wait_sec", "op": "gt", "value": {"fact_ref": "sla_target_sec"}}],
    )
    now = datetime(2026, 5, 26, 9, 30, tzinfo=timezone.utc)
    state = QueueState(queue_id="billing", longest_wait_sec=130, sla_target_sec=120, last_event_ts=now)
    result = evaluate(rule, state, now, REGISTRY)
    assert result.matched is True
    assert result.facts_snapshot["longest_wait_sec"] == 130
    assert result.facts_snapshot["sla_target_sec"] == 120


def test_fact_ref_correctness_billing_vs_tier_2():
    """Identical rule JSON against billing (target 120) and tier_2 (target
    300) yields different results. Proves no hardcoding."""
    rule = _rule(
        conditions=[{"fact": "longest_wait_sec", "op": "gt", "value": {"fact_ref": "sla_target_sec"}}],
    )
    now = datetime(2026, 5, 26, 9, 30, tzinfo=timezone.utc)
    billing = QueueState(queue_id="billing", longest_wait_sec=130, sla_target_sec=120, last_event_ts=now)
    tier_2 = QueueState(queue_id="tier_2", longest_wait_sec=130, sla_target_sec=300, last_event_ts=now)
    assert evaluate(rule, billing, now, REGISTRY).matched is True
    assert evaluate(rule, tier_2, now, REGISTRY).matched is False


def test_missing_fact_ref_target_is_recorded_in_missing_facts():
    """The fact_ref TARGET can itself be MISSING (e.g. sla_target_sec not
    yet populated on a queue) -- must be recorded, not just the left side."""
    rule = _rule(
        conditions=[{"fact": "longest_wait_sec", "op": "gt", "value": {"fact_ref": "sla_target_sec"}}],
    )
    now = datetime(2026, 5, 26, 9, 30, tzinfo=timezone.utc)
    state = QueueState(queue_id="billing", longest_wait_sec=130, sla_target_sec=None, last_event_ts=now)
    result = evaluate(rule, state, now, REGISTRY)
    assert result.matched is False
    assert "sla_target_sec" in result.missing_facts


def test_missing_fact_is_false_and_recorded_todo_3vl():
    """a_23: adherence_violation_duration_sec > 600 -> False AND
    missing_facts == ["adherence_violation_duration_sec"]. Assert
    missing_facts now -- it is what 3VL (Phase 10) branches on."""
    rule = _rule(
        subject_type="agent",
        conditions=[
            {"fact": "in_adherence_violation", "op": "eq", "value": True},
            {"fact": "adherence_violation_duration_sec", "op": "gt", "value": 600},
        ],
    )
    now = datetime(2026, 5, 26, 10, 15, 30, tzinfo=timezone.utc)
    a_23 = AgentState(
        agent_id="a_23", violation_active=True, violation_started_at=None,
        violation_start_source=ViolationStartSource.UNKNOWN, last_event_ts=now,
    )
    result = evaluate(rule, a_23, now, REGISTRY)
    assert result.matched is False
    assert result.missing_facts == ["adherence_violation_duration_sec"]
