from datetime import datetime, timezone

from app.domain.episodes import Transition
from app.domain.notifications import Notification


def test_notification_construction():
    n = Notification(
        id="n1", episode_id="ep1", rule_id="r1", subject_id="billing",
        transition=Transition.OPENED, occurrence_seq=0,
        recipient_kind="author", recipient_target="lead_sam",
        body="billing has 22 tickets waiting", facts_snapshot={"tickets_waiting": 22},
        event_time=datetime(2026, 5, 26, 9, 36, tzinfo=timezone.utc),
        created_at=datetime(2026, 5, 26, 9, 36, tzinfo=timezone.utc),
    )
    assert n.transition == Transition.OPENED
    assert n.facts_snapshot["tickets_waiting"] == 22


def test_notification_is_frozen():
    import pytest
    from pydantic import ValidationError

    n = Notification(
        id="n1", episode_id="ep1", rule_id="r1", subject_id="billing",
        transition=Transition.OPENED, occurrence_seq=0,
        recipient_kind="author", recipient_target="lead_sam", body="x",
        facts_snapshot={}, event_time=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ValidationError):
        n.body = "changed"
