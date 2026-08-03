"""Sanity tests for the storage-layer *Record DTOs still pending domain-type
reconciliation (RuleRecord/EpisodeRecord/NotificationRecord -- Phases 2-3):
frozen, and structurally sufficient for their repository Protocols.
QueueStateRecord/AgentStateRecord were reconciled to the real
app.domain.subjects.QueueState/AgentState in Phase 1 (see
tests/test_domain_subjects.py for their frozen-ness/shape tests, and
tests/test_sqlite_subject_state.py for round-trip behavior). Round-trip
behavior for the DTOs below is exercised per-implementation in
tests/test_sqlite_*.py."""
import dataclasses

from app.storage.repositories import EpisodeRecord, EventRecord, NotificationRecord, RuleRecord


@dataclasses.dataclass(frozen=True)
class _AllRecords:
    event: EventRecord
    rule: RuleRecord
    episode: EpisodeRecord
    notification: NotificationRecord


def _sample() -> _AllRecords:
    return _AllRecords(
        event=EventRecord("evt_1", "2026-05-26T09:00:00Z", "queue_snapshot", "{}", 1),
        rule=RuleRecord(
            "r1", "name", "queue", "{}", "[]", 0, 0, "author", None, "t", True,
            "lead_sam", "2026-05-26T09:00:00Z", 1,
        ),
        episode=EpisodeRecord("ep1", "r1", "billing", "open", None, None, None, None, 0, 0, False),
        notification=NotificationRecord(
            "n1", "ep1", "r1", "billing", "opened", 0, "author", "lead_sam", "body", "{}",
            "2026-05-26T09:36:00Z", "2026-05-26T09:36:00Z",
        ),
    )


def test_all_records_are_frozen():
    sample = _sample()
    for record in (sample.event, sample.rule, sample.episode, sample.notification):
        assert record.__class__.__dataclass_params__.frozen


def test_records_construct_with_expected_fields():
    sample = _sample()
    assert sample.event.event_id == "evt_1"
    assert sample.rule.id == "r1"
    assert sample.episode.id == "ep1"
    assert sample.notification.id == "n1"
