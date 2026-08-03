import logging

logger = logging.getLogger(__name__)


class ConsoleEmailChannel:
    """Default, always-safe channel: logs instead of sending real email.
    Used whenever SMTP isn't fully configured, so notifications remain
    visible (in logs) rather than silently disappearing."""

    def send(self, title: str, body: str, kind: str) -> None:
        logger.info("EMAIL [%s] %s: %s", kind, title, body)


class SmtpEmailChannel:
    """Only constructed once host/sender/recipient are all present — see
    dispatch._build_channels()."""

    def __init__(self, host: str, port: int, username: str | None, password: str | None, sender: str, recipient: str) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.recipient = recipient

    def send(self, title: str, body: str, kind: str) -> None:
        import smtplib
        from email.message import EmailMessage

        message = EmailMessage()
        message["Subject"] = f"[{kind}] {title}"
        message["From"] = self.sender
        message["To"] = self.recipient
        message.set_content(body)

        with smtplib.SMTP(self.host, self.port, timeout=5) as smtp:
            smtp.starttls()
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(message)
