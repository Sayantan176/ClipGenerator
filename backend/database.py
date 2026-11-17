"""
backend/database.py

Async SQLAlchemy engine, session factory, declarative Base,
`get_db` FastAPI dependency, and `init_db` startup helper.

Uses aiosqlite as the async driver for SQLite.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from backend.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Declarative base — all ORM models inherit from this
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Shared declarative base for all SQLAlchemy ORM models."""
    __allow_unmapped__ = True


# ---------------------------------------------------------------------------
# Engine & session factory (module-level singletons, created lazily)
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    """Return the module-level async engine, creating it on first call."""
    global _engine
    if _engine is None:
        settings = get_settings()

        # Ensure the directory that will hold the .db file exists
        db_path = Path(settings.database_url.removeprefix("sqlite+aiosqlite:///"))
        db_path.parent.mkdir(parents=True, exist_ok=True)

        _engine = create_async_engine(
            settings.database_url,
            # echo=True logs every SQL statement — useful during development,
            # but noisy in production.  Tie it to DEBUG level via settings.
            echo=settings.db_echo,
            # aiosqlite supports only a single connection at a time per file,
            # so pool_size does not apply.  We use NullPool for simplicity:
            # each session opens/closes its own connection.
            connect_args={"check_same_thread": False},
        )
        logger.info("Async SQLAlchemy engine created: %s", settings.database_url)

    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the module-level async session factory, creating it on first call."""
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            bind=_get_engine(),
            class_=AsyncSession,
            # expire_on_commit=False keeps ORM objects usable after a commit
            # without issuing extra SELECT statements — important in async code.
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _async_session_factory


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """
    Create all tables that do not yet exist in the database.

    Call this once at application startup (e.g. in a FastAPI lifespan handler).
    It is idempotent — safe to run on every restart.
    """
    # Import models here so that their classes are registered on Base.metadata
    # before we call create_all.  The import must happen *after* Base is defined
    # above to avoid circular-import issues.
    import backend.models  # noqa: F401  (side-effect import)

    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database tables verified / created successfully.")


async def close_db() -> None:
    """
    Dispose of the engine connection pool.

    Call this during application shutdown to release file handles gracefully.
    """
    global _engine, _async_session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("Async SQLAlchemy engine disposed.")
        _engine = None
        _async_session_factory = None


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a transactional ``AsyncSession``.

    Usage in a route::

        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            ...

    The session is automatically committed on success and rolled back on any
    unhandled exception, then closed in both cases.
    """
    session_factory = _get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
