from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Scheduler(Protocol):
    """PLAN.md 1.5. schedule()/due() drive the scan/heap loop; note_*
    are invalidation hooks -- ScanScheduler no-ops both (§1.5: 'zero
    invalidation logic'); HeapScheduler (Phase 8) uses them for real."""

    def schedule(self, due_at: datetime, rule_id: str, subject_id: str) -> None: ...
    def due(self, now: datetime) -> list[tuple[str, str]]: ...
    def note_subject_changed(self, subject_id: str) -> None: ...
    def note_rule_activated(self, rule_id: str) -> None: ...
