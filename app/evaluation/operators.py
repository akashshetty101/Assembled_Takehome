from datetime import datetime
from typing import Any

from app.domain.facts import MISSING, FactRegistry
from app.domain.rules import Condition, FactRef, Operator
from app.domain.subjects import SubjectType

_OPS = {
    Operator.GT: lambda a, b: a > b,
    Operator.GTE: lambda a, b: a >= b,
    Operator.LT: lambda a, b: a < b,
    Operator.LTE: lambda a, b: a <= b,
    Operator.EQ: lambda a, b: a == b,
    Operator.NEQ: lambda a, b: a != b,
    Operator.IN: lambda a, b: a in b,
}


def resolve_operand(
    condition: Condition,
    state: Any,
    now: datetime,
    registry: FactRegistry,
    subject_type: SubjectType,
) -> Any:
    """Literal values pass through unchanged; a fact_ref does a second
    registry lookup against the SAME subject's state -- e.g. sla_target_sec
    read from whichever queue is currently being evaluated, not hardcoded."""
    if isinstance(condition.value, FactRef):
        spec = registry.get(subject_type, condition.value.fact_ref)
        return spec.extractor(state, now)
    return condition.value


def apply_operator(op: Operator, left: Any, right: Any) -> bool:
    """v1 unknown-as-false: MISSING on either operand collapses to False.
    TODO(3vl): Phase 10 replaces this with Kleene semantics."""
    if left is MISSING or right is MISSING:
        return False
    return _OPS[op](left, right)
