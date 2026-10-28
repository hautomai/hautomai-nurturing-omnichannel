"""Database engine and session plumbing."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import DateTime, TypeDecorator, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings


class UTCDateTime(TypeDecorator):
    """Store naive UTC, always hand back timezone-aware UTC.

    Without this, SQLite writes aware datetimes with an offset suffix while
    Postgres asserts its own session timezone, and comparisons on "is this timer
    due?" become backend-dependent. Storing naive UTC makes the behaviour
    identical on both.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


_engine = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(settings: Settings | None = None):
    """Return a process-wide engine for the configured database URL."""
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        url = settings.database_url
        kwargs: dict[str, object] = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            # Tests and single-file local runs.
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in url:
                from sqlalchemy.pool import StaticPool

                # A shared in-memory database needs one connection for everyone.
                kwargs["poolclass"] = StaticPool
                kwargs.pop("connect_args", None)
        _engine = create_engine(url, **kwargs)
    return _engine


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(settings), autoflush=False, expire_on_commit=False, future=True
        )
    return _session_factory


def reset_engine() -> None:
    """Drop cached engine/session state. Used by tests between databases."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def create_all(settings: Settings | None = None) -> None:
    """Create every table. Development convenience; alembic owns production."""
    from app import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(bind=get_engine(settings))


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session scope for workers and scripts."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
