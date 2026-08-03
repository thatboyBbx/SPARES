"""dispatch_notification() writes the Notification row (unchanged
pull-based behaviour every endpoint already relies on) and defers actually
calling outbound channels until after the enclosing transaction commits, via
a SQLAlchemy `after_commit` hook keyed off `session.info`. A channel that
raises can never roll back or delay the sale/receipt/etc. that triggered it,
because by the time channels run, that transaction has already committed."""
import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.notifications.base import NotificationChannel
from app.core.notifications.email import ConsoleEmailChannel, SmtpEmailChannel
from app.core.notifications.push import FcmPushChannel, NoopPushChannel
from app.models import Notification

logger = logging.getLogger(__name__)

_PENDING_KEY = "spop_pending_notification_sends"


def _build_channels() -> list[NotificationChannel]:
    settings = get_settings()
    if not settings.notifications_enabled:
        return []

    channels: list[NotificationChannel] = []

    if settings.smtp_host and settings.smtp_sender and settings.smtp_recipient:
        channels.append(SmtpEmailChannel(settings.smtp_host, settings.smtp_port, settings.smtp_username, settings.smtp_password, settings.smtp_sender, settings.smtp_recipient))
    else:
        channels.append(ConsoleEmailChannel())

    if settings.firebase_credentials_json:
        try:
            channels.append(FcmPushChannel(settings.firebase_credentials_json))
        except Exception:
            logger.exception("Failed to initialize FCM push channel; falling back to no-op")
            channels.append(NoopPushChannel())
    else:
        channels.append(NoopPushChannel())

    return channels


@event.listens_for(Session, "after_commit")
def _flush_pending_notification_sends(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    channels = _build_channels()
    for entry in pending:
        for channel in channels:
            try:
                channel.send(entry["title"], entry["body"], entry["kind"])
            except Exception:
                logger.exception("Notification channel %s failed for kind=%s", type(channel).__name__, entry["kind"])


def dispatch_notification(db: Session, *, title: str, body: str, kind: str) -> Notification:
    notification = Notification(title=title, body=body, kind=kind)
    db.add(notification)
    db.info.setdefault(_PENDING_KEY, []).append({"title": title, "body": body, "kind": kind})
    return notification
