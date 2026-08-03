from app.scheduling.base import Scheduler


class _FakeScheduler:
    def schedule(self, due_at, rule_id, subject_id): ...
    def due(self, now): return []
    def note_subject_changed(self, subject_id): ...
    def note_rule_activated(self, rule_id): ...


def test_fake_scheduler_satisfies_protocol():
    """Structural typing sanity check -- any object with these four methods
    satisfies Scheduler, no inheritance required."""
    assert isinstance(_FakeScheduler(), Scheduler)
