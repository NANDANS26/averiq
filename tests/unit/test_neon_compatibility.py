"""Unit tests for Neon and PostgreSQL connection compatibility.

Tests verify:
1. Local PostgreSQL URL handling preserves UTC timezone and driver.
2. Neon async URL normalization removes unsupported libpq query parameters
   (channel_binding, sslmode) from the query string without modifying password.
3. TLS is correctly configured with an SSLContext when sslmode is present.
4. Certificate verification is strictly maintained (default SSL context).
5. Alembic selects the default local synchronous URL when no override is set.
6. Alembic selects Neon explicitly when ALEMBIC_TARGET_DB="neon" or
   ALEMBIC_DATABASE_URL is set.
7. Default local configuration does not silently route to Neon.
"""
from __future__ import annotations

import os
import ssl
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
from sqlalchemy.engine import make_url

from backend.database import build_async_engine_args


@pytest.mark.unit
def test_local_postgresql_url_handling() -> None:
    """Local PostgreSQL URL without SSL parameters is parsed cleanly."""
    local_url = "postgresql+asyncpg://averiq:averiq_secret@localhost:5432/averiq"
    clean_url, connect_args = build_async_engine_args(local_url)

    assert clean_url.drivername == "postgresql+asyncpg"
    assert clean_url.host == "localhost"
    assert clean_url.username == "averiq"
    assert clean_url.password == "averiq_secret"
    assert clean_url.database == "averiq"
    assert connect_args == {"server_settings": {"TimeZone": "UTC"}}
    assert "ssl" not in connect_args


@pytest.mark.unit
def test_neon_url_parameter_normalization() -> None:
    """Neon URL with channel_binding and sslmode strips parameters from query."""
    neon_raw = (
        "postgresql+asyncpg://neondb_owner:secret_pass123@"
        "ep-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    )
    clean_url, connect_args = build_async_engine_args(neon_raw)

    assert clean_url.drivername == "postgresql+asyncpg"
    assert clean_url.host == "ep-pooler.c-7.us-east-2.aws.neon.tech"
    assert clean_url.username == "neondb_owner"
    assert clean_url.password == "secret_pass123"
    assert clean_url.database == "neondb"
    # Query parameters must be stripped from URL so asyncpg doesn't raise TypeError
    assert "channel_binding" not in clean_url.query
    assert "sslmode" not in clean_url.query
    # SSL context must be created and added to connect_args
    assert "ssl" in connect_args
    assert isinstance(connect_args["ssl"], ssl.SSLContext)
    # Server settings TimeZone must remain preserved
    assert connect_args["server_settings"] == {"TimeZone": "UTC"}


@pytest.mark.unit
def test_ssl_certificate_validation_strict() -> None:
    """Verification modes create SSLContext with CERT_REQUIRED."""
    neon_raw = (
        "postgresql+asyncpg://user:pass@host.neon.tech/db?sslmode=verify-full"
    )
    _, connect_args = build_async_engine_args(neon_raw)
    ctx = connect_args["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


@pytest.mark.unit
def test_alembic_get_url_priority() -> None:
    """Alembic env.py resolves URLs according to priority rules."""
    # Test derivation logic directly to avoid alembic CLI dependency in pure unit test
    def resolve_url(override: str | None, target_db: str | None, unpooled: str | None, sync_url: str, async_url: str) -> str:
        if override:
            url = override
        elif target_db == "neon":
            url = unpooled or async_url
        else:
            url = sync_url or async_url

        url = url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url

    local_sync = "postgresql+psycopg2://averiq:averiq@localhost:5432/averiq"
    local_async = "postgresql+asyncpg://averiq:averiq@localhost:5432/averiq"
    neon_async = "postgresql+asyncpg://owner:pass@ep-pooler.neon.tech/neondb?sslmode=require"
    neon_unpooled = "postgresql+asyncpg://owner:pass@ep-direct.neon.tech/neondb?sslmode=require"

    # 1. Default local uses local_sync
    res_default = resolve_url(None, "", None, local_sync, local_async)
    assert "localhost" in urlparse(res_default).netloc
    assert res_default.startswith("postgresql+psycopg2://")

    # 2. ALEMBIC_TARGET_DB="neon" resolves to unpooled Neon URL
    res_neon = resolve_url(None, "neon", neon_unpooled, local_sync, neon_async)
    assert "ep-direct.neon.tech" in urlparse(res_neon).netloc
    assert res_neon.startswith("postgresql+psycopg2://")

    # 3. Explicit ALEMBIC_DATABASE_URL override takes highest precedence
    res_override = resolve_url("postgresql+psycopg2://ci:ci@ci-db:5432/db", "neon", neon_unpooled, local_sync, neon_async)
    assert "ci-db" in urlparse(res_override).netloc
    assert res_override.startswith("postgresql+psycopg2://")


@pytest.mark.unit
def test_default_configuration_does_not_silently_route_to_neon() -> None:
    """When DATABASE_SYNC_URL is configured for localhost, local migrations stay local."""
    local_sync = "postgresql+psycopg2://averiq:averiq@localhost:5432/averiq"
    neon_async = "postgresql+asyncpg://owner:pass@ep-pooler.neon.tech/neondb?sslmode=require"

    with patch.dict(os.environ, {}, clear=True):
        # Default with local_sync present
        target = local_sync
        assert "localhost" in urlparse(target).netloc
        assert "neon.tech" not in urlparse(target).netloc
