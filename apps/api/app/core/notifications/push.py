import logging

logger = logging.getLogger(__name__)


class NoopPushChannel:
    """Safe fallback when no Firebase credentials are configured."""

    def send(self, title: str, body: str, kind: str) -> None:
        pass


class FcmPushChannel:
    """firebase-admin is imported lazily inside __init__ so its cost (and
    its dependency) is only paid once real credentials are configured."""

    def __init__(self, credentials_json: str) -> None:
        import json

        import firebase_admin
        from firebase_admin import credentials

        cert = json.loads(credentials_json) if credentials_json.strip().startswith("{") else credentials_json
        cred = credentials.Certificate(cert)
        try:
            self._app = firebase_admin.get_app("spop")
        except ValueError:
            self._app = firebase_admin.initialize_app(cred, name="spop")

    def send(self, title: str, body: str, kind: str) -> None:
        from firebase_admin import messaging

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={"kind": kind},
            topic="spop-operators",
        )
        try:
            messaging.send(message, app=self._app)
        except Exception:
            logger.exception("FCM push send failed")
