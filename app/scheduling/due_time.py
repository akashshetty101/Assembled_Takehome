"""
Due Time Calculations.

This module houses the pure, mathematical calculations for determining the next evaluation 
due time of a rule-subject-episode combination. It is shared by both `ScanScheduler` and 
`HeapScheduler`, serving as the single source of truth for "time-sensitive" conditions.

Due-time Logic:
- Pending Episodes: Due exactly when the continuous match duration threshold (`for_duration_sec`) is met.
- Open Episodes (Cooldown): Due when the cooldown period (`cooldown_sec`) is elapsed, allowing reminder alerts.
- Idle Subjects (No active episode): Due when state-duration facts (like call duration or adherence violation duration) 
  will cross their literal thresholds in the future.
- Queue Snapshot Age: Handles queue rule sensitivity to snapshot arrival delays (R14).
"""

from datetime import datetime, timedelta
from typing import Any

from app.domain.episodes import Episode, EpisodeState
from app.domain.rules import FactRef, Rule
from app.domain.subjects import AgentState, QueueState, SubjectType

# fact -> the state field its "clock" starts from. Order matters only in
# that the first literal-threshold match wins if a rule somehow declared
# more than one (not a case PLAN's examples exercise).
_AGENT_DURATION_FACTS: dict[str, str] = {
    "current_state_duration_sec": "state_entered_at",
    "adherence_violation_duration_sec": "violation_started_at",
}

_QUEUE_DURATION_FACTS: tuple[str, ...] = ("snapshot_age_sec",)

# The one shared definition of "time-derived fact" (code-review MEDIUM fix):
# scheduling/scan.py imports this rather than maintaining its own copy, so
# the two "is this rule time-sensitive" checks cannot structurally drift
# apart -- this module (R14's) is the source of truth.
TIME_DERIVED_FACTS: frozenset[str] = frozenset(_AGENT_DURATION_FACTS) | frozenset(_QUEUE_DURATION_FACTS)


def _literal_threshold(rule: Rule, fact_name: str) -> float | None:
    for condition in rule.conditions:
        if condition.fact != fact_name or isinstance(condition.value, FactRef):
            continue
        if isinstance(condition.value, (int, float)) and not isinstance(condition.value, bool):
            return float(condition.value)
    return None


def next_due_at(rule: Rule, state: Any, episode: Episode | None, now: datetime) -> datetime | None:
    """Pure. ScanScheduler ignores this entirely; HeapScheduler (Phase 8)
    depends on it completely -- write it now anyway, since it is the pure,
    testable part of the heap and de-risks Phase 8 almost entirely.

    R14: snapshot_age_sec is a time-derived fact on a QUEUE, not just
    agent duration facts -- easy to forget, since seven of eight queue
    facts are purely event-driven. ScanScheduler's bug-masking means this
    only breaks in Phase 8 if forgotten here."""
    if episode is not None and episode.state == EpisodeState.PENDING:
        assert episode.first_true_at is not None
        return episode.first_true_at + timedelta(seconds=rule.for_duration_sec)

    if episode is not None and episode.state == EpisodeState.OPEN:
        if rule.cooldown_sec > 0:
            assert episode.last_notified_at is not None
            return episode.last_notified_at + timedelta(seconds=rule.cooldown_sec)
        return None  # notify-once: no time-derived fact can change the outcome

    if rule.subject_type == SubjectType.AGENT and isinstance(state, AgentState):
        for fact_name, field in _AGENT_DURATION_FACTS.items():
            threshold = _literal_threshold(rule, fact_name)
            if threshold is None:
                continue
            start = getattr(state, field)
            if start is not None:
                return start + timedelta(seconds=threshold)

    if rule.subject_type == SubjectType.QUEUE and isinstance(state, QueueState):
        threshold = _literal_threshold(rule, "snapshot_age_sec")  # R14
        if threshold is not None:
            return state.last_event_ts + timedelta(seconds=threshold)

    return None
