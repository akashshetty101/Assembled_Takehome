from app.domain.notifications import Notification


class InboxChannel:
    """The 'inbox' IS the notifications table plus the /notifications page
    that reads from it (Phase 5). By the time Channel.send() runs, the
    dispatcher has already persisted the notification (persist-then-send) --
    so there is nothing left for this channel to do. It exists as an
    explicit second Channel implementation because §1.8 calls for one: the
    page is a delivery mechanism, not just a debugging side effect."""

    def send(self, notification: Notification) -> None:
        pass
