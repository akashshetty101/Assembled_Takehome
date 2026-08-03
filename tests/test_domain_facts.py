from datetime import datetime, timezone

import pytest

from app.domain.facts import MISSING, FactRegistry, FactSpec
from app.domain.subjects import SubjectType

NOW = datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc)


def test_missing_is_a_singleton_repr():
    from app.domain.facts import MISSING as missing_again
    assert MISSING is missing_again
    assert repr(MISSING) == "MISSING"


def test_missing_has_no_truthiness():
    """Distinct from None and False -- accidental `if value:` on a MISSING
    fact must not silently coerce it. Callers must check `is MISSING`."""
    with pytest.raises(TypeError):
        bool(MISSING)


def test_missing_is_not_none_and_not_false():
    assert MISSING is not None
    assert MISSING is not False


def test_registry_get_returns_none_for_unknown_fact():
    registry = FactRegistry([])
    assert registry.get(SubjectType.QUEUE, "nope") is None


def test_registry_get_and_names_for_round_trip():
    spec = FactSpec("tickets_waiting", SubjectType.QUEUE, "number", lambda state, now: 5)
    registry = FactRegistry([spec])
    assert registry.get(SubjectType.QUEUE, "tickets_waiting") is spec
    assert registry.names_for(SubjectType.QUEUE) == ["tickets_waiting"]
    assert registry.names_for(SubjectType.AGENT) == []


def test_registry_rejects_duplicate_fact_name_for_same_subject_type():
    """Code-review LOW: a copy-paste when adding a fact should fail loudly
    at construction, not silently overwrite the first spec."""
    spec_a = FactSpec("tickets_waiting", SubjectType.QUEUE, "number", lambda s, n: 1)
    spec_b = FactSpec("tickets_waiting", SubjectType.QUEUE, "number", lambda s, n: 2)
    with pytest.raises(ValueError, match="duplicate fact"):
        FactRegistry([spec_a, spec_b])


def test_registry_separates_facts_by_subject_type():
    queue_spec = FactSpec("tickets_waiting", SubjectType.QUEUE, "number", lambda s, n: 1)
    agent_spec = FactSpec("current_state", SubjectType.AGENT, "str", lambda s, n: "on_call")
    registry = FactRegistry([queue_spec, agent_spec])
    assert registry.get(SubjectType.QUEUE, "current_state") is None
    assert registry.get(SubjectType.AGENT, "tickets_waiting") is None


def test_extractor_can_return_missing():
    spec = FactSpec("adherence_violation_duration_sec", SubjectType.AGENT, "number",
                     lambda state, now: MISSING)
    assert spec.extractor(None, NOW) is MISSING
