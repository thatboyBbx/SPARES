from typing import Protocol


class NotificationChannel(Protocol):
    """A best-effort outbound channel for a notification that has already
    been persisted. Implementations must never raise in a way that could
    roll back the caller's transaction — dispatch_notification() only ever
    invokes send() after commit."""

    def send(self, title: str, body: str, kind: str) -> None: ...
