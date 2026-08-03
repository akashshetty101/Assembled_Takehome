from datetime import datetime, timedelta
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"timestamp must be tz-aware UTC, got {value!r}")
    return value


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    ts: datetime

    @field_validator("ts")
    @classmethod
    def _ts_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class QueueSnapshotEvent(_EventBase):
    type: Literal["queue_snapshot"]
    queue_id: str
    tickets_waiting: int
    longest_wait_sec: int
    sla_target_sec: int
    agents_available: int
    agents_on_call: int
    volume_last_15m: int
    volume_forecast_next_15m: int | None = None


class AgentStateChangeEvent(_EventBase):
    type: Literal["agent_state_change"]
    agent_id: str
    queue_ids: list[str] | None = None
    previous_state: str | None = None
    previous_state_duration_sec: int | None = None
    new_state: str


class AdherenceCheckEvent(_EventBase):
    type: Literal["adherence_check"]
    agent_id: str
    queue_ids: list[str] | None = None
    scheduled_state: str
    actual_state: str
    in_violation: bool
    violation_started_at: datetime | None = None

    @field_validator("violation_started_at")
    @classmethod
    def _violation_started_at_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value is not None else None


Event = Annotated[
    Union[QueueSnapshotEvent, AgentStateChangeEvent, AdherenceCheckEvent],
    Field(discriminator="type"),
]

_event_adapter: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(raw: dict) -> Event:
    """Raises pydantic.ValidationError on an unknown `type` or any malformed
    field -- the caller (ingest/pipeline.py) counts this as a validation
    failure, not a crash."""
    return _event_adapter.validate_python(raw)
