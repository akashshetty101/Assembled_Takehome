from datetime import datetime

from app.domain.episodes import Episode, EpisodeState, Transition
from app.domain.rules import FactRef, Rule


def advance(
    episode: Episode | None,
    matched: bool,
    now: datetime,
    rule: Rule,
    *,
    rule_id: str,
    subject_id: str,
    new_id: str,
    missing_facts: list[str] | None = None,
) -> tuple[Episode, list[Transition]]:
    """Pure. for_duration applies to the CONJUNCTION, not per-condition:
    this function sees one already-ANDed boolean (matched). Any condition
    going false resets the clock -- pending -> clear on matched=False IS
    the conjunction reset; there are no per-condition timers anywhere in
    this module. Say so here, or a reader will hunt for them.

    `episode=None` means no active (pending/open) episode exists for this
    (rule_id, subject_id) pair -- treated identically to a closed 'clear'
    episode. `new_id` is a caller-supplied fresh id (e.g. uuid4()), used
    only when a NEW episode must be created (clear/None -> pending/open);
    id generation is impure and stays outside this function.

    `missing_facts` (Phase 9, resolve hardening): only consulted on the
    open -> clear edge. If a fact the rule's own conditions depend on is
    MISSING, `matched=False` is not evidence of recovery -- it's an
    absence of evidence. Resolving there would emit a RESOLVED
    notification asserting "recovered" with nothing behind it. Everywhere
    else (opening, pending, reminders) unknown-as-false is left exactly as
    it was: this is the scoped, cheap fix for the dangerous half only
    (Phase 10 -- full three-valued logic -- is the general fix).

    """
    if episode is not None:
        if episode.rule_id != rule_id:
            raise ValueError(
                f"episode.rule_id {episode.rule_id!r} does not match rule_id {rule_id!r} -- "
                "caller loaded the wrong episode for this (rule, subject) pair"
            )
        if episode.subject_id != subject_id:
            raise ValueError(
                f"episode.subject_id {episode.subject_id!r} does not match subject_id "
                f"{subject_id!r} -- caller loaded the wrong episode for this (rule, subject) pair"
            )

    state = episode.state if episode is not None else EpisodeState.CLEAR

    if state == EpisodeState.CLEAR:
        if not matched:
            if episode is not None:
                return episode, []
            return (
                Episode(id=new_id, rule_id=rule_id, subject_id=subject_id, state=EpisodeState.CLEAR),
                [],
            )
        if rule.for_duration_sec == 0:
            opened = Episode(
                id=new_id, rule_id=rule_id, subject_id=subject_id, state=EpisodeState.OPEN,
                first_true_at=now, opened_at=now, last_notified_at=now, notify_seq=0,
            )
            return opened, [Transition.OPENED]
        pending = Episode(
            id=new_id, rule_id=rule_id, subject_id=subject_id, state=EpisodeState.PENDING,
            first_true_at=now,
        )
        return pending, []

    if state == EpisodeState.PENDING:
        assert episode is not None and episode.first_true_at is not None
        if not matched:
            cleared = episode.model_copy(update={"state": EpisodeState.CLEAR, "first_true_at": None})
            return cleared, []
        elapsed = (now - episode.first_true_at).total_seconds()
        if elapsed < rule.for_duration_sec:
            suppressed = episode.model_copy(
                update={"evaluations_suppressed": episode.evaluations_suppressed + 1}
            )
            return suppressed, []
        opened = episode.model_copy(
            update={
                "state": EpisodeState.OPEN, "opened_at": now, "last_notified_at": now, "notify_seq": 0,
            }
        )
        return opened, [Transition.OPENED]

    # OPEN
    assert episode is not None
    if not matched:
        driving_facts = {c.fact for c in rule.conditions} | {
            c.value.fact_ref for c in rule.conditions if isinstance(c.value, FactRef)
        }
        if missing_facts and driving_facts & set(missing_facts):
            stale_since = episode.stale_since if episode.stale else now
            frozen = episode.model_copy(update={"stale": True, "stale_since": stale_since})
            return frozen, []
        closed = episode.model_copy(
            update={"state": EpisodeState.CLEAR, "closed_at": now, "stale": False, "stale_since": None}
        )
        return closed, [Transition.RESOLVED]

    # matched=True is itself real, data-backed evidence -- thaws a prior
    # freeze on both outcomes below (code-review HIGH fix), not just on
    # eventual resolve.
    if rule.cooldown_sec == 0:
        suppressed = episode.model_copy(
            update={
                "evaluations_suppressed": episode.evaluations_suppressed + 1,
                "stale": False, "stale_since": None,
            }
        )
        return suppressed, []

    assert episode.last_notified_at is not None
    elapsed_since_notify = (now - episode.last_notified_at).total_seconds()
    if elapsed_since_notify < rule.cooldown_sec:
        suppressed = episode.model_copy(
            update={
                "evaluations_suppressed": episode.evaluations_suppressed + 1,
                "stale": False, "stale_since": None,
            }
        )
        return suppressed, []

    reminder = episode.model_copy(
        update={
            "notify_seq": episode.notify_seq + 1, "last_notified_at": now,
            "stale": False, "stale_since": None,
        }
    )
    return reminder, [Transition.REMINDER]
