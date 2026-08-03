"""
Declarative base and the shared sync-tracking mixin.

Per the SPOP architecture doc, every entity that can be created or edited
on a device (and therefore needs to survive an offline/online sync cycle)
must carry:
    - id               : UUID primary key (never an auto-increment int —
                          IDs must be generatable offline, on-device,
                          without a round-trip to the server)
    - device_id        : which physical device created/last-touched the row
    - created_by_id    : which user performed the action (FK to users)
    - created_at       : server-assigned creation timestamp
    - updated_at       : server-assigned last-update timestamp
    - version          : monotonically increasing integer, incremented on
                          every update — used for optimistic-concurrency /
                          conflict detection during sync
    - sync_status      : lifecycle of this row from the sync engine's POV

Getting this mixin right now avoids retrofitting these columns onto ten
already-populated tables later.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""

    pass


class SyncStatus(str, enum.Enum):
    """
    Lifecycle status of a record from the offline-sync engine's
    perspective. Set by the client when queuing a write, confirmed by the
    server once persisted.
    """

    PENDING = "pending"   # created/edited on-device, not yet acknowledged by server
    SYNCED = "synced"     # confirmed persisted server-side
    CONFLICT = "conflict"  # server detected a version conflict; needs resolution
    FAILED = "failed"     # sync attempt failed (validation error, etc.)


def _utcnow() -> datetime:
    """Timezone-aware UTC 'now' — never use naive datetimes for sync timestamps."""
    return datetime.now(timezone.utc)


def as_aware_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip even for DateTime(timezone=True)
    columns — every stored datetime here is UTC by convention, so a naive
    value read back is always treated as UTC rather than the local zone."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class SyncedEntityMixin:
    """
    Mixin for any table whose rows can originate offline on a device and
    must be reconciled with the server via the sync engine.

    Do NOT use this mixin for purely server-side reference/config tables
    that are never created on-device (e.g. a static list of currencies).
    """

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Client-generatable UUID (string form, portable across Postgres/SQLite) — never an auto-increment integer.",
    )

    device_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Identifier of the device that created/last touched this row.",
    )

    created_by_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id"),
        nullable=False,
        comment="Which user performed the action.",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        comment="Incremented on every update; used for sync conflict detection.",
    )

    sync_status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus, name="sync_status"),
        default=SyncStatus.SYNCED,
        nullable=False,
        comment="SYNCED when written directly server-side; PENDING when queued from a device.",
    )


@event.listens_for(SyncedEntityMixin, "before_update", propagate=True)
def _bump_version(_mapper: object, _connection: object, target: SyncedEntityMixin) -> None:
    """Single source of truth for version increments — conflict detection on
    write endpoints compares a client-supplied expected_version against this
    value, so every mutation must go through this listener rather than
    endpoints setting `version` themselves."""
    target.version += 1
