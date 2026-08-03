from datetime import datetime
from typing import Any

from app.domain.facts import MISSING
from app.storage.repositories import EpisodeRecord


def render(template: str, subject_id: str, facts_snapshot: dict[str, Any]) -> str:
    """template -> body. Rule validation already guarantees every
    placeholder in `template` is either {subject_id} or a fact this rule
    declared (domain/rules.py's compile-check), so this never KeyErrors in
    practice -- MISSING values are rendered as a readable placeholder
    rather than crashing or leaking the sentinel's repr."""
    values = {"subject_id": subject_id}
    for key, value in facts_snapshot.items():
        values[key] = "unknown" if value is MISSING else value
    return template.format(**values)


def render_suppression_summary(episode: EpisodeRecord, as_of: datetime) -> str | None:
    """PLAN.md Phase 6 item 3: 'notified once at 09:36, condition held true
    across 5 further evaluations over 45 min, suppressed.' Returns None
    when nothing was suppressed, so callers can skip rendering entirely
    instead of showing a hollow '0 further evaluations' line. Uses
    `episode.closed_at` as the window end for a resolved episode (a fixed,
    reproducible fact) and the caller-supplied `as_of` (the current clock,
    never read directly -- this module stays pure) only while the episode
    is still open."""
    if episode.evaluations_suppressed == 0 or episode.last_notified_at is None:
        return None
    last_notified = datetime.fromisoformat(episode.last_notified_at)
    end = datetime.fromisoformat(episode.closed_at) if episode.closed_at else as_of
    minutes = max(0, round((end - last_notified).total_seconds() / 60))
    n = episode.evaluations_suppressed
    plural = "" if n == 1 else "s"
    return (
        f"notified once at {episode.last_notified_at}, condition held true across "
        f"{n} further evaluation{plural} over {minutes} min, suppressed."
    )
