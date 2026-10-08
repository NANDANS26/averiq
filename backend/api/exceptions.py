"""Custom exceptions and exception handlers for the Averiq API.

Defines domain-level exceptions that are raised by the service/repository
layer and translated to structured HTTP responses by registered handlers.

Exception hierarchy:
  AveriqError (base)
  ├── ForbiddenError      → HTTP 403
  ├── NotFoundError       → HTTP 404
  └── ConflictError       → HTTP 409

The handlers are registered on the FastAPI app in ``backend/main.py``.

All error responses follow the standard schema (API-007):

    {
        "error": "<ERROR_CODE>",
        "detail": [...],
        "request_id": "<uuid>"
    }

Requirements: SEC-ISOL-002 (ForbiddenError → HTTP 403)
"""

from __future__ import annotations

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class AveriqError(Exception):
    """Base class for all Averiq domain exceptions."""


class ForbiddenError(AveriqError):
    """Raised when an authenticated user attempts to access a resource that
    does not belong to their merchant, or when they lack sufficient permissions.

    The repository layer raises this for cross-merchant access violations
    (SEC-ISOL-002). The RBAC dependency layer raises HTTP 403 directly via
    FastAPI's HTTPException; this error is for the domain/service/repository
    layers that do not have direct access to the request context.

    Translated to HTTP 403 by ``forbidden_error_handler``.
    """

    def __init__(self, detail: str = "Access forbidden") -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(AveriqError):
    """Raised when a requested resource does not exist.

    Translated to HTTP 404 by ``not_found_error_handler``.
    """

    def __init__(self, detail: str = "Resource not found") -> None:
        super().__init__(detail)
        self.detail = detail


class ConflictError(AveriqError):
    """Raised when an operation would violate a uniqueness or state constraint.

    Examples:
      - Attempting to create a second active Policy for the same merchant.
      - Submitting a duplicate idempotency key with different parameters.

    Translated to HTTP 409 by ``conflict_error_handler``.
    """

    def __init__(self, detail: str = "Conflict") -> None:
        super().__init__(detail)
        self.detail = detail


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


def _get_request_id(request: Request) -> str:
    """Extract the request_id from the structlog context or response headers."""
    import structlog.contextvars

    return structlog.contextvars.get_contextvars().get(
        "request_id",
        request.headers.get("X-Request-ID", ""),
    )


async def forbidden_error_handler(request: Request, exc: ForbiddenError) -> JSONResponse:
    """Translate a ``ForbiddenError`` raised by the domain/repository layer to HTTP 403.

    This handler fires when the merchant-scoped repository detects a
    cross-merchant access attempt (SEC-ISOL-002). The structured log entry
    satisfies SEC-AUTH-007 (log every authorization rejection).

    Response body::

        {
            "error": "Forbidden",
            "detail": ["<human-readable reason>"],
            "request_id": "<uuid>"
        }
    """
    request_id = _get_request_id(request)
    logger.warning(
        "authz.forbidden",
        detail=exc.detail,
        method=request.method,
        path=request.url.path,
        request_id=request_id,
    )
    return JSONResponse(
        status_code=403,
        content={
            "error": "Forbidden",
            "detail": [exc.detail],
            "request_id": request_id,
        },
    )


async def not_found_error_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    """Translate a ``NotFoundError`` raised by the domain layer to HTTP 404.

    Response body::

        {
            "error": "Not found",
            "detail": ["<human-readable reason>"],
            "request_id": "<uuid>"
        }
    """
    request_id = _get_request_id(request)
    return JSONResponse(
        status_code=404,
        content={
            "error": "Not found",
            "detail": [exc.detail],
            "request_id": request_id,
        },
    )


async def conflict_error_handler(request: Request, exc: ConflictError) -> JSONResponse:
    """Translate a ``ConflictError`` raised by the domain layer to HTTP 409.

    Response body::

        {
            "error": "Conflict",
            "detail": ["<human-readable reason>"],
            "request_id": "<uuid>"
        }
    """
    request_id = _get_request_id(request)
    return JSONResponse(
        status_code=409,
        content={
            "error": "Conflict",
            "detail": [exc.detail],
            "request_id": request_id,
        },
    )
