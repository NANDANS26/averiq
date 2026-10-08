"""Authentication and authorization audit logging middleware.

Logs every authentication failure (HTTP 401) and authorization rejection
(HTTP 403) as a structured log entry per SEC-AUTH-007.

These events are emitted to the structured log stream. They are NOT stored
in ``audit_events`` (which records domain state changes); they are log-level
security telemetry for SIEM ingestion and operator alerting.

Log fields emitted on every 401/403:
  event:       ``"auth.failure"`` (401) | ``"authz.rejection"`` (403)
  actor:       user_id from unverified JWT ``sub`` claim, or ``"anonymous"``
               when no token is present or the token cannot be decoded.
               **The sub claim is read without verification** — the token has
               already been rejected by the auth layer; we read it solely to
               identify the actor in the log for audit purposes.
  resource:    URL path of the attempted request
  method:      HTTP method (GET, POST, …)
  timestamp:   UTC ISO 8601 string
  reason:      ``"authentication_failed"`` (401) | ``"insufficient_role"`` (403)
  source_ip:   Client IP derived from X-Forwarded-For (first address) or
               ``request.client.host``. Falls back to ``"unknown"``.
  request_id:  Correlation / request ID from structlog context (if present)
  status_code: Raw HTTP status code for filtering

Security note:
  The JWT sub claim is extracted *without* signature verification because
  the regular auth dependency has already rejected the token (wrong secret,
  expired, or missing). We never trust the sub value for authorisation here —
  we only use it to make log entries more actionable by naming the actor.
  If decoding fails for any reason the actor defaults to ``"anonymous"``.

Requirements: SEC-AUTH-007
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger(__name__)


def _extract_actor(request: Request) -> str:
    """Return the best-effort actor string from an incoming request.

    Tries to extract the ``sub`` claim from the Bearer JWT without signature
    verification. Falls back to ``"anonymous"`` on any failure.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return "anonymous"

    token = auth_header[7:]
    if not token:
        return "anonymous"

    try:
        # Try jose first; if not installed, fall back to a pure-stdlib
        # base64url decode (we only need the payload to identify the actor).
        try:
            from jose import jwt as jose_jwt  # type: ignore[import-untyped]

            unverified_claims = jose_jwt.get_unverified_claims(token)
        except ImportError:
            # jose not available — decode payload segment directly.
            # JWT structure: header.payload.signature (all base64url)
            parts = token.split(".")
            if len(parts) < 2:
                return "anonymous"
            import base64
            import json as _json

            # base64url requires padding to be added back.
            payload_b64 = parts[1]
            padding = 4 - (len(payload_b64) % 4)
            if padding != 4:
                payload_b64 += "=" * padding
            raw = base64.urlsafe_b64decode(payload_b64)
            unverified_claims = _json.loads(raw)

        return str(unverified_claims.get("sub", "anonymous")) or "anonymous"
    except Exception:  # noqa: BLE001
        return "anonymous"


def _extract_source_ip(request: Request) -> str:
    """Return the best-effort client IP address.

    Prefers the first address in X-Forwarded-For (standard for reverse-proxy
    deployments). Falls back to the direct connection peer address.
    """
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    if request.client:
        return request.client.host

    return "unknown"


class AuthAuditMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that intercepts 401 and 403 responses.

    After the route handler (or any downstream middleware) produces a
    response, this middleware inspects the status code. On 401 or 403 it
    emits a structured ``structlog`` warning that satisfies SEC-AUTH-007.

    The middleware runs *after* authentication dependencies, so the auth
    layer has already made its decision; this middleware only observes the
    outcome and records it.

    Registration in ``backend/main.py``::

        from backend.api.auth_logging import AuthAuditMiddleware
        app.add_middleware(AuthAuditMiddleware)

    Note on middleware ordering in Starlette/FastAPI:
        Middleware added via ``add_middleware`` is executed in LIFO order
        (last registered runs outermost). Register ``AuthAuditMiddleware``
        *after* the correlation middleware so that ``request_id`` and
        ``correlation_id`` are already bound to the structlog context when
        the log entry is emitted here.
    """

    async def dispatch(self, request: Request, call_next: object) -> Response:  # type: ignore[override]
        response: Response = await call_next(request)  # type: ignore[arg-type, misc]

        if response.status_code in (401, 403):
            actor = _extract_actor(request)
            source_ip = _extract_source_ip(request)

            if response.status_code == 401:
                event_name = "auth.failure"
                reason = "authentication_failed"
            else:
                event_name = "authz.rejection"
                reason = "insufficient_role"

            # Pull request_id from structlog context if the correlation
            # middleware has already bound it; otherwise fall back to the
            # response header (which the correlation middleware sets).
            ctx = structlog.contextvars.get_contextvars()
            request_id = ctx.get("request_id") or response.headers.get("X-Request-ID", "")

            logger.warning(
                event_name,
                actor=actor,
                resource=request.url.path,
                method=request.method,
                timestamp=datetime.now(timezone.utc).isoformat(),
                reason=reason,
                source_ip=source_ip,
                request_id=request_id,
                status_code=response.status_code,
            )

        return response
