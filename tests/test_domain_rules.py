from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.rules import Condition, FactRef, Operator, Recipient, Rule
from app.domain.subjects import SubjectType
from app.evaluation.registry import build_registry

REGISTRY = build_registry()
CTX = {"registry": REGISTRY}


def _validate(data: dict) -> Rule:
    return Rule.model_validate(data, context=CTX)


# -- R1: architectural boundary ------------------------------------------

def test_domain_rules_does_not_import_evaluation():
    """R1: nothing should make domain/rules.py import evaluation/ -- the
    registry must be injected via context, never imported as a global."""
    source = Path("app/domain/rules.py").read_text()
    assert "app.evaluation" not in source
    assert "import evaluation" not in source


def test_rule_validation_without_registry_context_raises_clear_error():
    """R1's other half: no silent fallback to a module-level registry."""
    data = {
        "name": "n", "subject_type": "queue", "selector": {"kind": "all"},
        "conditions": [{"fact": "tickets_waiting", "op": "gt", "value": 20}],
        "recipient": {"kind": "author"}, "template": "{tickets_waiting}",
        "created_by": "lead_sam",
    }
    with pytest.raises(ValidationError, match="registry"):
        Rule.model_validate(data)  # no context at all


# -- Valid construction, the three spec examples --------------------------

def test_agent_on_call_45min_rule_validates():
    rule = _validate({
        "name": "agent on a single call > 45 min",
        "subject_type": "agent",
        "selector": {"kind": "all"},
        "conditions": [
            {"fact": "current_state", "op": "eq", "value": "on_call"},
            {"fact": "current_state_duration_sec", "op": "gt", "value": 2700},
        ],
        "for_duration_sec": 0,
        "recipient": {"kind": "author"},
        "template": "{subject_id} has been on a single call for {current_state_duration_sec}s",
        "created_by": "lead_sam",
    })
    assert rule.subject_type == SubjectType.AGENT
    assert len(rule.conditions) == 2


def test_out_of_adherence_10min_rule_validates():
    rule = _validate({
        "name": "out of adherence > 10 min",
        "subject_type": "agent",
        "selector": {"kind": "all"},
        "conditions": [
            {"fact": "in_adherence_violation", "op": "eq", "value": True},
            {"fact": "adherence_violation_duration_sec", "op": "gt", "value": 600},
        ],
        "cooldown_sec": 600,
        "recipient": {"kind": "subject_agent"},
        "template": "{subject_id} has been out of adherence for {adherence_violation_duration_sec}s",
        "created_by": "lead_sam",
    })
    assert rule.recipient.kind.value == "subject_agent"


def test_queue_breaching_sla_rule_with_fact_ref_validates():
    rule = _validate({
        "name": "queue breaching SLA",
        "subject_type": "queue",
        "selector": {"kind": "all"},
        "conditions": [
            {"fact": "longest_wait_sec", "op": "gt", "value": {"fact_ref": "sla_target_sec"}},
        ],
        "for_duration_sec": 300,
        "recipient": {"kind": "author"},
        "template": "{subject_id} breaching SLA: {longest_wait_sec} > {sla_target_sec}",
        "created_by": "lead_sam",
    })
    assert isinstance(rule.conditions[0].value, FactRef)


def test_channel_recipient_rule_validates():
    rule = _validate({
        "name": "queue severely backed up",
        "subject_type": "queue",
        "selector": {"kind": "all"},
        "conditions": [{"fact": "tickets_waiting", "op": "gt", "value": 15}],
        "recipient": {"kind": "channel", "target": "#ops-alerts"},
        "template": "{subject_id} has {tickets_waiting} tickets waiting",
        "created_by": "lead_sam",
    })
    assert rule.recipient.target == "#ops-alerts"


def test_queue_membership_selector_validates():
    rule = _validate({
        "name": "any of my agents on a call",
        "subject_type": "agent",
        "selector": {"kind": "queue_membership", "queue_ids": ["billing"]},
        "conditions": [{"fact": "current_state", "op": "eq", "value": "on_call"}],
        "recipient": {"kind": "author"},
        "template": "{subject_id}: {current_state}",
        "created_by": "lead_sam",
    })
    assert rule.selector.kind == "queue_membership"


def test_ids_selector_validates():
    rule = _validate({
        "name": "billing only",
        "subject_type": "queue",
        "selector": {"kind": "ids", "ids": ["billing"]},
        "conditions": [{"fact": "tickets_waiting", "op": "gt", "value": 20}],
        "recipient": {"kind": "author"}, "template": "{subject_id}",
        "created_by": "lead_sam",
    })
    assert rule.selector.ids == ["billing"]


def test_in_operator_with_list_value_validates():
    rule = _validate({
        "name": "state in list",
        "subject_type": "agent",
        "selector": {"kind": "all"},
        "conditions": [{"fact": "current_state", "op": "in_", "value": ["on_break", "offline"]}],
        "recipient": {"kind": "author"}, "template": "{subject_id}",
        "created_by": "lead_sam",
    })
    assert rule.conditions[0].op == Operator.IN


# -- Rejections (Phase 2 Tests section, one per case) ---------------------

def _base(**overrides):
    data = {
        "name": "n", "subject_type": "queue", "selector": {"kind": "all"},
        "conditions": [{"fact": "tickets_waiting", "op": "gt", "value": 20}],
        "recipient": {"kind": "author"}, "template": "{subject_id}",
        "created_by": "lead_sam",
    }
    data.update(overrides)
    return data


def test_unknown_fact_for_subject_type_rejected():
    data = _base(conditions=[{"fact": "not_a_real_fact", "op": "gt", "value": 1}])
    with pytest.raises(ValidationError, match="unknown fact"):
        _validate(data)


def test_agent_fact_on_queue_rule_rejected():
    data = _base(subject_type="queue",
                  conditions=[{"fact": "current_state", "op": "eq", "value": "on_call"}])
    with pytest.raises(ValidationError, match="unknown fact"):
        _validate(data)


def test_cross_subject_fact_ref_rejected():
    """fact_ref must resolve within the SAME subject_type as the rule."""
    data = _base(
        subject_type="queue",
        conditions=[{"fact": "tickets_waiting", "op": "gt", "value": {"fact_ref": "current_state_duration_sec"}}],
    )
    with pytest.raises(ValidationError, match="fact_ref"):
        _validate(data)


def test_type_mismatched_operand_rejected():
    """No `current_state > 5` -- current_state is str-typed."""
    data = _base(
        subject_type="agent",
        conditions=[{"fact": "current_state", "op": "gt", "value": 5}],
    )
    with pytest.raises(ValidationError, match="incompatible"):
        _validate(data)


def test_subject_agent_recipient_on_queue_rule_rejected():
    data = _base(subject_type="queue", recipient={"kind": "subject_agent"})
    with pytest.raises(ValidationError, match="subject_agent"):
        _validate(data)


def test_channel_recipient_without_target_rejected():
    data = _base(recipient={"kind": "channel"})
    with pytest.raises(ValidationError, match="channel"):
        _validate(data)


def test_empty_conditions_rejected():
    data = _base(conditions=[])
    with pytest.raises(ValidationError):
        _validate(data)


def test_negative_for_duration_rejected():
    data = _base(for_duration_sec=-1)
    with pytest.raises(ValidationError):
        _validate(data)


def test_negative_cooldown_rejected():
    data = _base(cooldown_sec=-1)
    with pytest.raises(ValidationError):
        _validate(data)


def test_unknown_operator_rejected():
    data = _base(conditions=[{"fact": "tickets_waiting", "op": "bogus_op", "value": 1}])
    with pytest.raises(ValidationError):
        _validate(data)


def test_template_referencing_unregistered_fact_rejected():
    data = _base(template="{subject_id} saw {not_a_declared_fact}")
    with pytest.raises(ValidationError, match="template"):
        _validate(data)


def test_template_referencing_fact_not_in_this_rules_conditions_rejected():
    """A real, registered fact -- just not one THIS rule declared. Prevents
    a rule that would KeyError at render time even though the fact exists."""
    data = _base(template="{subject_id} on call for {current_state_duration_sec}s")
    with pytest.raises(ValidationError, match="template"):
        _validate(data)


def test_in_operator_requires_list_value():
    data = _base(conditions=[{"fact": "tickets_waiting", "op": "in_", "value": 20}])
    with pytest.raises(ValidationError, match="list"):
        _validate(data)


def test_in_operator_rejects_type_incompatible_list_element():
    data = _base(
        subject_type="agent",
        conditions=[{"fact": "current_state", "op": "in_", "value": ["on_call", 5]}],
    )
    with pytest.raises(ValidationError, match="incompatible"):
        _validate(data)


def test_fact_ref_value_type_mismatch_rejected():
    """sla_target_sec (number) vs current_state (str) -- same AGENT subject
    won't have sla_target_sec at all, so use two same-subject facts with
    different value_types instead, on a synthetic pairing that only differs
    by type: in_adherence_violation (bool) referenced against
    current_state_duration_sec (number)."""
    data = _base(
        subject_type="agent",
        conditions=[
            {"fact": "current_state_duration_sec", "op": "gt",
             "value": {"fact_ref": "in_adherence_violation"}},
        ],
    )
    with pytest.raises(ValidationError, match="value_type"):
        _validate(data)


def test_malformed_template_format_string_rejected():
    data = _base(template="{subject_id} unclosed {")
    with pytest.raises(ValidationError, match="template"):
        _validate(data)


def test_template_auto_numbering_placeholder_rejected():
    """Code-review HIGH: string.Formatter().parse() returns field_name=""
    (falsy) for an auto-numbering placeholder like '{}', so an `if
    field_name` filter would silently let it through validation -- then
    `template.format(**values)` (keyword-args only) raises IndexError at
    delivery time, since '{}' expects a positional arg. Must be rejected
    here, the same as any other unknown field."""
    data = _base(template="{subject_id} has {} tickets waiting")
    with pytest.raises(ValidationError, match="template"):
        _validate(data)


def test_in_operator_with_fact_ref_value_rejected():
    """Code-review HIGH: fact_ref targets are always scalar, so op 'in_'
    combined with a fact_ref must be rejected at validation time -- not
    left to crash (TypeError: 'int' is not iterable) or silently do
    substring matching (str `in` str) at evaluate() time."""
    data = _base(
        conditions=[{"fact": "tickets_waiting", "op": "in_", "value": {"fact_ref": "sla_target_sec"}}],
    )
    with pytest.raises(ValidationError, match="in_"):
        _validate(data)


def test_bool_literal_rejected_against_number_fact():
    """Code-review LOW: explicit regression for the named risk (PLAN.md
    1.6's 'no current_state > 5') on its bool/number sibling -- Python's
    isinstance(True, int) is True, so this must not silently pass."""
    data = _base(conditions=[{"fact": "tickets_waiting", "op": "gt", "value": True}])
    with pytest.raises(ValidationError, match="incompatible"):
        _validate(data)


def test_frozen_rule_cannot_be_mutated():
    rule = _validate(_base())
    with pytest.raises(ValidationError):
        rule.enabled = False


def test_extra_field_rejected():
    data = _base()
    data["not_a_real_field"] = True
    with pytest.raises(ValidationError):
        _validate(data)
