"""Async database session factory for Averiq.

Provides the SQLAlchemy async engine and a session factory used across all
domain repositories. The session is injected into FastAPI endpoints via the
``get_session`` dependency.

Usage (FastAPI dependency):

    from backend.database import get_session

    @router.get("/example")
    async def example(session: AsyncSession = Depends(get_session)) -> ...:
        ...

Usage (Celery task / standalone):

    from backend.database import async_session_factory

    async with async_session_factory() as session:
        async with session.begin():
            ...
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase

from backend.config import settings

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

# Use NullPool for reliable execution across Celery tasks and FastAPI requests
# where different asyncio event loops are created per task invocation.
_engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,               # Set to True for SQL query logging in dev
    poolclass=NullPool,
    # Ensure all datetime values include timezone info (UTC)
    connect_args={"server_settings": {"TimeZone": "UTC"}},
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

# async_sessionmaker is the SQLAlchemy 2.x replacement for sessionmaker + AsyncSession
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,   # Keep objects accessible after session.commit()
    autocommit=False,
    autoflush=False,
)

# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models.

    All SQLAlchemy ORM models in ``backend/domain/models.py`` inherit from this class.
    The ``metadata`` attribute on this base is used by Alembic for auto-generating
    migrations.
    """

    # Let mypy know this is a valid SQLAlchemy declarative base
    __abstract__: bool = True


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_session() -> AsyncGenerator[AsyncSession, Any]:
    """Yield an ``AsyncSession`` for use as a FastAPI dependency.

    The session is committed if no exception occurs during the request;
    otherwise it is rolled back. The session is always closed on exit.

    Example::

        @router.post("/example")
        async def create_example(
            data: ExampleIn,
            session: AsyncSession = Depends(get_session),
        ) -> ExampleOut:
            obj = Example(**data.model_dump())
            session.add(obj)
            await session.flush()
            return ExampleOut.model_validate(obj)
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


async def create_all_tables() -> None:
    """Create all tables defined in the ORM metadata.

    Intended for use in integration tests only. Production table creation
    is managed by Alembic migrations (``make migrate``).
    """
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all_tables() -> None:
    """Drop all tables defined in the ORM metadata.

    Intended for use in integration tests only. Use with extreme caution.
    """
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def dispose_engine() -> None:
    """Dispose the engine and close all pooled connections.

    Call during application shutdown (FastAPI lifespan on_shutdown).
    """
    await _engine.dispose()


def get_engine() -> AsyncEngine:
    """Return the module-level async engine (for Alembic env.py and health checks)."""
    return _engine
