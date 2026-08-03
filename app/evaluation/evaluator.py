from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain.facts import MISSING, FactRegistry
from app.domain.rules import FactRef, Rule
from app.evaluation.operators import apply_operator, resolve_operand


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    matched: bool
    facts_snapshot: dict[str, Any]
    missing_facts: list[str]


def evaluate(rule: Rule, state: Any, now: datetime, registry: FactRegistry) -> EvaluationResult:
    """v1: MISSING -> False (TODO(3vl): Phase 10 replaces `all(results)`
    with Kleene AND). facts_snapshot captures every fact this rule
    references, including fact_ref targets, so Phase 5 can render 'why'
    with real numbers."""
    facts_snapshot: dict[str, Any] = {}
    missing_facts: list[str] = []
    results: list[bool] = []

    for condition in rule.conditions:
        spec = registry.get(rule.subject_type, condition.fact)
        left = spec.extractor(state, now)
        facts_snapshot[condition.fact] = left
        if left is MISSING:
            missing_facts.append(condition.fact)

        right = resolve_operand(condition, state, now, registry, rule.subject_type)
        if isinstance(condition.value, FactRef):
            facts_snapshot[condition.value.fact_ref] = right
            if right is MISSING:
                missing_facts.append(condition.value.fact_ref)

        results.append(apply_operator(condition.op, left, right))

    return EvaluationResult(matched=all(results), facts_snapshot=facts_snapshot, missing_facts=missing_facts)
