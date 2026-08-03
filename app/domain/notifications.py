from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.domain.episodes import Transition


class Notification(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    episode_id: str
    rule_id: str
    subject_id: str
    transition: Transition
    occurrence_seq: int
    recipient_kind: str
    recipient_target: str
    body: str
    facts_snapshot: dict[str, Any]
    event_time: datetime
    created_at: datetime
