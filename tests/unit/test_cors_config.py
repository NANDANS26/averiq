"""Unit tests for CORS and CSRF protection configuration (task 20.1).

Tests:
  1. CORSMiddleware is registered in the FastAPI app.
  2. allow_credentials=True is set (required for Bearer token auth).
  3. allow_origins does NOT include the "*" wildcard — origins are read from settings.

The tests mock the ``opentelemetry`` module (not installed in this environment)
so that ``backend.main`` can be imported for middleware inspection.

Requirements: SEC-AUTH-001, NFR-SEC-005
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings


# ---------------------------------------------------------------------------
# Module-level setup: stub the opentelemetry package so that importing
# backend.main (which transitively imports backend.webhook_processor.handler)
# does not fail with ModuleNotFoundError in environments where the optional
# opentelemetry SDK is not installed.
# ---------------------------------------------------------------------------

def _stub_opentelemetry() -> None:
    """Inject minimal opentelemetry stubs into sys.modules if not already present."""
    if "opentelemetry" in sys.modules:
        return  # already installed — no stub needed

    # Create stub packages/modules
    otel = types.ModuleType("opentelemetry")
    otel_metrics = types.ModuleType("opentelemetry.metrics")

    # Provide a get_meter function that returns a mock meter
    mock_meter = MagicMock()
    mock_meter.create_counter.return_value = MagicMock()
    mock_meter.create_histogram.return_value = MagicMock()
    otel_metrics.get_meter = MagicMock(return_value=mock_meter)  # type: ignore[attr-defined]

    sys.modules["opentelemetry"] = otel
    sys.modules["opentelemetry.metrics"] = otel_metrics


_stub_opentelemetry()

# Now it is safe to import backend.main
from backend.main import app  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: find CORSMiddleware in the app's middleware stack
# ---------------------------------------------------------------------------

def _get_cors_middleware() -> object | None:
    """Walk the middleware stack and return the CORSMiddleware entry, if present."""
    for middleware in app.user_middleware:
        # Starlette stores middlewares as Middleware(cls, **kwargs) named-tuples
        if middleware.cls is CORSMiddleware:
            return middleware
    return None


# ---------------------------------------------------------------------------
# Test 1: CORSMiddleware is registered in the app
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cors_middleware_is_registered() -> None:
    """CORSMiddleware must be registered in the FastAPI application middleware stack.

    Without CORS middleware, browser-based clients (the React frontend) cannot
    make cross-origin requests to the API.
    """
    cors_mw = _get_cors_middleware()
    assert cors_mw is not None, (
        "CORSMiddleware must be registered in backend.main.app. "
        "Add app.add_middleware(CORSMiddleware, ...) in main.py."
    )


# ---------------------------------------------------------------------------
# Test 2: allow_credentials=True is set
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cors_allow_credentials_is_true() -> None:
    """CORSMiddleware must have allow_credentials=True for Bearer token auth.

    The frontend sends Authorization: Bearer <token> headers. CORS preflight
    requests require allow_credentials=True for credentialed requests to succeed.
    """
    cors_mw = _get_cors_middleware()
    assert cors_mw is not None, "CORSMiddleware not registered — see test_cors_middleware_is_registered"

    kwargs = cors_mw.kwargs
    assert kwargs.get("allow_credentials") is True, (
        "CORSMiddleware must set allow_credentials=True to support Bearer token auth"
    )


# ---------------------------------------------------------------------------
# Test 3: allow_origins does NOT include the "*" wildcard
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cors_no_wildcard_origin() -> None:
    """allow_origins must NOT contain "*" when allow_credentials=True.

    Browsers block credentialed requests to wildcard origins (CORS spec §3.2.5).
    Origins must be an explicit allowlist read from settings.ALLOWED_CORS_ORIGINS.
    """
    cors_mw = _get_cors_middleware()
    assert cors_mw is not None, "CORSMiddleware not registered — see test_cors_middleware_is_registered"

    kwargs = cors_mw.kwargs
    allow_origins: list[str] = kwargs.get("allow_origins", [])

    assert "*" not in allow_origins, (
        "CORSMiddleware must NOT use '*' wildcard origin when allow_credentials=True. "
        "Use settings.ALLOWED_CORS_ORIGINS to specify an explicit allowlist."
    )

    # Verify origins are sourced from settings (not hardcoded or empty)
    assert len(allow_origins) > 0, (
        "allow_origins must be a non-empty list from settings.ALLOWED_CORS_ORIGINS"
    )
    assert allow_origins == settings.ALLOWED_CORS_ORIGINS, (
        "allow_origins must match settings.ALLOWED_CORS_ORIGINS exactly"
    )
