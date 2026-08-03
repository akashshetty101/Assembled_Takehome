"""
Rule Evaluator.

This module houses the pure, side-effect-free logic to evaluate rule conditions against 
projected subject state facts at a given time.

Process Flow:
1. Fact Extraction: Iterates through each condition in a rule, fetching the fact value
   from the current subject state via the dynamic registry extractor.
2. Operand Resolution: Resolves the condition's right-hand operand (either a literal value
   or a dynamic reference to another fact on the same subject).
3. Operator Application: Executes comparison operators (e.g., `gt`, `gte`, `in_`) on
   the left-hand and right-hand operands.
4. Outcome Aggregation: Checks if all conditions are met. Captures missing facts and
   builds a snapshot of all referenced facts for template rendering.

Design & Limitations:
- Conjunction Conformance: All conditions are ANDed together (a flat list of conditions).
- Unknown Handling (Missing Facts): If a required fact is uncomputable (resulting in the `MISSING` sentinel),
  the condition evaluates to False. Any missing facts are tracked and reported in `missing_facts`
  so the state machine can freeze rather than resolve (resolve hardening). Full three-valued logic
  using Kleene semantics is slated for a future phase (Phase 10).
"""

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
