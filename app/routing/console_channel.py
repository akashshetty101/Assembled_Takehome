from app.domain.notifications import Notification


class ConsoleChannel:
    """Formats as a chat message: [09:55:00] @lead_sam — <body>."""

    def send(self, notification: Notification) -> None:
        ts = notification.event_time.strftime("%H:%M:%S")
        if notification.recipient_kind == "channel":
            recipient = notification.recipient_target
        else:
            recipient = f"@{notification.recipient_target}"
        print(f"[{ts}] {recipient} — {notification.body}")
