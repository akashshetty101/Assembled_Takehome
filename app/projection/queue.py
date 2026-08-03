from app.domain.events import QueueSnapshotEvent
from app.domain.subjects import QueueState


def apply_snapshot(prev: QueueState | None, event: QueueSnapshotEvent) -> QueueState:
    """A snapshot is the queue's entire state, not a delta -- prev is unused
    on purpose (kept in the signature to match the shared apply(prev, event)
    shape projection/agent.py also uses)."""
    del prev
    return QueueState(
        queue_id=event.queue_id,
        tickets_waiting=event.tickets_waiting,
        longest_wait_sec=event.longest_wait_sec,
        sla_target_sec=event.sla_target_sec,
        agents_available=event.agents_available,
        agents_on_call=event.agents_on_call,
        volume_last_15m=event.volume_last_15m,
        volume_forecast_next_15m=event.volume_forecast_next_15m,
        last_event_ts=event.ts,
    )
