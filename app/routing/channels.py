from typing import Protocol

from app.domain.notifications import Notification


class Channel(Protocol):
    def send(self, notification: Notification) -> None: ...


# A SlackChannel implementation would live at routing/slack_channel.py,
# implementing this same Channel protocol. Not built -- the spec
# de-prioritized real integrations explicitly (Takehome.md "Out of scope").
