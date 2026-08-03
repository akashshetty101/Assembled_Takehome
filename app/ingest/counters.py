from app.storage.repositories import CountersRepository

COUNTER_NAMES = (
    "events_accepted",
    "duplicates",
    "duplicate_payload_mismatch",
    "late_drops",
    "validation_failures",
    "state_disagreement",
    "evaluations_event",
    "evaluations_due",
)


class Counters:
    """Named wrapper around CountersRepository. Guards against a typo'd
    counter name silently creating an unrendered stray row instead of
    raising -- validation at the boundary. Names fixed now; Phase 5 renders
    them. subjects_tracked is NOT here: it's derived at render time from
    queue_repo.count() + agent_repo.count(), not an incrementable counter."""

    def __init__(self, repo: CountersRepository) -> None:
        self._repo = repo

    def increment(self, name: str, by: int = 1) -> None:
        if name not in COUNTER_NAMES:
            raise ValueError(f"unknown counter name: {name!r}")
        self._repo.increment(name, by)

    def get(self, name: str) -> int:
        if name not in COUNTER_NAMES:
            raise ValueError(f"unknown counter name: {name!r}")
        return self._repo.get(name)

    def snapshot(self) -> dict[str, int]:
        return {name: self.get(name) for name in COUNTER_NAMES}
