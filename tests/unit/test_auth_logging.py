"""Unit tests for AuthAuditMiddleware (task 4.3, SEC-AUTH-007).

Tests verify that the middleware:
  1. Passes through 200 responses without emitting any log entry.
  2. Logs a structured entry (event="auth.failure") on 401 responses.
  3. Logs a structured entry (event="authz.rejection") on 403 responses.
  4. Sets actor="anonymous" when no Authorization header is present.
  5. Extracts the ``sub`` claim from a valid (unverified) JWT for the actor.
  6. Falls back to actor="anonymous" when the token is malformed.
  7. Correctly derives source_ip from X-Forwarded-For.

All tests are pure unit tests — no database, no Redis, no network calls.
The FastAPI test client is used purely in-process.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from backend.api.auth_logging import AuthAuditMiddleware, _extract_actor, _extract_source_ip

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bare_jwt(sub: str) -> str:
    """Build a structurally valid but *unsigned* JWT with a given sub.

    The token is not signed with any real key — jose.jwt.get_unverified_claims
    only needs the header and payload to be valid base64url JSON; it ignores
    the signature segment.
    """

    def _b64url(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = _b64url({"alg": "HS256", "typ": "JWT"})
    payload = _b64url({"sub": sub, "exp": 9999999999})
    signature = "fakesig"
    return f"{header}.{payload}.{signature}"


# ---------------------------------------------------------------------------
# Fixture: minimal FastAPI app with configurable route status codes
# ---------------------------------------------------------------------------


def _build_app(route_status: int) -> FastAPI:
    """Return a tiny FastAPI app whose only route returns *route_status*."""
    test_app = FastAPI()
    test_app.add_middleware(AuthAuditMiddleware)

    @test_app.get("/test")
    async def test_route() -> JSONResponse:
        return JSONResponse(status_code=route_status, content={"detail": "test"})

    return test_app


# ---------------------------------------------------------------------------
# Tests: passthrough for non-auth error codes
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_200_response_does_not_log() -> None:
    """AuthAuditMiddleware must NOT log anything for a successful 200 response."""
    app = _build_app(200)
    with patch("backend.api.auth_logging.logger") as mock_logger:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/test")

    assert response.status_code == 200
    mock_logger.warning.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_500_response_does_not_log() -> None:
    """AuthAuditMiddleware must NOT log anything for non-401/403 errors."""
    app = _build_app(500)
    with patch("backend.api.auth_logging.logger") as mock_logger:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/test")

    assert response.status_code == 500
    mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: 401 authentication failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_401_logs_auth_failure_event() -> None:
    """A 401 response must emit a structured log entry with event='auth.failure'."""
    app = _build_app(401)
    with patch("backend.api.auth_logging.logger") as mock_logger:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/test")

    assert response.status_code == 401
    mock_logger.warning.assert_called_once()
    call_args = mock_logger.warning.call_args
    assert call_args[0][0] == "auth.failure"
    kwargs = call_args[1]
    assert kwargs["reason"] == "authentication_failed"
    assert kwargs["status_code"] == 401
    assert kwargs["resource"] == "/test"
    assert kwargs["method"] == "GET"
    assert "timestamp" in kwargs


@pytest.mark.unit
@pytest.mark.asyncio
async def test_401_actor_is_anonymous_without_token() -> None:
    """When no Authorization header is sent, actor must be 'anonymous'."""
    app = _build_app(401)
    with patch("backend.api.auth_logging.logger") as mock_logger:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/test")  # no Authorization header

    assert response.status_code == 401
    kwargs = mock_logger.warning.call_args[1]
    assert kwargs["actor"] == "anonymous"


# ---------------------------------------------------------------------------
# Tests: 403 authorization rejection
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_403_logs_authz_rejection_event() -> None:
    """A 403 response must emit a structured log entry with event='authz.rejection'."""
    app = _build_app(403)
    with patch("backend.api.auth_logging.logger") as mock_logger:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/test")

    assert response.status_code == 403
    mock_logger.warning.assert_called_once()
    call_args = mock_logger.warning.call_args
    assert call_args[0][0] == "authz.rejection"
    kwargs = call_args[1]
    assert kwargs["reason"] == "insufficient_role"
    assert kwargs["status_code"] == 403


@pytest.mark.unit
@pytest.mark.asyncio
async def test_403_actor_is_anonymous_without_token() -> None:
    """When no Authorization header is sent on a 403, actor must be 'anonymous'."""
    app = _build_app(403)
    with patch("backend.api.auth_logging.logger") as mock_logger:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/test")

    assert response.status_code == 403
    kwargs = mock_logger.warning.call_args[1]
    assert kwargs["actor"] == "anonymous"


# ---------------------------------------------------------------------------
# Tests: actor extraction from JWT sub claim
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_401_actor_extracted_from_valid_jwt_sub() -> None:
    """When a Bearer JWT with a valid sub claim is present, actor = sub value."""
    app = _build_app(401)
    token = _make_bare_jwt(sub="user-abc-123")
    with patch("backend.api.auth_logging.logger") as mock_logger:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/test", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    kwargs = mock_logger.warning.call_args[1]
    assert kwargs["actor"] == "user-abc-123"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_401_actor_anonymous_on_malformed_token() -> None:
    """A malformed/garbage Bearer token must result in actor='anonymous'."""
    app = _build_app(401)
    with patch("backend.api.auth_logging.logger") as mock_logger:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/test", headers={"Authorization": "Bearer not.a.valid.jwt.at.all"}
            )

    assert response.status_code == 401
    kwargs = mock_logger.warning.call_args[1]
    assert kwargs["actor"] == "anonymous"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_403_actor_extracted_from_valid_jwt_sub() -> None:
    """On 403 with a valid (unverified) JWT, actor = sub claim value."""
    app = _build_app(403)
    token = _make_bare_jwt(sub="operator-999")
    with patch("backend.api.auth_logging.logger") as mock_logger:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/test", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    kwargs = mock_logger.warning.call_args[1]
    assert kwargs["actor"] == "operator-999"


# ---------------------------------------------------------------------------
# Tests: source_ip extraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_source_ip_from_x_forwarded_for() -> None:
    """source_ip must be extracted from the first address in X-Forwarded-For."""
    app = _build_app(401)
    with patch("backend.api.auth_logging.logger") as mock_logger:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/test",
                headers={"X-Forwarded-For": "203.0.113.5, 10.0.0.1"},
            )

    assert response.status_code == 401
    kwargs = mock_logger.warning.call_args[1]
    assert kwargs["source_ip"] == "203.0.113.5"


# ---------------------------------------------------------------------------
# Tests: helper function unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_actor_no_header() -> None:
    """_extract_actor returns 'anonymous' when no Authorization header is set."""
    mock_request = MagicMock()
    mock_request.headers = {}
    assert _extract_actor(mock_request) == "anonymous"


@pytest.mark.unit
def test_extract_actor_bearer_with_valid_sub() -> None:
    """_extract_actor returns the sub claim from a structurally valid JWT."""
    token = _make_bare_jwt(sub="test-user-xyz")
    mock_request = MagicMock()
    mock_request.headers = {"Authorization": f"Bearer {token}"}
    assert _extract_actor(mock_request) == "test-user-xyz"


@pytest.mark.unit
def test_extract_actor_malformed_token() -> None:
    """_extract_actor returns 'anonymous' for a token that cannot be decoded."""
    mock_request = MagicMock()
    mock_request.headers = {"Authorization": "Bearer garbage"}
    assert _extract_actor(mock_request) == "anonymous"


@pytest.mark.unit
def test_extract_actor_bearer_no_token_value() -> None:
    """_extract_actor returns 'anonymous' when Bearer prefix is present but token is empty."""
    mock_request = MagicMock()
    mock_request.headers = {"Authorization": "Bearer "}
    assert _extract_actor(mock_request) == "anonymous"


@pytest.mark.unit
def test_extract_source_ip_forwarded_for() -> None:
    """_extract_source_ip returns the first IP from X-Forwarded-For."""
    mock_request = MagicMock()
    mock_request.headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}
    mock_request.client = None
    assert _extract_source_ip(mock_request) == "1.2.3.4"


@pytest.mark.unit
def test_extract_source_ip_fallback_to_client_host() -> None:
    """_extract_source_ip falls back to request.client.host when no forwarded header."""
    mock_request = MagicMock()
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "192.168.1.1"
    assert _extract_source_ip(mock_request) == "192.168.1.1"


@pytest.mark.unit
def test_extract_source_ip_unknown_when_no_client() -> None:
    """_extract_source_ip returns 'unknown' when no forwarded header and no client."""
    mock_request = MagicMock()
    mock_request.headers = {}
    mock_request.client = None
    assert _extract_source_ip(mock_request) == "unknown"
