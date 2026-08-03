import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.clock import Clock, ManualClock, SystemClock


def test_system_clock_returns_tz_aware_utc_now():
    clock = SystemClock()
    now = clock.now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_manual_clock_starts_at_given_time():
    start = datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc)
    clock = ManualClock(start)
    assert clock.now() == start


def test_manual_clock_set_moves_time():
    clock = ManualClock(datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc))
    target = datetime(2026, 5, 26, 9, 55, tzinfo=timezone.utc)
    clock.set(target)
    assert clock.now() == target


def test_manual_clock_advance_adds_seconds():
    clock = ManualClock(datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc))
    clock.advance(300)
    assert clock.now() == datetime(2026, 5, 26, 9, 5, tzinfo=timezone.utc)


def test_manual_clock_rejects_naive_datetime_on_init():
    with pytest.raises(ValueError):
        ManualClock(datetime(2026, 5, 26, 9, 0))


def test_manual_clock_rejects_naive_datetime_on_set():
    clock = ManualClock(datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        clock.set(datetime(2026, 5, 26, 9, 5))


def test_manual_clock_rejects_non_utc_timezone():
    offset_tz = timezone(timedelta(hours=5))
    with pytest.raises(ValueError):
        ManualClock(datetime(2026, 5, 26, 9, 0, tzinfo=offset_tz))


def test_clock_protocol_satisfied_by_both_impls():
    assert isinstance(SystemClock(), Clock)
    manual = ManualClock(datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc))
    assert isinstance(manual, Clock)


APP_ROOT = Path(__file__).resolve().parent.parent / "app"
WALL_CLOCK_PATTERN = re.compile(r"datetime\.(now|utcnow)\s*\(")


def test_datetime_now_appears_in_exactly_one_file():
    """Architectural invariant from PLAN.md 1.6: datetime.now()/utcnow() lives
    only in app/clock.py. Everything else must go through the Clock protocol."""
    offenders = {}
    for path in APP_ROOT.rglob("*.py"):
        matches = WALL_CLOCK_PATTERN.findall(path.read_text())
        if matches:
            offenders[str(path.relative_to(APP_ROOT.parent))] = len(matches)
    assert offenders == {"app/clock.py": 1}
