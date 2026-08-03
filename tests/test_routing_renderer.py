from datetime import datetime, timezone

from app.domain.facts import MISSING
from app.routing.renderer import render, render_suppression_summary
from app.storage.repositories import EpisodeRecord


def test_render_substitutes_subject_id_and_facts():
    body = render("{subject_id} is breaching SLA: {longest_wait_sec} > {sla_target_sec}",
                   "billing", {"longest_wait_sec": 130, "sla_target_sec": 120})
    assert body == "billing is breaching SLA: 130 > 120"


def test_render_handles_missing_fact_gracefully():
    """A MISSING value in facts_snapshot must not crash rendering (e.g. a
    stale-episode edge case) -- render it as a readable placeholder."""
    body = render("{subject_id}: {adherence_violation_duration_sec}", "a_23",
                   {"adherence_violation_duration_sec": MISSING})
    assert "a_23" in body
    assert "MISSING" in body or "unknown" in body


def _episode(**overrides) -> EpisodeRecord:
    data = dict(
        id="ep1", rule_id="r1", subject_id="billing", state="open",
        first_true_at=None, opened_at="2026-05-26T09:00:00Z", closed_at=None,
        last_notified_at="2026-05-26T09:36:00Z", notify_seq=0,
        evaluations_suppressed=0, stale=False,
    )
    data.update(overrides)
    return EpisodeRecord(**data)


def test_suppression_summary_none_when_nothing_suppressed():
    episode = _episode(evaluations_suppressed=0)
    as_of = datetime(2026, 5, 26, 10, 21, tzinfo=timezone.utc)
    assert render_suppression_summary(episode, as_of) is None


def test_suppression_summary_uses_closed_at_when_episode_resolved():
    episode = _episode(evaluations_suppressed=5, closed_at="2026-05-26T10:21:00Z")
    as_of = datetime(2026, 5, 27, tzinfo=timezone.utc)  # must be ignored -- episode is closed
    summary = render_suppression_summary(episode, as_of)
    assert "09:36:00" in summary
    assert "5 further evaluations" in summary
    assert "45 min" in summary
    assert summary.endswith("suppressed.")


def test_suppression_summary_uses_as_of_when_episode_still_open():
    episode = _episode(evaluations_suppressed=1, closed_at=None)
    as_of = datetime(2026, 5, 26, 9, 51, tzinfo=timezone.utc)
    summary = render_suppression_summary(episode, as_of)
    assert "1 further evaluation " in summary  # singular, no trailing "s"
    assert "15 min" in summary
