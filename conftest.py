"""Root conftest.py — injects minimal environment variables required for all tests.

Several backend modules (backend.config, backend.database) validate required env vars
at import time via pydantic-settings.  Unit and integration tests that only use mocked
sessions do not need a real database, but they still trigger these imports.

This file sets the bare minimum env vars so the import chain succeeds without
requiring a running database or any real secrets.
"""

from __future__ import annotations

import os

# Set required env vars BEFORE any backend module is imported.
# These values are test-only placeholders; they are never used for real I/O
# in unit/mock-based tests.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://averiq:test@localhost:5432/averiq_test",
)
os.environ.setdefault(
    "APP_SECRET_KEY",
    "0" * 64,  # 64-char placeholder hex string (never used in mock-based tests)
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
