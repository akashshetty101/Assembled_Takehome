from typing import Protocol

from app.domain.notifications import Notification


class Channel(Protocol):
    def send(self, notification: Notification) -> None: ...

