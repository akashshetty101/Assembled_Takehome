"""Repository Protocols -- the Postgres swap surface. No SQL leaks past here;
callers never see sqlite3.Row.

Sequencing note (flagged, not a silent deviation): PLAN.md's module map places
these Protocols in Phase 0, but their final signatures are meant to be
domain/ types (Rule, Episode, Notification, QueueState, AgentState) that
Phases 1-3 haven't built yet. Phase 0 typed QueueStateRepository/
AgentStateRepository against small frozen-dataclass *Record DTOs standing in
for the not-yet-built domain types. Phase 1 built domain/subjects.py
(QueueState, AgentState) -- as promised, those two Protocols are now
reconciled to the real domain types below (a narrowing type change on top of
tests that already pass, not new plumbing). RulesRepository/EpisodesRepository/
NotificationsRepository still use their Phase 0 *Record DTOs, reconciled the
same way once domain/rules.py (Phase 2) and domain/episodes.py (Phase 3) land.

Transaction contract: no method on any concrete repository commits or rolls
back. Callers group one or more repository calls into a single atomic unit of
work using app.storage.db.transaction(conn) -- required by R4 ("the watermark
must advance only from accepted events, in the same transaction as the
projection write") and by Phase 6's persist-then-send guarantee.
"""

from dataclasses import dataclass
from typing import Protocol

from app.domain.subjects import AgentState, QueueState


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_id: str
    ts: str
    type: str
    payload_json: str
    received_seq: int


@dataclass(frozen=True, slots=True)
class RuleRecord:
    id: str
    name: str
    subject_type: str
    selector_json: str
    conditions_json: str
    for_duration_sec: int
    cooldown_sec: int
    recipient_kind: str
    recipient_target: str | None
    template: str
    enabled: bool
    created_by: str
    created_at: str
    version: int


@dataclass(frozen=True, slots=True)
class EpisodeRecord:
    id: str
    rule_id: str
    subject_id: str
    state: str
    first_true_at: str | None
    opened_at: str | None
    closed_at: str | None
    last_notified_at: str | None
    notify_seq: int
    evaluations_suppressed: int
    stale: bool


@dataclass(frozen=True, slots=True)
class NotificationRecord:
    id: str
    episode_id: str
    rule_id: str
    subject_id: str
    transition: str
    occurrence_seq: int
    recipient_kind: str
    recipient_target: str
    body: str
    facts_snapshot_json: str
    event_time: str
    created_at: str


class EventsRepository(Protocol):
    def exists(self, event_id: str) -> bool: ...
    def get(self, event_id: str) -> EventRecord | None: ...
    def append(self, record: EventRecord) -> None: ...


class CountersRepository(Protocol):
    def increment(self, name: str, by: int = 1) -> None: ...
    def get(self, name: str) -> int: ...
    def all(self) -> dict[str, int]: ...


class QueueStateRepository(Protocol):
    def get(self, queue_id: str) -> QueueState | None: ...
    def put(self, state: QueueState) -> None: ...
    def list(self) -> list[QueueState]: ...
    def count(self) -> int: ...


class AgentStateRepository(Protocol):
    def get(self, agent_id: str) -> AgentState | None: ...
    def put(self, state: AgentState) -> None: ...
    def list(self) -> list[AgentState]: ...
    def count(self) -> int: ...


class RulesRepository(Protocol):
    def get(self, rule_id: str) -> RuleRecord | None: ...
    def list(self, *, enabled_only: bool = False) -> list[RuleRecord]: ...
    def create(self, record: RuleRecord) -> None: ...
    def update(self, record: RuleRecord) -> None: ...


class EpisodesRepository(Protocol):
    def get(self, episode_id: str) -> EpisodeRecord | None: ...
    def get_active(self, rule_id: str, subject_id: str) -> EpisodeRecord | None: ...
    def create(self, record: EpisodeRecord) -> None: ...
    def update(self, record: EpisodeRecord) -> None: ...
    def list_active(self) -> list[EpisodeRecord]: ...


class NotificationsRepository(Protocol):
    def insert_if_absent(self, record: NotificationRecord) -> bool: ...
    def list(
        self,
        *,
        recipient_target: str | None = None,
        rule_id: str | None = None,
        subject_id: str | None = None,
    ) -> list[NotificationRecord]: ...
