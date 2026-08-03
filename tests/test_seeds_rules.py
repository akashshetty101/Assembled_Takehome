import json
from pathlib import Path

from app.domain.rules import Rule
from app.evaluation.registry import build_registry

SEEDS_PATH = Path(__file__).resolve().parent.parent / "seeds" / "rules.json"


def _load() -> list[Rule]:
    registry = build_registry()
    raw = json.loads(SEEDS_PATH.read_text())
    return [Rule.model_validate(r, context={"registry": registry}) for r in raw]


def test_seeds_file_exists():
    assert SEEDS_PATH.exists()


def test_all_seed_rules_validate_with_zero_new_code():
    """Zero-new-code claim: loading real rule data through the same Rule
    model used everywhere else requires no special-casing. 7 rules: the
    original 5 plus two added post-v1 -- "queue understaffed with tickets
    waiting" (Tier A: existing facts, seed-only) and "queue at risk of SLA
    breach" (Tier B: one new derived fact, sla_risk_pct)."""
    rules = _load()
    assert len(rules) == 7


def test_understaffed_queue_rule_present():
    rules = _load()
    rule = next(r for r in rules if r.name == "queue understaffed with tickets waiting")
    fact_names = {c.fact for c in rule.conditions}
    assert fact_names == {"agents_available", "tickets_waiting"}


def test_at_risk_rule_uses_sla_risk_pct_band_not_hardcoded_breach():
    """The at-risk rule brackets [0.8, 1.0) so it stops firing once the
    queue actually breaches -- that's a distinct rule ("queue breaching
    SLA") and this rule shouldn't double-notify for the same incident."""
    rules = _load()
    rule = next(r for r in rules if r.name == "queue at risk of SLA breach")
    fact_names = {c.fact for c in rule.conditions}
    assert fact_names == {"sla_risk_pct"}
    ops = {c.op.value for c in rule.conditions}
    assert ops == {"gte", "lt"}


def test_three_spec_examples_are_among_the_seeds():
    rules = _load()
    names = {r.name for r in rules}
    assert "agent on a single call > 45 min" in names
    assert "out of adherence > 10 min" in names
    assert "queue breaching SLA" in names


def test_channel_recipient_rule_present():
    """Proves the third routing arm exists in the seed set."""
    rules = _load()
    channel_rules = [r for r in rules if r.recipient.kind.value == "channel"]
    assert len(channel_rules) == 1
    assert channel_rules[0].recipient.target


def test_sla_rule_uses_fact_ref_not_hardcoded_target():
    from app.domain.rules import FactRef

    rules = _load()
    sla_rule = next(r for r in rules if r.name == "queue breaching SLA")
    assert sla_rule.selector.kind == "all"  # applies to every queue, same rule
    assert isinstance(sla_rule.conditions[0].value, FactRef)
    assert sla_rule.conditions[0].value.fact_ref == "sla_target_sec"


def test_no_snapshot_age_sec_rule_seeded():
    """R15: snapshot_age_sec's value depends on where the replay clock
    stops, which would make the golden fixture sensitive to end-of-replay
    timing rather than to the data. Deliberately excluded from seeds."""
    rules = _load()
    for rule in rules:
        fact_names = {c.fact for c in rule.conditions}
        assert "snapshot_age_sec" not in fact_names
