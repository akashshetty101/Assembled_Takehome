import pytest
from pydantic import ValidationError

from app.domain.episodes import Episode, EpisodeState, Transition


def test_episode_state_values():
    assert {s.value for s in EpisodeState} == {"clear", "pending", "open"}


def test_transition_values():
    assert {t.value for t in Transition} == {"opened", "reminder", "resolved"}


def test_episode_defaults():
    ep = Episode(id="e1", rule_id="r1", subject_id="billing", state=EpisodeState.CLEAR)
    assert ep.first_true_at is None
    assert ep.opened_at is None
    assert ep.closed_at is None
    assert ep.last_notified_at is None
    assert ep.notify_seq == 0
    assert ep.evaluations_suppressed == 0
    assert ep.stale is False
    assert ep.stale_since is None


def test_episode_is_frozen():
    ep = Episode(id="e1", rule_id="r1", subject_id="billing", state=EpisodeState.CLEAR)
    with pytest.raises(ValidationError):
        ep.state = EpisodeState.OPEN


def test_pending_episode_requires_first_true_at():
    """Code-review MEDIUM: nothing but a bare assert in machine.py was
    guarding this -- a real invariant needs a real validator."""
    with pytest.raises(ValidationError, match="pending"):
        Episode(id="e1", rule_id="r1", subject_id="billing", state=EpisodeState.PENDING)


def test_open_episode_requires_opened_at_and_last_notified_at():
    with pytest.raises(ValidationError, match="open"):
        Episode(id="e1", rule_id="r1", subject_id="billing", state=EpisodeState.OPEN)
    with pytest.raises(ValidationError, match="open"):
        Episode(
            id="e1", rule_id="r1", subject_id="billing", state=EpisodeState.OPEN,
            opened_at=None, last_notified_at=None,
        )


def test_stale_requires_open_state_and_stale_since():
    """Phase 9 invariant: stale=True is only meaningful for an OPEN episode
    that's frozen awaiting data -- not a real, checkable-at-construction
    assert buried in machine.py."""
    from datetime import datetime, timezone

    ts = datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc)
    with pytest.raises(ValidationError, match="stale"):
        Episode(
            id="e1", rule_id="r1", subject_id="billing", state=EpisodeState.OPEN,
            opened_at=ts, last_notified_at=ts, stale=True, stale_since=None,
        )
    with pytest.raises(ValidationError, match="stale"):
        Episode(
            id="e1", rule_id="r1", subject_id="billing", state=EpisodeState.CLEAR,
            closed_at=ts, stale=True, stale_since=ts,
        )
    # Valid: stale=True with state=open and stale_since set.
    ep = Episode(
        id="e1", rule_id="r1", subject_id="billing", state=EpisodeState.OPEN,
        opened_at=ts, last_notified_at=ts, stale=True, stale_since=ts,
    )
    assert ep.stale_since == ts


def test_closed_clear_episode_may_retain_historical_timestamps():
    """A closed (open->clear) episode legitimately keeps its historical
    first_true_at/opened_at/last_notified_at -- only PENDING/OPEN states
    require their defining timestamps; CLEAR has no such requirement."""
    from datetime import datetime, timezone

    ts = datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc)
    ep = Episode(
        id="e1", rule_id="r1", subject_id="billing", state=EpisodeState.CLEAR,
        first_true_at=ts, opened_at=ts, last_notified_at=ts, closed_at=ts,
    )
    assert ep.opened_at == ts
