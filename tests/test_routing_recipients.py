import pytest
from pydantic import ValidationError

from app.domain.rules import Rule
from app.evaluation.registry import build_registry
from app.routing.recipients import resolve_recipient

REGISTRY = build_registry()
CTX = {"registry": REGISTRY}


def _rule(**overrides) -> Rule:
    data = {
        "name": "n", "subject_type": "agent", "selector": {"kind": "all"},
        "conditions": [{"fact": "current_state", "op": "eq", "value": "on_call"}],
        "recipient": {"kind": "author"}, "template": "{subject_id}",
        "created_by": "lead_sam",
    }
    data.update(overrides)
    return Rule.model_validate(data, context=CTX)


def test_author_recipient_resolves_to_created_by():
    rule = _rule(recipient={"kind": "author"})
    ref = resolve_recipient(rule, subject_id="a_11")
    assert ref.kind == "author"
    assert ref.target == "lead_sam"


def test_subject_agent_recipient_resolves_to_the_subject_itself():
    rule = _rule(recipient={"kind": "subject_agent"})
    ref = resolve_recipient(rule, subject_id="a_19")
    assert ref.kind == "subject_agent"
    assert ref.target == "a_19"


def test_channel_recipient_resolves_to_configured_target():
    rule = _rule(recipient={"kind": "channel", "target": "#ops-alerts"})
    ref = resolve_recipient(rule, subject_id="billing")
    assert ref.kind == "channel"
    assert ref.target == "#ops-alerts"
