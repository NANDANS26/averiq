"""Alembic environment configuration for Averiq.

This file is executed by Alembic when running migrations (both online and
offline modes). It:
  1. Adds the project root to sys.path so that backend.* imports resolve.
  2. Loads the target metadata from the SQLAlchemy ORM Base so Alembic can
     auto-detect schema changes.
  3. Reads the database URL from the environment (DATABASE_SYNC_URL or
     DATABASE_URL), converting asyncpg → psycopg2 as required by Alembic.

Never store real credentials in this file or in alembic.ini.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Resolve project root so that `backend.*` imports work regardless of where
# `alembic` CLI is invoked from.
# ---------------------------------------------------------------------------
# infrastructure/alembic/env.py → go up two levels to the project root
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Import all ORM models so their tables are registered in Base.metadata before
# Alembic reads it.  The `noqa: F401` suppresses "imported but unused" warnings.
# ---------------------------------------------------------------------------
from backend.database import Base
from backend.config import settings
import backend.domain.models

# ---------------------------------------------------------------------------
# Alembic Config object
# ---------------------------------------------------------------------------
config = context.config

# Attach Python logging configuration from the ini file when one is present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata that Alembic compares against the live database schema.
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Helper: derive a psycopg2 (synchronous) URL for Alembic
# ---------------------------------------------------------------------------

def get_url() -> str:
    """Return the synchronous PostgreSQL URL used by Alembic.

    Settings loads the project-root .env file, so migrations use the same
    configuration source as the FastAPI application.
    """
    url = settings.DATABASE_SYNC_URL or settings.DATABASE_URL

    url = url.replace(
        "postgresql+asyncpg://",
        "postgresql+psycopg2://",
        1,
    )

    if url.startswith("postgresql://"):
        url = url.replace(
            "postgresql://",
            "postgresql+psycopg2://",
            1,
        )

    if not url:
        raise RuntimeError(
            "No database URL configured. Set DATABASE_SYNC_URL or DATABASE_URL."
        )

    return url


# ---------------------------------------------------------------------------
# Offline migrations (no live DB connection)
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """Emit migration SQL to stdout without connecting to a database.

    Useful for generating SQL scripts that a DBA can review before applying.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migrations (live DB connection)
# ---------------------------------------------------------------------------

def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    # Build configuration section, injecting the URL resolved from env
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # No connection pooling for migration runs
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
