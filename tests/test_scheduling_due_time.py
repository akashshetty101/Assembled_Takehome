from datetime import datetime, timedelta, timezone

from app.domain.episodes import Episode, EpisodeState
from app.domain.rules import Rule
from app.domain.subjects import AgentState, QueueState
from app.evaluation.registry import build_registry
from app.scheduling.due_time import next_due_at

REGISTRY = build_registry()
CTX = {"registry": REGISTRY}


def _rule(**overrides) -> Rule:
    data = {
        "name": "n", "subject_type": "agent", "selector": {"kind": "all"},
        "conditions": [
            {"fact": "current_state", "op": "eq", "value": "on_call"},
            {"fact": "current_state_duration_sec", "op": "gt", "value": 2700},
        ],
        "recipient": {"kind": "author"}, "template": "{subject_id}",
        "created_by": "lead_sam",
    }
    data.update(overrides)
    return Rule.model_validate(data, context=CTX)


def test_on_call_rule_no_episode_due_at_state_entered_plus_threshold():
    """a_11: state_entered_at=09:10 -> due at 09:55 (09:10 + 2700s)."""
    rule = _rule()
    state = AgentState(
        agent_id="a_11", current_state="on_call",
        state_entered_at=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc),
        last_event_ts=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc),
    )
    now = datetime(2026, 5, 26, 9, 20, tzinfo=timezone.utc)
    due = next_due_at(rule, state, None, now)
    assert due == datetime(2026, 5, 26, 9, 55, tzinfo=timezone.utc)


def test_pending_episode_due_at_first_true_plus_for_duration():
    rule = _rule(for_duration_sec=300, conditions=[{"fact": "tickets_waiting", "op": "gt", "value": 20}],
                 subject_type="queue")
    ep = Episode(
        id="ep1", rule_id="r1", subject_id="billing", state=EpisodeState.PENDING,
        first_true_at=datetime(2026, 5, 26, 9, 30, tzinfo=timezone.utc),
    )
    state = QueueState(queue_id="billing", last_event_ts=datetime(2026, 5, 26, 9, 30, tzinfo=timezone.utc))
    now = datetime(2026, 5, 26, 9, 31, tzinfo=timezone.utc)
    due = next_due_at(rule, state, ep, now)
    assert due == datetime(2026, 5, 26, 9, 35, tzinfo=timezone.utc)


def test_open_episode_with_cooldown_due_at_last_notified_plus_cooldown():
    rule = _rule(cooldown_sec=600)
    ts = datetime(2026, 5, 26, 9, 45, tzinfo=timezone.utc)
    ep = Episode(
        id="ep1", rule_id="r1", subject_id="a_19", state=EpisodeState.OPEN,
        opened_at=ts, last_notified_at=ts,
    )
    state = AgentState(agent_id="a_19", current_state="on_call", state_entered_at=ts, last_event_ts=ts)
    due = next_due_at(rule, state, ep, ts + timedelta(seconds=10))
    assert due == ts + timedelta(seconds=600)


def test_open_episode_with_zero_cooldown_has_no_due_time():
    """cooldown=0 means notify-once; nothing time-derived can change the
    outcome, so there is nothing to wake for."""
    rule = _rule(cooldown_sec=0)
    ts = datetime(2026, 5, 26, 9, 45, tzinfo=timezone.utc)
    ep = Episode(id="ep1", rule_id="r1", subject_id="a_19", state=EpisodeState.OPEN,
                 opened_at=ts, last_notified_at=ts)
    state = AgentState(agent_id="a_19", current_state="on_call", state_entered_at=ts, last_event_ts=ts)
    assert next_due_at(rule, state, ep, ts) is None


def test_no_time_derived_fact_returns_none():
    """A rule with only tickets_waiting > 20 (no duration fact), no active
    episode -- nothing time-derived can change the outcome."""
    rule = _rule(subject_type="queue", conditions=[{"fact": "tickets_waiting", "op": "gt", "value": 20}])
    state = QueueState(queue_id="billing", tickets_waiting=5,
                        last_event_ts=datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc))
    now = datetime(2026, 5, 26, 9, 30, tzinfo=timezone.utc)
    assert next_due_at(rule, state, None, now) is None


def test_snapshot_age_sec_rule_is_time_sensitive_on_a_queue():
    """R14: snapshot_age_sec is time-derived on a QUEUE -- easy to forget,
    since seven of eight queue facts are purely event-driven."""
    rule = _rule(subject_type="queue", conditions=[{"fact": "snapshot_age_sec", "op": "gt", "value": 120}])
    ts = datetime(2026, 5, 26, 9, 30, tzinfo=timezone.utc)
    state = QueueState(queue_id="billing", last_event_ts=ts)
    due = next_due_at(rule, state, None, ts)
    assert due == ts + timedelta(seconds=120)


def test_fact_ref_gated_duration_threshold_returns_none_known_phase8_gap():
    """Code-review MEDIUM, documented not fixed: _literal_threshold only
    handles a literal numeric threshold, so a duration fact compared via
    fact_ref (not used by any current seed rule) makes next_due_at return
    None even though ScanScheduler's cruder fact-name-only check would
    still include the subject. Harmless today (ScanScheduler ignores this
    function entirely); a real gap for Phase 8's HeapScheduler, which
    would need this case to work to ever schedule such a rule."""
    from app.domain.rules import FactRef

    rule = _rule(
        conditions=[
            {"fact": "current_state", "op": "eq", "value": "on_call"},
            {"fact": "current_state_duration_sec", "op": "gt",
             "value": {"fact_ref": "current_state_duration_sec"}},
        ],
    )
    assert isinstance(rule.conditions[1].value, FactRef)
    state = AgentState(agent_id="a_11", current_state="on_call",
                        state_entered_at=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc),
                        last_event_ts=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc))
    assert next_due_at(rule, state, None, datetime(2026, 5, 26, 9, 20, tzinfo=timezone.utc)) is None


def test_adherence_violation_rule_no_episode_due_at_violation_started_plus_threshold():
    """a_88: violation started 10:00, threshold 600s -> due 10:10."""
    rule = _rule(
        subject_type="agent",
        conditions=[
            {"fact": "in_adherence_violation", "op": "eq", "value": True},
            {"fact": "adherence_violation_duration_sec", "op": "gt", "value": 600},
        ],
    )
    ts = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
    state = AgentState(agent_id="a_88", violation_active=True, violation_started_at=ts, last_event_ts=ts)
    now = ts + timedelta(seconds=60)
    due = next_due_at(rule, state, None, now)
    assert due == ts + timedelta(seconds=600)
