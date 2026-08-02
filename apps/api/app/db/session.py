"""
Database engine and session factory.

Uses SQLAlchemy 2.x's synchronous engine for now (Phase 0-2 workloads are
simple CRUD; move to the async engine + asyncpg later if request volume
demands it — don't add that complexity before it's needed).
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # avoids errors from stale connections after DB restarts
    echo=settings.debug,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a database session and guarantees it is
    closed after the request, even if an exception is raised.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
