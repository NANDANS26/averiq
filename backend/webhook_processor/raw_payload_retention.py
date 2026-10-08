"""Optional raw webhook payload retention.

Controlled by ``WEBHOOK_RETAIN_RAW_PAYLOAD`` config flag (default: False).

When enabled, stores the verbatim raw request body bytes in the
``webhook_raw_payloads`` table with an expiry timestamp.  This allows:
  - HMAC signature re-verification against the original bytes
  - Debugging of webhook parsing issues
  - Compliance audit trails (where required)

WARNING: raw payloads may contain PII (customer names, email addresses, etc.).
Ensure a data retention policy is applied:
  - ``expires_at`` is set based on ``WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS`` (default: 30)
  - A background job should purge rows where ``expires_at < NOW()``

Security:
  - No public API endpoint exposes this table
  - Application DB role should have INSERT-only on ``webhook_raw_payloads`` in production
  - Raw payloads are NEVER returned in API responses

Requirements: FR-WEBHOOK-012, DATA-006
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.domain.models import WebhookRawPayload

logger = structlog.get_logger(__name__)

# Default retention window in days — used if the config attribute is somehow absent.
# In practice, ``settings.WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS`` is always set.
_DEFAULT_RETENTION_DAYS = 30


async def maybe_store_raw_payload(
    session: AsyncSession,
    webhook_event_id: uuid.UUID,
    raw_body: bytes,
    content_type: str = "application/json",
) -> WebhookRawPayload | None:
    """Conditionally persist raw webhook bytes to ``webhook_raw_payloads``.

    This function is a no-op when ``WEBHOOK_RETAIN_RAW_PAYLOAD`` is False
    (the default), which means no PII is retained unnecessarily.

    When the flag is True, the bytes are written to ``webhook_raw_payloads``
    within the caller's open ``AsyncSession``.  The caller is responsible for
    committing the transaction.  Use ``await session.flush()`` before
    committing if you need the auto-generated row to be visible within the
    same transaction.

    Args:
        session:           Open ``AsyncSession`` (caller's transaction).
        webhook_event_id:  UUID of the parent ``WebhookEvent`` row.
        raw_body:          Raw HTTP request body bytes (verbatim — not decoded).
        content_type:      HTTP ``Content-Type`` header value.

    Returns:
        The persisted ``WebhookRawPayload`` ORM object, or ``None`` if
        retention is disabled.
    """
    if not settings.WEBHOOK_RETAIN_RAW_PAYLOAD:
        return None

    # Compute retention window from config (falls back to _DEFAULT_RETENTION_DAYS
    # if the attribute is somehow missing — defensive guard for test environments).
    retention_days: int = getattr(
        settings,
        "WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS",
        _DEFAULT_RETENTION_DAYS,
    )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=retention_days)

    raw_payload = WebhookRawPayload(
        id=uuid.uuid4(),
        webhook_event_id=webhook_event_id,
        raw_body=raw_body,
        content_type=content_type,
        expires_at=expires_at,
        created_at=now,
    )

    session.add(raw_payload)
    await session.flush()

    logger.debug(
        "webhook.raw_payload_stored",
        webhook_event_id=str(webhook_event_id),
        size_bytes=len(raw_body),
        expires_at=expires_at.isoformat(),
        note="Raw payload retained per WEBHOOK_RETAIN_RAW_PAYLOAD=true",
    )

    return raw_payload
