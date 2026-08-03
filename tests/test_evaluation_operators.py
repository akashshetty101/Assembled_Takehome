from datetime import datetime, timezone

import pytest

from app.domain.facts import MISSING
from app.domain.rules import Condition, FactRef, Operator
from app.domain.subjects import QueueState, SubjectType
from app.evaluation.operators import apply_operator, resolve_operand
from app.evaluation.registry import build_registry

NOW = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
REGISTRY = build_registry()


@pytest.mark.parametrize(
    "op,left,right,expected",
    [
        (Operator.GT, 5, 3, True), (Operator.GT, 3, 5, False),
        (Operator.GTE, 5, 5, True), (Operator.GTE, 4, 5, False),
        (Operator.LT, 3, 5, True), (Operator.LT, 5, 3, False),
        (Operator.LTE, 5, 5, True), (Operator.LTE, 6, 5, False),
        (Operator.EQ, "on_call", "on_call", True), (Operator.EQ, "a", "b", False),
        (Operator.NEQ, "a", "b", True), (Operator.NEQ, "a", "a", False),
        (Operator.IN, "on_break", ["on_break", "offline"], True),
        (Operator.IN, "on_call", ["on_break", "offline"], False),
    ],
)
def test_apply_operator_table(op, left, right, expected):
    assert apply_operator(op, left, right) is expected


@pytest.mark.parametrize("op", list(Operator))
def test_apply_operator_missing_left_collapses_to_false(op):
    """v1 unknown-as-false (TODO(3vl)): MISSING on either operand -> False,
    never raises, never magically becomes True."""
    assert apply_operator(op, MISSING, 5) is False


@pytest.mark.parametrize("op", list(Operator))
def test_apply_operator_missing_right_collapses_to_false(op):
    assert apply_operator(op, 5, MISSING) is False


def test_apply_operator_boundary_exactly_2700():
    """a_11/a_23: current_state_duration_sec > 2700 at exactly 2700 -- must
    be False. Pin >, not >=, per PLAN.md 1.6/R8."""
    assert apply_operator(Operator.GT, 2700, 2700) is False
    assert apply_operator(Operator.GT, 2701, 2700) is True


def test_apply_operator_boundary_exactly_600():
    """a_19: adherence_violation_duration_sec > 600 at exactly 600 -> False."""
    assert apply_operator(Operator.GT, 600, 600) is False
    assert apply_operator(Operator.GT, 601, 600) is True


def test_resolve_operand_literal_passes_through():
    condition = Condition(fact="tickets_waiting", op=Operator.GT, value=20)
    result = resolve_operand(condition, state=None, now=NOW, registry=REGISTRY,
                              subject_type=SubjectType.QUEUE)
    assert result == 20


def test_resolve_operand_fact_ref_looks_up_same_subjects_state():
    """fact_ref correctness: identical condition shape, different queue
    state -> different resolved value. Proves no hardcoding."""
    condition = Condition(
        fact="longest_wait_sec", op=Operator.GT, value=FactRef(fact_ref="sla_target_sec")
    )
    billing = QueueState(queue_id="billing", sla_target_sec=120, last_event_ts=NOW)
    tier_2 = QueueState(queue_id="tier_2", sla_target_sec=300, last_event_ts=NOW)

    billing_target = resolve_operand(condition, billing, NOW, REGISTRY, SubjectType.QUEUE)
    tier_2_target = resolve_operand(condition, tier_2, NOW, REGISTRY, SubjectType.QUEUE)

    assert billing_target == 120
    assert tier_2_target == 300
