from datetime import datetime, timezone

from app.domain.episodes import Transition
from app.domain.notifications import Notification
from app.routing.console_channel import ConsoleChannel
from app.routing.inbox_channel import InboxChannel


def _notification(**overrides) -> Notification:
    data = dict(
        id="n1", episode_id="ep1", rule_id="r1", subject_id="a_11",
        transition=Transition.OPENED, occurrence_seq=0,
        recipient_kind="author", recipient_target="lead_sam",
        body="a_11 has been on a single call for 45m",
        facts_snapshot={}, event_time=datetime(2026, 5, 26, 9, 55, tzinfo=timezone.utc),
        created_at=datetime(2026, 5, 26, 9, 55, tzinfo=timezone.utc),
    )
    data.update(overrides)
    return Notification(**data)


def test_console_channel_formats_as_chat_message(capsys):
    ConsoleChannel().send(_notification())
    out = capsys.readouterr().out
    assert out.strip() == "[09:55:00] @lead_sam — a_11 has been on a single call for 45m"


def test_console_channel_channel_recipient_has_no_at_sign(capsys):
    ConsoleChannel().send(_notification(recipient_kind="channel", recipient_target="#ops-alerts"))
    out = capsys.readouterr().out
    assert "#ops-alerts" in out
    assert "@#ops-alerts" not in out


def test_inbox_channel_send_does_not_raise():
    """The 'inbox' is the notifications table + the /notifications page that
    reads from it -- by the time Channel.send() is called (after persist,
    Phase 4's dispatcher), the notification is already in the inbox."""
    InboxChannel().send(_notification())
