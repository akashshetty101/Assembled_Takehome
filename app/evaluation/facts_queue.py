from datetime import datetime

from app.domain.facts import MISSING, FactSpec
from app.domain.subjects import QueueState, SubjectType


def _or_missing(value):
    return value if value is not None else MISSING


def _tickets_waiting(state: QueueState, now: datetime):
    return _or_missing(state.tickets_waiting)


def _longest_wait_sec(state: QueueState, now: datetime):
    return _or_missing(state.longest_wait_sec)


def _sla_target_sec(state: QueueState, now: datetime):
    return _or_missing(state.sla_target_sec)


def _agents_available(state: QueueState, now: datetime):
    return _or_missing(state.agents_available)


def _agents_on_call(state: QueueState, now: datetime):
    return _or_missing(state.agents_on_call)


def _volume_last_15m(state: QueueState, now: datetime):
    return _or_missing(state.volume_last_15m)


def _volume_forecast_next_15m(state: QueueState, now: datetime):
    """Cut from v1 rules (§1.2): null in the real sample data at 10:00 --
    "an input you cannot trust is not a v1 feature." The fact is still
    registered (so a future rule COULD reference it) but correctly reports
    MISSING rather than a guessed value."""
    return _or_missing(state.volume_forecast_next_15m)


def _snapshot_age_sec(state: QueueState, now: datetime):
    """R14: a time-derived fact on a QUEUE. It changes with no new event --
    the queue could be silent while this value keeps growing -- which is
    exactly why Phase 4's due_time.py must not assume queue rules are never
    time-sensitive."""
    return (now - state.last_event_ts).total_seconds()


QUEUE_FACTS: list[FactSpec] = [
    FactSpec("tickets_waiting", SubjectType.QUEUE, "number", _tickets_waiting),
    FactSpec("longest_wait_sec", SubjectType.QUEUE, "number", _longest_wait_sec),
    FactSpec("sla_target_sec", SubjectType.QUEUE, "number", _sla_target_sec),
    FactSpec("agents_available", SubjectType.QUEUE, "number", _agents_available),
    FactSpec("agents_on_call", SubjectType.QUEUE, "number", _agents_on_call),
    FactSpec("volume_last_15m", SubjectType.QUEUE, "number", _volume_last_15m),
    FactSpec("volume_forecast_next_15m", SubjectType.QUEUE, "number", _volume_forecast_next_15m),
    FactSpec("snapshot_age_sec", SubjectType.QUEUE, "number", _snapshot_age_sec),
]
