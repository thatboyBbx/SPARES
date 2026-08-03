from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import as_aware_utc
from app.db.session import get_db
from app.models import RefreshToken, User, UserRole

settings = get_settings()
security = HTTPBearer(auto_error=False)
_pwd_context = CryptContext(schemes=["bcrypt"])


def _legacy_pbkdf2_hash(password: str) -> str:
    """The original Phase-0 hashing scheme. Kept only to verify (and
    transparently upgrade) any password hashed before the bcrypt switch —
    never used for new hashes."""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"sparepilot-pilot", 120_000).hex()


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        if _pwd_context.verify(password, stored_hash):
            return True
    except ValueError:
        pass  # not a bcrypt hash — fall through to the legacy check below
    return secrets.compare_digest(_legacy_pbkdf2_hash(password), stored_hash)


def verify_and_upgrade_password(user: User, password: str, db: Session) -> bool:
    """Verify a password and, if it matched via the legacy pbkdf2 scheme,
    transparently rehash it with bcrypt so it upgrades on next login."""
    if _pwd_context.identify(user.hashed_password) == "bcrypt":
        return _pwd_context.verify(password, user.hashed_password)
    if secrets.compare_digest(_legacy_pbkdf2_hash(password), user.hashed_password):
        user.hashed_password = hash_password(password)
        db.commit()
        return True
    return False


def create_access_token(subject: str, role: str, *, expires_minutes: int | None = None) -> str:
    expires_delta = timedelta(minutes=expires_minutes or settings.access_token_expire_minutes)
    expires_at = datetime.now(timezone.utc) + expires_delta
    payload = {"sub": subject, "role": role, "exp": expires_at}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, object]:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:  # pragma: no cover - exercised through API calls
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc


def _hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_refresh_token(db: Session, user_id: str, device_id: str | None = None) -> str:
    """Refresh tokens are high-entropy random secrets, not user-chosen
    passwords — sha256 is sufficient, no need for bcrypt's slow KDF here."""
    raw_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    db.add(RefreshToken(user_id=user_id, token_hash=_hash_refresh_token(raw_token), device_id=device_id, expires_at=expires_at))
    db.commit()
    return raw_token


def rotate_refresh_token(db: Session, raw_token: str) -> tuple[str, str, User]:
    """Validate + revoke the presented refresh token and issue a new
    access/refresh pair. Raises 401 on any invalid/expired/revoked token."""
    token_hash = _hash_refresh_token(raw_token)
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if not record or record.revoked_at is not None or as_aware_utc(record.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    user = db.get(User, record.user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    record.revoked_at = datetime.now(timezone.utc)
    db.commit()
    new_refresh_token = create_refresh_token(db, user.id, record.device_id)
    new_access_token = create_access_token(user.id, user.role.value)
    return new_access_token, new_refresh_token, user


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash_refresh_token(raw_token)))
    if record and record.revoked_at is None:
        record.revoked_at = datetime.now(timezone.utc)
        db.commit()


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    claims = decode_access_token(credentials.credentials)
    user = db.scalar(select(User).where(User.id == claims["sub"]))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def require_roles(*roles: UserRole):
    def dependency(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return dependency
