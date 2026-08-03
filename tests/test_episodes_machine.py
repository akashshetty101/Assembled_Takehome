from datetime import datetime, timedelta, timezone

import pytest

from app.domain.episodes import Episode, EpisodeState, Transition
from app.domain.rules import Rule
from app.episodes.machine import advance
from app.evaluation.registry import build_registry

REGISTRY = build_registry()
CTX = {"registry": REGISTRY}
T0 = datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc)


def _rule(**overrides) -> Rule:
    data = {
        "name": "n", "subject_type": "queue", "selector": {"kind": "all"},
        "conditions": [{"fact": "tickets_waiting", "op": "gt", "value": 20}],
        "recipient": {"kind": "author"}, "template": "{subject_id}",
        "created_by": "lead_sam",
    }
    data.update(overrides)
    return Rule.model_validate(data, context=CTX)


def _ep(state: EpisodeState, **overrides) -> Episode:
    data = {"id": "ep1", "rule_id": "r1", "subject_id": "billing", "state": state}
    data.update(overrides)
    return Episode(**data)


def _t(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


# -- Exhaustive transition table, one test per row ---------------------------

def test_clear_false_stays_clear_no_emit():
    rule = _rule()
    ep = _ep(EpisodeState.CLEAR)
    new_ep, transitions = advance(ep, False, T0, rule, rule_id="r1", subject_id="billing", new_id="new1")
    assert new_ep.state == EpisodeState.CLEAR
    assert transitions == []


def test_no_episode_and_not_matched_creates_a_clear_placeholder():
    """The very first evaluation of a (rule, subject) pair that never
    matches: no prior episode exists, matched=False -- a fresh clear
    episode is constructed (not just a bare None), so the caller always has
    something concrete to persist/compare against next time."""
    rule = _rule()
    new_ep, transitions = advance(None, False, T0, rule, rule_id="r1", subject_id="billing", new_id="new1")
    assert new_ep.state == EpisodeState.CLEAR
    assert new_ep.id == "new1"
    assert transitions == []


def test_clear_true_zero_duration_opens_immediately():
    rule = _rule(for_duration_sec=0)
    new_ep, transitions = advance(None, True, T0, rule, rule_id="r1", subject_id="billing", new_id="new1")
    assert new_ep.state == EpisodeState.OPEN
    assert new_ep.id == "new1"
    assert new_ep.first_true_at == T0
    assert new_ep.opened_at == T0
    assert new_ep.notify_seq == 0
    assert transitions == [Transition.OPENED]


def test_clear_true_positive_duration_goes_pending_no_emit():
    rule = _rule(for_duration_sec=300)
    new_ep, transitions = advance(None, True, T0, rule, rule_id="r1", subject_id="billing", new_id="new1")
    assert new_ep.state == EpisodeState.PENDING
    assert new_ep.first_true_at == T0
    assert transitions == []


def test_pending_false_goes_clear_silently():
    """The conjunction reset: pending -> clear on matched=False is silent --
    blips die here with no notification, no error."""
    rule = _rule(for_duration_sec=300)
    ep = _ep(EpisodeState.PENDING, first_true_at=T0)
    new_ep, transitions = advance(ep, False, _t(100), rule, rule_id="r1", subject_id="billing", new_id="new2")
    assert new_ep.state == EpisodeState.CLEAR
    assert new_ep.first_true_at is None
    assert transitions == []


def test_pending_true_still_waiting_suppresses():
    rule = _rule(for_duration_sec=300)
    ep = _ep(EpisodeState.PENDING, first_true_at=T0, evaluations_suppressed=2)
    new_ep, transitions = advance(ep, True, _t(200), rule, rule_id="r1", subject_id="billing", new_id="new1")
    assert new_ep.state == EpisodeState.PENDING
    assert new_ep.evaluations_suppressed == 3
    assert transitions == []


def test_pending_true_duration_elapsed_opens():
    rule = _rule(for_duration_sec=300)
    ep = _ep(EpisodeState.PENDING, first_true_at=T0)
    new_ep, transitions = advance(ep, True, _t(300), rule, rule_id="r1", subject_id="billing", new_id="new1")
    assert new_ep.state == EpisodeState.OPEN
    assert new_ep.opened_at == _t(300)
    assert new_ep.notify_seq == 0
    assert new_ep.id == ep.id  # same episode, not a new one
    assert transitions == [Transition.OPENED]


def test_open_true_cooldown_zero_suppresses_always():
    rule = _rule(cooldown_sec=0)
    ep = _ep(EpisodeState.OPEN, opened_at=T0, last_notified_at=T0, notify_seq=0, evaluations_suppressed=5)
    new_ep, transitions = advance(ep, True, _t(9999), rule, rule_id="r1", subject_id="billing", new_id="new1")
    assert new_ep.state == EpisodeState.OPEN
    assert new_ep.evaluations_suppressed == 6
    assert new_ep.notify_seq == 0
    assert transitions == []


def test_open_true_within_cooldown_suppresses():
    rule = _rule(cooldown_sec=600)
    ep = _ep(EpisodeState.OPEN, opened_at=T0, last_notified_at=T0, notify_seq=0)
    new_ep, transitions = advance(ep, True, _t(300), rule, rule_id="r1", subject_id="billing", new_id="new1")
    assert new_ep.state == EpisodeState.OPEN
    assert new_ep.evaluations_suppressed == 1
    assert new_ep.notify_seq == 0
    assert transitions == []


def test_open_true_cooldown_elapsed_emits_reminder():
    rule = _rule(cooldown_sec=600)
    ep = _ep(EpisodeState.OPEN, opened_at=T0, last_notified_at=T0, notify_seq=0)
    new_ep, transitions = advance(ep, True, _t(600), rule, rule_id="r1", subject_id="billing", new_id="new1")
    assert new_ep.state == EpisodeState.OPEN
    assert new_ep.notify_seq == 1
    assert new_ep.last_notified_at == _t(600)
    assert transitions == [Transition.REMINDER]


def test_open_false_closes_and_resolves():
    rule = _rule()
    ep = _ep(EpisodeState.OPEN, opened_at=T0, last_notified_at=T0)
    new_ep, transitions = advance(ep, False, _t(500), rule, rule_id="r1", subject_id="billing", new_id="new1")
    assert new_ep.state == EpisodeState.CLEAR
    assert new_ep.closed_at == _t(500)
    assert transitions == [Transition.RESOLVED]


# -- Named scenario tests from Phase 3's own Tests section -------------------

def test_for_duration_conjunction_reset():
    """Two conditions, for_duration=300. A true at t=0 (matched=True); B
    false at t=200 (matched=False, resets); both true at t=250
    (matched=True, fresh pending) -> no notification at t=300 (only 50s
    into the new window), opened at t=550 (250 + 300). The clock restarted;
    it did not resume from t=0."""
    rule = _rule(for_duration_sec=300)

    ep, trans = advance(None, True, _t(0), rule, rule_id="r1", subject_id="billing", new_id="ep_a")
    assert ep.state == EpisodeState.PENDING and ep.first_true_at == _t(0)
    assert trans == []

    ep, trans = advance(ep, False, _t(200), rule, rule_id="r1", subject_id="billing", new_id="ep_b")
    assert ep.state == EpisodeState.CLEAR
    assert trans == []

    ep, trans = advance(None, True, _t(250), rule, rule_id="r1", subject_id="billing", new_id="ep_c")
    assert ep.state == EpisodeState.PENDING and ep.first_true_at == _t(250)
    assert trans == []

    ep, trans = advance(ep, True, _t(300), rule, rule_id="r1", subject_id="billing", new_id="ep_d")
    assert ep.state == EpisodeState.PENDING  # only 50s since t=250, not 300 yet
    assert trans == []

    ep, trans = advance(ep, True, _t(550), rule, rule_id="r1", subject_id="billing", new_id="ep_e")
    assert ep.state == EpisodeState.OPEN
    assert trans == [Transition.OPENED]


def test_blip_suppression_billing_tickets_waiting_22_to_20():
    """Model on billing tickets_waiting 22 -> 20: true then false inside
    for_duration -> transitions list empty across the whole sequence."""
    rule = _rule(for_duration_sec=300)
    ep, trans1 = advance(None, True, _t(0), rule, rule_id="r1", subject_id="billing", new_id="ep_a")
    ep, trans2 = advance(ep, False, _t(240), rule, rule_id="r1", subject_id="billing", new_id="ep_b")
    assert ep.state == EpisodeState.CLEAR
    assert trans1 + trans2 == []


def test_cooldown_default_zero_notifies_once_across_20_evaluations():
    """cooldown_sec=0, true for 20 consecutive evaluations -> exactly one
    opened, suppressed == 19."""
    rule = _rule(for_duration_sec=0, cooldown_sec=0)
    ep, trans = advance(None, True, _t(0), rule, rule_id="r1", subject_id="billing", new_id="ep_a")
    all_transitions = list(trans)
    for i in range(1, 20):
        ep, trans = advance(ep, True, _t(i * 60), rule, rule_id="r1", subject_id="billing", new_id="unused")
        all_transitions += trans
    assert all_transitions == [Transition.OPENED]
    assert ep.evaluations_suppressed == 19


def test_reminder_cadence_45_min_every_60s_cooldown_600():
    """cooldown_sec=600, true for 45 min evaluated every 60s -> 1 opened +
    4 reminder, notify_seq 0..4 monotonic."""
    rule = _rule(for_duration_sec=0, cooldown_sec=600)
    ep, trans = advance(None, True, _t(0), rule, rule_id="r1", subject_id="billing", new_id="ep_a")
    all_transitions = list(trans)
    seqs = [ep.notify_seq]
    for i in range(1, 46):  # every 60s from t=60 to t=2700 (45 min)
        ep, trans = advance(ep, True, _t(i * 60), rule, rule_id="r1", subject_id="billing", new_id="unused")
        all_transitions += trans
        if trans:
            seqs.append(ep.notify_seq)
    assert all_transitions == [Transition.OPENED] + [Transition.REMINDER] * 4
    assert seqs == [0, 1, 2, 3, 4]


def test_resolution_then_idempotent_further_false():
    """open -> false -> exactly one resolved; a further false emits nothing
    -- idempotent at the machine level, before the DB constraint applies."""
    rule = _rule()
    ep = _ep(EpisodeState.OPEN, opened_at=T0, last_notified_at=T0)
    ep, trans1 = advance(ep, False, _t(100), rule, rule_id="r1", subject_id="billing", new_id="unused")
    ep, trans2 = advance(ep, False, _t(200), rule, rule_id="r1", subject_id="billing", new_id="unused")
    assert trans1 == [Transition.RESOLVED]
    assert trans2 == []


def test_advance_rejects_mismatched_rule_id_or_subject_id():
    """Code-review MEDIUM: a caller bug (e.g. an off-by-one loading the
    wrong episode for a (rule_id, subject_id) pair) must fail loudly here,
    at the one place that can see both the episode's real identity and the
    caller's claimed identity -- not propagate a mismatched Episode
    downstream to be persisted under the wrong key."""
    rule = _rule()
    ep = _ep(EpisodeState.OPEN, rule_id="r1", subject_id="billing", opened_at=T0, last_notified_at=T0)
    with pytest.raises(ValueError, match="rule_id"):
        advance(ep, True, T0, rule, rule_id="r_WRONG", subject_id="billing", new_id="unused")
    with pytest.raises(ValueError, match="subject_id"):
        advance(ep, True, T0, rule, rule_id="r1", subject_id="tier_2_WRONG", new_id="unused")


# -- Phase 9: resolve hardening (stale freeze on missing driving fact) ------

def test_open_missing_driving_fact_freezes_as_stale_instead_of_resolving():
    """The dangerous half of unknown-as-false (PLAN.md Phase 9): a fact the
    rule's own conditions depend on goes MISSING while OPEN. Resolving here
    would emit a RESOLVED notification asserting recovery with no evidence
    (the real a_19 bug). Instead: refuse to resolve, freeze as stale."""
    rule = _rule()  # conditions: [{"fact": "tickets_waiting", ...}]
    ep = _ep(EpisodeState.OPEN, opened_at=T0, last_notified_at=T0)
    new_ep, transitions = advance(
        ep, False, _t(100), rule, rule_id="r1", subject_id="billing", new_id="unused",
        missing_facts=["tickets_waiting"],
    )
    assert transitions == []
    assert new_ep.state == EpisodeState.OPEN
    assert new_ep.stale is True
    assert new_ep.stale_since == _t(100)


def test_stale_episode_then_real_false_resolves_and_clears_stale():
    """Once frozen as stale, a genuine False (the fact is present and the
    condition no longer holds) must still resolve normally and clear the
    stale flag -- freezing is not a permanent lock."""
    rule = _rule()
    ep = _ep(EpisodeState.OPEN, opened_at=T0, last_notified_at=T0)
    stale_ep, trans1 = advance(
        ep, False, _t(100), rule, rule_id="r1", subject_id="billing", new_id="unused",
        missing_facts=["tickets_waiting"],
    )
    assert trans1 == []
    assert stale_ep.stale is True

    resolved_ep, trans2 = advance(
        stale_ep, False, _t(200), rule, rule_id="r1", subject_id="billing", new_id="unused",
        missing_facts=[],
    )
    assert trans2 == [Transition.RESOLVED]
    assert resolved_ep.state == EpisodeState.CLEAR
    assert resolved_ep.stale is False
    assert resolved_ep.stale_since is None


def test_missing_fact_not_a_rule_condition_does_not_freeze():
    """Only facts the rule's OWN conditions depend on count. A missing fact
    unrelated to this rule (e.g. a fact_ref target from a different
    condition set, or noise from another rule) must not block resolution."""
    rule = _rule()  # only condition is on "tickets_waiting"
    ep = _ep(EpisodeState.OPEN, opened_at=T0, last_notified_at=T0)
    new_ep, transitions = advance(
        ep, False, _t(100), rule, rule_id="r1", subject_id="billing", new_id="unused",
        missing_facts=["longest_wait_sec"],
    )
    assert transitions == [Transition.RESOLVED]
    assert new_ep.state == EpisodeState.CLEAR
    assert new_ep.stale is False


def test_open_missing_fact_ref_target_freezes_as_stale():
    """Code-review CRITICAL fix: a condition's own `fact` is not the only
    thing it depends on -- a FactRef condition (e.g. `longest_wait_sec >
    {fact_ref: sla_target_sec}`, the real seeded SLA rule) also depends on
    its fact_ref TARGET. If only condition.fact were checked, a missing
    fact_ref target would resolve normally -- reproducing the exact
    dangerous-half bug Phase 9 exists to close, just through the fact_ref
    door instead of the plain-fact door."""
    from app.domain.rules import FactRef

    rule = _rule(
        conditions=[{"fact": "longest_wait_sec", "op": "gt", "value": {"fact_ref": "sla_target_sec"}}],
    )
    assert isinstance(rule.conditions[0].value, FactRef)
    ep = _ep(EpisodeState.OPEN, opened_at=T0, last_notified_at=T0)
    new_ep, transitions = advance(
        ep, False, _t(100), rule, rule_id="r1", subject_id="billing", new_id="unused",
        missing_facts=["sla_target_sec"],
    )
    assert transitions == []
    assert new_ep.state == EpisodeState.OPEN
    assert new_ep.stale is True
    assert new_ep.stale_since == _t(100)


def test_stale_episode_thaws_on_genuine_reminder():
    """Code-review HIGH fix: once fresh data confirms the condition still
    genuinely matches (a real, data-backed REMINDER), the episode is no
    longer 'awaiting data' -- stale must clear, not persist until an
    eventual resolve."""
    rule = _rule(cooldown_sec=600)
    ep = _ep(EpisodeState.OPEN, opened_at=T0, last_notified_at=T0, stale=True, stale_since=_t(50))
    new_ep, transitions = advance(
        ep, True, _t(600), rule, rule_id="r1", subject_id="billing", new_id="unused",
    )
    assert transitions == [Transition.REMINDER]
    assert new_ep.stale is False
    assert new_ep.stale_since is None


def test_stale_episode_thaws_on_genuine_cooldown_suppressed_match():
    """Same as above, but the matched=True evaluation lands within
    cooldown (suppressed, not a REMINDER) -- still real data, still must
    thaw."""
    rule = _rule(cooldown_sec=600)
    ep = _ep(EpisodeState.OPEN, opened_at=T0, last_notified_at=T0, stale=True, stale_since=_t(50))
    new_ep, transitions = advance(
        ep, True, _t(300), rule, rule_id="r1", subject_id="billing", new_id="unused",
    )
    assert transitions == []
    assert new_ep.stale is False
    assert new_ep.stale_since is None


def test_opening_path_unchanged_by_missing_facts_missing_still_means_dont_open():
    """The asymmetry Phase 9 exists to fix is scoped to resolving an
    already-OPEN episode. The opening path (clear/pending -> open) is
    untouched: a missing driving fact must still fail safe (no open),
    exactly as before Phase 9."""
    rule = _rule(for_duration_sec=0)
    new_ep, transitions = advance(
        None, False, T0, rule, rule_id="r1", subject_id="billing", new_id="new1",
        missing_facts=["tickets_waiting"],
    )
    assert new_ep.state == EpisodeState.CLEAR
    assert transitions == []


def test_reopen_after_close_gets_a_new_id_old_stays_closed():
    """open -> clear -> true again -> a NEW episode with a new id; the old
    (now-closed) episode's identity must not be reused -- the partial
    unique index on (rule_id, subject_id) WHERE state IN (pending, open)
    depends on the old row genuinely being 'clear', not still 'open'."""
    rule = _rule(for_duration_sec=0)
    old = _ep(EpisodeState.OPEN, id="old_ep", opened_at=T0, last_notified_at=T0)
    closed, trans1 = advance(old, False, _t(100), rule, rule_id="r1", subject_id="billing", new_id="unused")
    assert closed.state == EpisodeState.CLEAR
    assert trans1 == [Transition.RESOLVED]

    reopened, trans2 = advance(closed, True, _t(200), rule, rule_id="r1", subject_id="billing", new_id="new_ep")
    assert reopened.state == EpisodeState.OPEN
    assert reopened.id == "new_ep"
    assert reopened.id != old.id
    assert trans2 == [Transition.OPENED]
