"""Averiq FastAPI application entrypoint.

Creates the FastAPI app, configures middleware, registers routers, and
exposes the ASGI app object used by Uvicorn.

Starting the development server::

    uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

Production (inside Docker)::

    uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1

Architecture note: all routers are imported and mounted here. No business
logic lives in this module. The Policy_Engine is a mandatory call-graph gate
between the Decision_Engine and Execution_Engine; that structural constraint
is enforced in the router/dependency layer, not here.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.auth_logging import AuthAuditMiddleware
from backend.api.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    conflict_error_handler,
    forbidden_error_handler,
    not_found_error_handler,
)
from backend.config import settings
from backend.database import dispose_engine

# ---------------------------------------------------------------------------
# Structured logging setup
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.LOG_LEVEL)
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, Any]:
    """Manage application startup and shutdown lifecycle.

    Startup: log configuration summary (no secret values), initialise OTel.
    Shutdown: dispose the SQLAlchemy engine and release DB connections.
    """
    logger.info(
        "averiq.startup",
        operating_mode=settings.OPERATING_MODE,
        failure_lab_enabled=settings.ENABLE_FAILURE_LAB,
        log_level=settings.LOG_LEVEL,
    )
    yield
    await dispose_engine()
    logger.info("averiq.shutdown")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Averiq — AI Revenue Recovery Decision Engine",
    version="0.1.0",
    description=(
        "Averiq treats payment failures as decision problems. "
        "AI proposes → Policy controls → Infrastructure executes → "
        "Verification confirms → Metrics prove."
    ),
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------
# No wildcard when credentials are included. Origins are read from env.

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Correlation-ID"],
    expose_headers=["X-Request-ID", "X-Correlation-ID", "Retry-After"],
)

# ---------------------------------------------------------------------------
# Auth audit logging middleware (SEC-AUTH-007)
# ---------------------------------------------------------------------------
# Registered before the correlation middleware so that it runs *after* it
# in Starlette's LIFO dispatch order — the correlation middleware therefore
# binds request_id/correlation_id before this middleware emits log entries.

app.add_middleware(AuthAuditMiddleware)

# ---------------------------------------------------------------------------
# Domain exception handlers
# ---------------------------------------------------------------------------
# These translate domain-layer exceptions (raised by repositories and services)
# into structured HTTP responses with consistent error bodies (API-007).
# ForbiddenError covers cross-merchant access violations (SEC-ISOL-002) and
# is logged as a structured authz event (SEC-AUTH-007).

app.add_exception_handler(ForbiddenError, forbidden_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(NotFoundError, not_found_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(ConflictError, conflict_error_handler)  # type: ignore[arg-type]

# HTTP 422 validation error handler — standardises field-level validation errors
# (API-006, API-007) to the shared {"error": ..., "detail": [...], "request_id": ...} shape.
from fastapi.exceptions import RequestValidationError  # noqa: E402


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = structlog.contextvars.get_contextvars().get("request_id", "")
    return JSONResponse(
        status_code=422,
        content={"error": "VALIDATION_ERROR", "detail": exc.errors(), "request_id": request_id},
    )

# ---------------------------------------------------------------------------
# Request correlation middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def correlation_middleware(request: Request, call_next: Any) -> Response:
    """Attach request_id and correlation_id to every request.

    - ``request_id``: a fresh UUID generated for every HTTP request
    - ``correlation_id``: propagated from the ``X-Correlation-ID`` header if
      present; otherwise equals the request_id

    Both IDs are bound to the structlog context so every log entry within
    the request lifecycle includes them. They are also returned in response
    headers for client-side tracing.
    """
    request_id = str(uuid.uuid4())
    correlation_id = request.headers.get("X-Correlation-ID") or request_id

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        correlation_id=correlation_id,
        method=request.method,
        path=request.url.path,
    )

    start_time = time.monotonic()
    try:
        response: Response = await call_next(request)
    except Exception as exc:
        logger.exception("request.unhandled_error", exc_info=exc)
        response = JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "detail": [],
                "request_id": request_id,
            },
        )

    duration_ms = (time.monotonic() - start_time) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Correlation-ID"] = correlation_id

    logger.info(
        "request.complete",
        status_code=response.status_code,
        duration_ms=round(duration_ms, 2),
    )

    structlog.contextvars.clear_contextvars()
    return response


# ---------------------------------------------------------------------------
# Operating mode banner middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def operating_mode_middleware(request: Request, call_next: Any) -> Response:
    """Inject the operating mode into every response header.

    Frontend components read ``X-Operating-Mode`` to display the persistent
    SIMULATED (blue) / REAL_TEST_MODE (amber) banner on every page.
    """
    response: Response = await call_next(request)
    response.headers["X-Operating-Mode"] = settings.OPERATING_MODE
    return response


# ---------------------------------------------------------------------------
# Standard error responses
# ---------------------------------------------------------------------------


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a standardised 404 response body."""
    return JSONResponse(
        status_code=404,
        content={
            "error": "Not found",
            "detail": [],
            "request_id": structlog.contextvars.get_contextvars().get("request_id", ""),
        },
    )


@app.exception_handler(405)
async def method_not_allowed_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a standardised 405 response body."""
    return JSONResponse(
        status_code=405,
        content={
            "error": "Method not allowed",
            "detail": [],
            "request_id": structlog.contextvars.get_contextvars().get("request_id", ""),
        },
    )


# ---------------------------------------------------------------------------
# Health and Readiness endpoints (no auth required — used by probes and operators)
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    tags=["health"],
    summary="Liveness check",
    response_description="Service is alive",
)
async def health_check() -> dict[str, Any]:
    """Return a minimal liveness response."""
    return {
        "status": "ok",
        "version": app.version,
        "environment": settings.ENVIRONMENT,
        "operating_mode": settings.OPERATING_MODE,
    }


@app.get(
    "/readiness",
    tags=["health"],
    summary="Readiness check for dependencies",
    response_description="Dependency readiness status",
)
async def readiness_check(response: Response) -> Any:
    """Verify readiness of PostgreSQL, Redis, and Model Artifacts."""
    from backend.api.system import check_readiness
    res, status_code = await check_readiness()
    response.status_code = status_code
    return res


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------
# Routers are imported here once they are implemented (tasks 4.x, 6.x, etc.).
# Each router is prefixed with /api/v1 and tagged for OpenAPI grouping.

# Webhook ingestion router (task 6.1)
# POST /api/v1/webhooks/razorpay/{merchant_id}
# No authentication required — HMAC signature verification IS the auth mechanism.
# SEC-AUTH-001: webhook endpoint is explicitly exempt from Bearer token auth.
from backend.webhook_processor.handler import router as webhook_router  # noqa: E402

app.include_router(webhook_router, prefix="/api/v1/webhooks", tags=["webhooks"])

# Analytics router (task 8.2)
# GET /api/v1/analytics/revenue — revenue risk summary (FR-RISK-006, FR-RISK-007)
from backend.api.analytics import router as analytics_router  # noqa: E402

app.include_router(analytics_router, prefix="/api/v1/analytics", tags=["analytics"])

# Human review queue router (task 12.3)
# GET  /api/v1/review                            — pending review queue (OPERATOR+)
# POST /api/v1/interventions/{id}/approve        — approve intervention (OPERATOR+)
# POST /api/v1/interventions/{id}/reject         — reject intervention (OPERATOR+)
# Requirements: FR-POLICY-007
from backend.api.review import router as review_router  # noqa: E402

app.include_router(review_router, prefix="/api/v1", tags=["review"])

# Model lifecycle management router (task 10.7)
# POST /api/v1/models/{model_id}/promote — promote model through lifecycle stages
# Requirements: FR-MODEL-009, AI-MODEL-001, AI-MODEL-003
from backend.api.models import router as models_router  # noqa: E402

app.include_router(models_router, prefix="/api/v1", tags=["models"])

# Cases and interventions router (task 16.1)
# GET /api/v1/cases                           — paginated cursor list
# GET /api/v1/cases/{case_id}                 — full case detail
# GET /api/v1/cases/{case_id}/trace           — decision trace
# GET /api/v1/cases/{case_id}/audit           — audit history (AUD-006 safe)
# GET /api/v1/cases/{case_id}/interventions   — interventions for case
# GET /api/v1/interventions/{intervention_id} — single intervention detail
# Requirements: FR-STATE-001, AUD-002, AUD-006, API-001, API-002, API-004
from backend.api.cases import router as cases_router  # noqa: E402

app.include_router(cases_router, prefix="/api/v1", tags=["cases"])

# Policy lifecycle router (task 16.2)
# GET  /api/v1/policies              — list policies (VIEWER+)
# GET  /api/v1/policies/active       — active policy (VIEWER+)
# GET  /api/v1/policies/{id}         — single policy detail (VIEWER+)
# POST /api/v1/policies              — create draft policy (ADMIN)
# POST /api/v1/policies/{id}/activate — promote to active (ADMIN)
# Requirements: FR-POLICY-012, FR-POLICY-013
from backend.api.policy import router as policy_router  # noqa: E402

app.include_router(policy_router, prefix="/api/v1", tags=["policies"])

# System status router (task 16.2)
# GET /api/v1/status — operating mode, version, failure lab flag (no auth required)
# Requirements: API-002, INT-RAZORPAY-009, INT-RAZORPAY-010
from backend.api.system import router as system_router  # noqa: E402

app.include_router(system_router, prefix="/api/v1", tags=["system"])

# Attribution and metrics router (task 15.2)
# GET /api/v1/analytics/attribution — attribution breakdown (FR-ATTR-003)
# GET /api/v1/analytics/metrics     — named metrics summary (FR-ATTR-004)
# Requirements: FR-ATTR-003, FR-ATTR-004
from backend.api.attribution import router as attribution_router  # noqa: E402

app.include_router(attribution_router, prefix="/api/v1/analytics", tags=["analytics"])

# Failure Lab router (task 16.3)
# POST /api/v1/simulation/failures/{scenario} — trigger failure scenario
# GET  /api/v1/simulation/failures            — list available scenarios
# The router is always registered, but every endpoint calls _require_failure_lab()
# which returns HTTP 404 when ENABLE_FAILURE_LAB=False (API-009).
# This avoids conditional import complexity while maintaining the 404 contract.
from backend.api.simulation import router as simulation_router  # noqa: E402

app.include_router(simulation_router, prefix="/api/v1", tags=["simulation"])

from backend.api.auth import router as auth_router  # noqa: E402

app.include_router(auth_router, prefix="/api/v1", tags=["auth"])

# Demo center router (Phase 6)
from backend.api.demo import router as demo_router  # noqa: E402

app.include_router(demo_router, prefix="/api/v1", tags=["demo"])

