from app.domain.events import AdherenceCheckEvent, AgentStateChangeEvent
from app.domain.subjects import AgentState, ViolationStartSource
from app.ingest.normalize import normalize_agent_state, normalize_queue_ids


def _base(prev: AgentState | None, agent_id: str, ts) -> AgentState:
    return prev if prev is not None else AgentState(agent_id=agent_id, last_event_ts=ts)


def apply_state_change(prev: AgentState | None, event: AgentStateChangeEvent) -> AgentState:
    """agent_state_change is authoritative for current_state / state_entered_at
    (R7). state_entered_at is ALWAYS event.ts -- previous_state_duration_sec
    describes the state being left, not the one being entered (R6); it is
    read nowhere in this function on purpose."""
    base = _base(prev, event.agent_id, event.ts)
    return base.model_copy(
        update={
            "current_state": normalize_agent_state(event.new_state),
            "state_entered_at": event.ts,
            "queue_ids": normalize_queue_ids(event.queue_ids),
            "last_event_ts": event.ts,
        }
    )


def apply_adherence(prev: AgentState | None, event: AdherenceCheckEvent) -> AgentState:
    """adherence_check.actual_state feeds adherence facts only and NEVER
    mutates current_state (R7) -- current_state is simply absent from every
    update dict below. Prefers event.violation_started_at; when in_violation
    is true but the timestamp is null, records violation_start_source
    "unknown" and leaves the timestamp null rather than substituting
    event.ts, which would manufacture a duration out of nothing."""
    base = _base(prev, event.agent_id, event.ts)
    queue_ids = normalize_queue_ids(event.queue_ids)

    if not event.in_violation:
        return base.model_copy(
            update={
                "queue_ids": queue_ids,
                "violation_active": False,
                "violation_started_at": None,
                "violation_start_source": ViolationStartSource.UNKNOWN,
                "last_event_ts": event.ts,
            }
        )

    if event.violation_started_at is not None:
        return base.model_copy(
            update={
                "queue_ids": queue_ids,
                "violation_active": True,
                "violation_started_at": event.violation_started_at,
                "violation_start_source": ViolationStartSource.EVENT,
                "last_event_ts": event.ts,
            }
        )

    return base.model_copy(
        update={
            "queue_ids": queue_ids,
            "violation_active": True,
            "violation_started_at": None,
            "violation_start_source": ViolationStartSource.UNKNOWN,
            "last_event_ts": event.ts,
        }
    )


def state_disagreement(prev: AgentState | None, event: AdherenceCheckEvent) -> bool:
    """Pure detection for R7's state_disagreement counter: true when the
    adherence check's actual_state disagrees with the last agent_state_change
    -- e.g. evt_01HXYZ086 (a_23, actual_state=in_meeting) vs the 09:45
    agent_state_change (on_call), with no transition event in between.
    False when there is no prior current_state to disagree with."""
    if prev is None or prev.current_state is None:
        return False
    return normalize_agent_state(event.actual_state) != prev.current_state
