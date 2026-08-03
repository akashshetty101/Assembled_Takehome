from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator


class EpisodeState(str, Enum):
    CLEAR = "clear"
    PENDING = "pending"
    OPEN = "open"


class Transition(str, Enum):
    OPENED = "opened"
    REMINDER = "reminder"
    RESOLVED = "resolved"


class Episode(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    rule_id: str
    subject_id: str
    state: EpisodeState
    first_true_at: datetime | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    last_notified_at: datetime | None = None
    notify_seq: int = 0
    evaluations_suppressed: int = 0
    stale: bool = False
    stale_since: datetime | None = None

    @model_validator(mode="after")
    def _validate_state_consistency(self) -> "Episode":
        """Code-review MEDIUM fix: a real invariant, not a strippable
        assert in machine.py. CLEAR has no requirement -- a closed episode
        legitimately retains its historical timestamps."""
        if self.state == EpisodeState.PENDING and self.first_true_at is None:
            raise ValueError("state='pending' requires first_true_at to be set")
        if self.state == EpisodeState.OPEN and (self.opened_at is None or self.last_notified_at is None):
            raise ValueError("state='open' requires opened_at and last_notified_at to be set")
        if self.stale and (self.state != EpisodeState.OPEN or self.stale_since is None):
            raise ValueError("stale=True requires state='open' and stale_since to be set")
        return self
