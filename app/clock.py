from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Source of truth for 'now'. Every other module gets time through this."""

    def now(self) -> datetime: ...


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"Clock requires a tz-aware UTC datetime, got {value!r}")
    return value


class SystemClock:
    """The one permitted call site for wall-clock time (PLAN.md 1.6)."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class ManualClock:
    """Driven explicitly by tests and the replay CLI. Never wall-clock time."""

    def __init__(self, start: datetime) -> None:
        self._now = _require_utc(start)

    def now(self) -> datetime:
        return self._now

    def set(self, when: datetime) -> None:
        self._now = _require_utc(when)

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)
