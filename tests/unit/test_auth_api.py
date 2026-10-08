"""Unit tests for POST /api/v1/auth/login and logout endpoints."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.config import settings
from backend.database import get_session as backend_get_session
from backend.domain.auth_utils import hash_password
from backend.domain.models import User
from backend.main import app


@pytest.fixture
def mock_merchant_id():
    return uuid.uuid4()


@pytest.fixture
def mock_user(mock_merchant_id):
    user = User(
        id=uuid.uuid4(),
        merchant_id=mock_merchant_id,
        email="operator@averiq.demo",
        password_hash=hash_password("valid_password_123"),
        role="OPERATOR",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    return user


@pytest.mark.asyncio
async def test_login_valid_credentials(mock_user):
    transport = ASGITransport(app=app)
    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_user
    mock_session.execute.return_value = mock_res
    app.dependency_overrides[backend_get_session] = lambda: mock_session

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/auth/login",
            json={"email": "operator@averiq.demo", "password": "valid_password_123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["role"] == "OPERATOR"
        assert data["email"] == "operator@averiq.demo"
        assert data["merchant_id"] == str(mock_user.merchant_id)


@pytest.mark.asyncio
async def test_login_invalid_password(mock_user):
    transport = ASGITransport(app=app)
    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_user
    mock_session.execute.return_value = mock_res
    app.dependency_overrides[backend_get_session] = lambda: mock_session

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/auth/login",
            json={"email": "operator@averiq.demo", "password": "wrong_password"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid email or password"


@pytest.mark.asyncio
async def test_login_unknown_user():
    transport = ASGITransport(app=app)
    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_res
    app.dependency_overrides[backend_get_session] = lambda: mock_session

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/auth/login",
            json={"email": "unknown@averiq.demo", "password": "password_123"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid email or password"


@pytest.mark.asyncio
async def test_login_system_role_rejected(mock_user):
    mock_user.role = "SYSTEM"
    transport = ASGITransport(app=app)
    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_user
    mock_session.execute.return_value = mock_res
    app.dependency_overrides[backend_get_session] = lambda: mock_session

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/auth/login",
            json={"email": "operator@averiq.demo", "password": "valid_password_123"},
        )
        assert response.status_code == 403
