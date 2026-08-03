from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict

from app.domain.events import AdherenceCheckEvent, AgentStateChangeEvent, Event, QueueSnapshotEvent


class SubjectType(str, Enum):
    QUEUE = "queue"
    AGENT = "agent"


class SubjectRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject_type: SubjectType
    subject_id: str


class QueueState(BaseModel):
    model_config = ConfigDict(frozen=True)

    queue_id: str
    tickets_waiting: int | None = None
    longest_wait_sec: int | None = None
    sla_target_sec: int | None = None
    agents_available: int | None = None
    agents_on_call: int | None = None
    volume_last_15m: int | None = None
    volume_forecast_next_15m: int | None = None
    last_event_ts: datetime


class ViolationStartSource(str, Enum):
    EVENT = "event"
    OBSERVED = "observed"
    UNKNOWN = "unknown"


class AgentState(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_id: str
    current_state: str | None = None
    state_entered_at: datetime | None = None
    queue_ids: list[str] = []
    violation_active: bool = False
    violation_started_at: datetime | None = None
    violation_start_source: ViolationStartSource = ViolationStartSource.UNKNOWN
    last_event_ts: datetime


def subject_ref_for_event(event: Event) -> SubjectRef:
    if isinstance(event, QueueSnapshotEvent):
        return SubjectRef(subject_type=SubjectType.QUEUE, subject_id=event.queue_id)
    if isinstance(event, (AgentStateChangeEvent, AdherenceCheckEvent)):
        return SubjectRef(subject_type=SubjectType.AGENT, subject_id=event.agent_id)
    raise TypeError(f"unhandled event type: {type(event)!r}")
