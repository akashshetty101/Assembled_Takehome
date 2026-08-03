"""
Episode Transition State Machine.

This is a pure, side-effect-free state machine that advances the lifecycle of alert episodes.
An episode represents a rule-triggering incident for a specific subject (e.g., agent or queue) 
and can exist in three states: CLEAR, PENDING, or OPEN.

States & Lifecycle:
- CLEAR: The rule condition is currently false. No alerts are active.
- PENDING: The condition became true, but the duration threshold (`for_duration_sec`) 
  has not yet elapsed. This suppresses alerts for brief, self-correcting blips.
- OPEN: The condition has held true continuously for the required duration. 
  An alert has been emitted and the episode is active.

Key Behaviors & Concurrency Reset:
- Continuous Matches: `for_duration_sec` applies to the conjunction of all rule conditions. 
  Any evaluation returning `matched=False` immediately resets PENDING back to CLEAR, 
  restarting the duration clock.
- Cooldown Reminders: When OPEN, re-evaluations do not re-alert unless `cooldown_sec` 
  has elapsed, emitting Transition.REMINDER.
- Safe Resolution (Resolve Hardening): On the OPEN -> CLEAR transition, if `matched=False` 
  but the facts driving the rule are MISSING, the machine refuses to resolve. It freezes 
  the episode as `stale` to avoid emitting a false "recovered" notice during a data outage.
"""

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

    # --- 1. CLEAR STATE ---
    # The condition is currently false; we are waiting for it to become true.
    if state == EpisodeState.CLEAR:
        if not matched:
            # Still false: no change, return clear state
            if episode is not None:
                return episode, []
            return (
                Episode(id=new_id, rule_id=rule_id, subject_id=subject_id, state=EpisodeState.CLEAR),
                [],
            )
        # Condition became true: check if we alert immediately or wait for a duration threshold.
        if rule.for_duration_sec == 0:
            # No duration filter: open the episode immediately.
            opened = Episode(
                id=new_id, rule_id=rule_id, subject_id=subject_id, state=EpisodeState.OPEN,
                first_true_at=now, opened_at=now, last_notified_at=now, notify_seq=0,
            )
            return opened, [Transition.OPENED]
        # Duration filter active: move to PENDING and record the start timestamp.
        pending = Episode(
            id=new_id, rule_id=rule_id, subject_id=subject_id, state=EpisodeState.PENDING,
            first_true_at=now,
        )
        return pending, []

    # --- 2. PENDING STATE ---
    # The condition was true on a prior run; we are waiting for the duration to elapse.
    if state == EpisodeState.PENDING:
        assert episode is not None and episode.first_true_at is not None
        if not matched:
            # Conjunction Reset: Condition went false before duration elapsed.
            # Reset back to CLEAR silently (blip suppressed).
            cleared = episode.model_copy(update={"state": EpisodeState.CLEAR, "first_true_at": None})
            return cleared, []
        
        # Check if the condition has held true continuously for the required duration.
        elapsed = (now - episode.first_true_at).total_seconds()
        if elapsed < rule.for_duration_sec:
            # Threshold not yet reached: increment suppression counter and stay in PENDING.
            suppressed = episode.model_copy(
                update={"evaluations_suppressed": episode.evaluations_suppressed + 1}
            )
            return suppressed, []
        
        # Threshold reached: transition to OPEN and fire the OPENED notification.
        opened = episode.model_copy(
            update={
                "state": EpisodeState.OPEN, "opened_at": now, "last_notified_at": now, "notify_seq": 0,
            }
        )
        return opened, [Transition.OPENED]

    # --- 3. OPEN STATE ---
    # The alert is currently active and has already been notified.
    assert episode is not None
    if not matched:
        # The condition went false; try to resolve the episode.
        # Find all facts the rule depends on (direct conditions + right-hand references).
        driving_facts = {c.fact for c in rule.conditions} | {
            c.value.fact_ref for c in rule.conditions if isinstance(c.value, FactRef)
        }
        
        # Resolve Hardening (Phase 9): If a fact we rely on is missing from the state,
        # we cannot trust the matched=False result. Freeze the episode as 'stale' instead of resolving it.
        if missing_facts and driving_facts & set(missing_facts):
            stale_since = episode.stale_since if episode.stale else now
            frozen = episode.model_copy(update={"stale": True, "stale_since": stale_since})
            return frozen, []
            
        # Clean recovery: all driving facts are present, and the condition is genuinely false. Resolve!
        closed = episode.model_copy(
            update={"state": EpisodeState.CLEAR, "closed_at": now, "stale": False, "stale_since": None}
        )
        return closed, [Transition.RESOLVED]

    # matched=True means the issue is still active. This data-backed evidence
    # clears any previous stale freeze (thaws the episode).
    
    # If cooldown is 0, we notify exactly once per incident.
    # Subsequent active matches are suppressed.
    if rule.cooldown_sec == 0:
        suppressed = episode.model_copy(
            update={
                "evaluations_suppressed": episode.evaluations_suppressed + 1,
                "stale": False, "stale_since": None,
            }
        )
        return suppressed, []

    # Cooldown is active: check if enough time has elapsed since the last notification.
    assert episode.last_notified_at is not None
    elapsed_since_notify = (now - episode.last_notified_at).total_seconds()
    if elapsed_since_notify < rule.cooldown_sec:
        # Within cooldown: suppress repeat alert.
        suppressed = episode.model_copy(
            update={
                "evaluations_suppressed": episode.evaluations_suppressed + 1,
                "stale": False, "stale_since": None,
            }
        )
        return suppressed, []

    # Cooldown elapsed: increment notification sequence and emit a REMINDER.
    reminder = episode.model_copy(
        update={
            "notify_seq": episode.notify_seq + 1, "last_notified_at": now,
            "stale": False, "stale_since": None,
        }
    )
    return reminder, [Transition.REMINDER]
