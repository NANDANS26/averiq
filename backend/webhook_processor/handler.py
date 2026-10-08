"""Synchronous webhook handler for Razorpay incoming events.

Implements the 10-step synchronous pipeline (FR-WEBHOOK-001 through FR-WEBHOOK-014).
No Celery publish, no AI/ML calls, no external API calls occur in this HTTP path.
The handler returns HTTP 200 before Razorpay's 5-second session timeout.

10-Step Pipeline
----------------
Step 1  Capture raw_body bytes BEFORE any JSON parsing (FR-WEBHOOK-001)
Step 2  Extract provider_event_id from x-razorpay-event-id header;
        generate fallback key when header is missing (FR-WEBHOOK-008)
Step 3  Rate limit check — Redis sliding-window counter per merchant_id (FR-WEBHOOK-014)
Step 4  HMAC-SHA256 signature verification with constant-time compare (FR-WEBHOOK-002/003)
Step 5  Schema-validate parsed JSON payload; log violations without crashing (FR-WEBHOOK-012)
Step 6  Dedup check — SELECT WHERE (merchant_id, provider_event_id) (FR-WEBHOOK-009)
Step 7  Begin atomic DB transaction (FR-WEBHOOK-004/007)
Step 8  INSERT webhook_events (processing_status='PERSISTED')
         INSERT outbox_events (enqueued=False)
         Both in the SAME async with session.begin() block
Step 9  COMMIT — durable before HTTP response (implicit on context manager exit)
Step 10 Return HTTP 200 {"status": "received"}

Outbox delivery model (at-least-once + idempotent — NOT exactly-once)
----------------------------------------------------------------------
Case A: DB commit succeeds, outbox publisher hasn't run yet
        → pending outbox record exists; picked up within 30s by beat
Case B: Publisher publishes then crashes before marking enqueued=True
        → duplicate Celery task delivery; downstream idempotency absorbs it
Case C: Queue unavailable → outbox remains enqueued=False;
        background beat retries until queue is reachable

Requirements: FR-WEBHOOK-001 through FR-WEBHOOK-014
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from opentelemetry import metrics
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_session
from backend.domain.crypto import decrypt_secret
from backend.domain.models import Merchant, OutboxEvent, WebhookEvent

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# OpenTelemetry metrics (NFR-OBS-003, NFR-OBS-005)
# All labels must be low-cardinality — never case_id, customer_id, payment_id
# ---------------------------------------------------------------------------

_meter = metrics.get_meter("averiq.webhook_processor")

_webhook_received_counter = _meter.create_counter(
    name="webhook_received_total",
    description="Total webhook POST requests received",
    unit="1",
)

_webhook_duplicate_counter = _meter.create_counter(
    name="webhook_duplicate_total",
    description="Total duplicate webhook events detected and idempotently absorbed",
    unit="1",
)

_webhook_missing_event_id_counter = _meter.create_counter(
    name="webhook_missing_event_id_total",
    description="Webhooks received without x-razorpay-event-id header (degraded-mode dedup)",
    unit="1",
)

# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter()


# ---------------------------------------------------------------------------
# Rate limiter — Redis sliding-window counter (FR-WEBHOOK-014)
# ---------------------------------------------------------------------------


async def _check_rate_limit(redis_client: Any, merchant_id: str, limit: int) -> bool:
    """Return True if within rate limit, False if exceeded.

    Uses a sliding-window counter in Redis: increments a key and sets a 60s
    TTL on first use. The key resets each minute, approximating a per-minute
    rate window.

    Tradeoff documented (FR-WEBHOOK-014): the 60-second rolling window means
    a burst at the boundary can temporarily allow up to 2× the configured
    limit. Set WEBHOOK_RATE_LIMIT_PER_MINUTE conservatively to account for
    legitimate Razorpay burst activity during high payment volume.

    Rate limiting NEVER silently discards valid events — HTTP 429 causes
    Razorpay to retry per its exponential back-off policy (up to 24 hours),
    so no valid payment events are lost.

    Args:
        redis_client: An async redis.asyncio.Redis client.
        merchant_id:  Merchant UUID string (used as part of the key).
        limit:        Maximum requests per 60-second window.

    Returns:
        True  — request is within limit; proceed.
        False — rate limit exceeded; return HTTP 429.
    """
    key = f"rl:webhook:{merchant_id}"
    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.expire(key, 60)
    results = await pipe.execute()
    return int(results[0]) <= limit


def _get_redis_client() -> Any:
    """Return a lazy-initialised async Redis client.

    Imported here (not at module level) to avoid import-time failures in
    environments where Redis is not yet configured (e.g. unit tests that
    mock this function).
    """
    try:
        import redis.asyncio as aioredis  # type: ignore[import-untyped]
        return aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "webhook.redis_client_init_failed",
            error=str(exc),
            note="Rate limiting unavailable; proceeding without it",
        )
        return None


# ---------------------------------------------------------------------------
# HMAC-SHA256 verification (FR-WEBHOOK-002, FR-WEBHOOK-003)
# ---------------------------------------------------------------------------


def _verify_hmac(raw_body: bytes, raw_secret: str, signature_header: str) -> bool:
    """Verify Razorpay HMAC-SHA256 webhook signature.

    Computes HMAC-SHA256 of raw_body using raw_secret and compares the hex
    digest against the X-Razorpay-Signature header value using
    hmac.compare_digest() for constant-time comparison (prevents timing attacks).

    Args:
        raw_body:          The raw request body bytes captured in Step 1.
        raw_secret:        The plaintext webhook secret (decrypted from DB).
        signature_header:  Value of the X-Razorpay-Signature header.

    Returns:
        True if the signature matches; False otherwise.
    """
    expected = hmac.new(
        raw_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# Payload schema validation (FR-WEBHOOK-012)
# ---------------------------------------------------------------------------

# Required top-level fields in a valid Razorpay webhook payload
_REQUIRED_PAYLOAD_FIELDS = {"event", "payload", "created_at", "account_id"}


def _validate_payload_schema(
    body: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Validate a parsed Razorpay webhook payload against the expected schema.

    Logs violations without raising; the caller decides whether to proceed.
    Does not crash on unexpected fields (Razorpay may add new fields).

    Args:
        body: The JSON-parsed webhook payload.

    Returns:
        (is_valid, violations) — violations is an empty list on success.
    """
    violations: list[str] = []

    for field in _REQUIRED_PAYLOAD_FIELDS:
        if field not in body:
            violations.append(f"Missing required field: '{field}'")

    # Validate 'created_at' is a positive integer (Unix timestamp)
    created_at = body.get("created_at")
    if created_at is not None and not isinstance(created_at, int):
        violations.append(f"'created_at' must be an integer Unix timestamp; got {type(created_at).__name__}")

    # Validate 'event' is a non-empty string
    event_type = body.get("event")
    if event_type is not None and not isinstance(event_type, str):
        violations.append(f"'event' must be a string; got {type(event_type).__name__}")
    elif isinstance(event_type, str) and not event_type.strip():
        violations.append("'event' must be a non-empty string")

    return len(violations) == 0, violations


# ---------------------------------------------------------------------------
# Fallback dedup key generation (Step 2 — missing event ID path)
# ---------------------------------------------------------------------------


def _generate_fallback_dedup_key(
    entity_id: str,
    event_type: str,
    unix_ts: int,
) -> str:
    """Generate a fallback dedup key when x-razorpay-event-id header is absent.

    Uses entity_id + event_type + 5-minute time bucket to produce a
    deterministic key for a given event window. This is degraded-mode
    behaviour: the fallback key is NOT equivalent to a real provider event ID
    and may not survive a legitimate re-delivery of the same event outside
    the 5-minute bucket.

    time_bucket = floor(unix_ts / 300) * 300  — 5-minute buckets

    Args:
        entity_id:  Razorpay entity ID (e.g. pay_XXXX or sub_XXXX).
        event_type: Razorpay event type string (e.g. "payment.failed").
        unix_ts:    Unix timestamp from the webhook payload.

    Returns:
        A string of the form "FALLBACK_{entity_id}_{event_type}_{time_bucket}".
    """
    time_bucket = int(unix_ts // 300) * 300
    return f"FALLBACK_{entity_id}_{event_type}_{time_bucket}"


# ---------------------------------------------------------------------------
# Helper — extract entity info from Razorpay payload
# ---------------------------------------------------------------------------


def _extract_entity_info(body: dict[str, Any]) -> tuple[str, str, str]:
    """Extract entity_id, entity_type, and event_type from the webhook payload.

    Razorpay webhook payloads have the structure:
      {
        "event": "payment.failed",
        "payload": {
          "payment": {
            "entity": { "id": "pay_XXXX", ... }
          }
        }
      }

    Returns:
        (entity_id, entity_type, event_type) — all strings; empty string on failure.
    """
    event_type: str = body.get("event", "")

    # Derive entity_type from the event prefix (e.g. "payment" from "payment.failed")
    event_prefix = event_type.split(".")[0] if "." in event_type else ""

    payload_section = body.get("payload", {})
    entity_type = ""
    entity_id = ""

    if isinstance(payload_section, dict):
        # Razorpay nests entity under its type key: payload.payment.entity, etc.
        for key in ("payment", "subscription", "order", "refund", "invoice"):
            entity_wrapper = payload_section.get(key, {})
            if isinstance(entity_wrapper, dict):
                entity_data = entity_wrapper.get("entity", {})
                if isinstance(entity_data, dict) and "id" in entity_data:
                    entity_id = str(entity_data["id"])
                    entity_type = key
                    break

        # If we found an entity via the payload structure, use that entity_type
        # Override event_prefix with the matched key if they differ
        if not entity_id and event_prefix:
            entity_type = event_prefix

    return entity_id, entity_type, event_type


# ---------------------------------------------------------------------------
# Helper — hash merchant_id for log safety (NFR-OBS-007)
# ---------------------------------------------------------------------------


def _hash_merchant_id(merchant_id: str) -> str:
    """Return a SHA-256 hash of merchant_id for use in log entries.

    Logs must not expose raw UUIDs as high-cardinality metric labels (NFR-OBS-005),
    and must not expose PII (NFR-OBS-007). Using a hash preserves debuggability
    while masking the raw identifier in observable outputs.
    """
    return hashlib.sha256(merchant_id.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Main webhook endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/razorpay/{merchant_id}",
    status_code=200,
    summary="Razorpay webhook ingestion endpoint",
    description=(
        "Receives Razorpay payment gateway webhooks. "
        "Authenticates via HMAC-SHA256, deduplicates, and persists atomically. "
        "No authentication required — signature verification IS the auth mechanism."
    ),
    tags=["webhooks"],
)
async def receive_webhook(
    merchant_id: str = Path(
        ...,
        description="The merchant UUID — set as the webhook URL in Razorpay dashboard",
    ),
    request: Request = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Synchronous 10-step webhook ingestion pipeline.

    The ``merchant_id`` path parameter identifies which merchant this webhook
    belongs to. Each merchant configures a unique webhook URL in Razorpay:
      https://<host>/api/v1/webhooks/razorpay/<merchant_uuid>

    This endpoint:
    - Requires NO Bearer token (SEC-AUTH-001: webhook endpoint exempt from auth).
    - Returns HTTP 200 for both new events (persisted) and duplicates (idempotent).
    - Returns HTTP 401 for invalid HMAC signatures.
    - Returns HTTP 429 when rate limit is exceeded.
    - NEVER performs AI/ML inference, external API calls, or expensive operations.
    """

    # ------------------------------------------------------------------
    # Step 1: Capture raw_body bytes BEFORE any parsing (FR-WEBHOOK-001)
    # ------------------------------------------------------------------
    # This MUST be the very first operation. Once request.body() is consumed,
    # the byte stream is gone. All subsequent parsing uses this captured value.
    raw_body: bytes = await request.body()

    # Bind webhook context to structlog for all subsequent log entries
    request_id = structlog.contextvars.get_contextvars().get("request_id", str(uuid.uuid4()))
    logger.info(
        "webhook.received",
        merchant_id_hash=_hash_merchant_id(merchant_id),
        content_length=len(raw_body),
        request_id=request_id,
    )

    # Emit webhook_received_total counter (NFR-OBS-003)
    _webhook_received_counter.add(1, {"provider": "razorpay"})

    # ------------------------------------------------------------------
    # Step 2: Extract provider_event_id from x-razorpay-event-id header
    #         Fall back to generated key if header is absent/whitespace
    # ------------------------------------------------------------------
    raw_event_id_header: str = request.headers.get("x-razorpay-event-id", "").strip()

    # We need the payload to generate the fallback key — parse it speculatively.
    # If JSON parsing fails here we still proceed to HMAC verification (which
    # will reject malformed payloads anyway via schema validation in Step 5).
    _parsed_body: dict[str, Any] = {}
    try:
        _parsed_body = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass  # Handled in Step 5; proceed to HMAC verify first

    entity_id, entity_type, event_type = _extract_entity_info(_parsed_body)
    unix_ts: int = int(_parsed_body.get("created_at", 0))

    if raw_event_id_header:
        # Valid header — use verbatim as the primary dedup key
        provider_event_id: str = raw_event_id_header
    else:
        # Missing/empty header — generate fallback key (degraded-mode)
        provider_event_id = _generate_fallback_dedup_key(
            entity_id=entity_id or "unknown",
            event_type=event_type or "unknown",
            unix_ts=unix_ts,
        )
        # Emit metric and structured warning (NFR-OBS-003, FR-WEBHOOK-014)
        _webhook_missing_event_id_counter.add(1, {"provider": "razorpay"})
        logger.warning(
            "webhook.missing_event_id",
            merchant_id_hash=_hash_merchant_id(merchant_id),
            fallback_key=provider_event_id,
            note=(
                "x-razorpay-event-id header absent; using fallback dedup key. "
                "This is degraded-mode behaviour — the fallback key may not survive "
                "a legitimate re-delivery of the same event outside the 5-minute bucket."
            ),
        )

    # ------------------------------------------------------------------
    # Step 3: Rate limit check (FR-WEBHOOK-014)
    # Performed BEFORE dedup and DB writes to avoid unnecessary DB load
    # on burst traffic. HTTP 429 causes Razorpay to retry per its
    # exponential back-off policy — valid events are NOT silently discarded.
    # ------------------------------------------------------------------
    redis_client = _get_redis_client()
    if redis_client is not None:
        try:
            within_limit = await _check_rate_limit(
                redis_client,
                merchant_id,
                settings.WEBHOOK_RATE_LIMIT_PER_MINUTE,
            )
            if not within_limit:
                logger.warning(
                    "webhook.rate_limit_exceeded",
                    merchant_id_hash=_hash_merchant_id(merchant_id),
                    limit=settings.WEBHOOK_RATE_LIMIT_PER_MINUTE,
                    note="HTTP 429 returned; Razorpay will retry per its back-off policy",
                )
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": "60"},
                    content={
                        "error": "Rate limit exceeded",
                        "detail": "Too many webhook requests. Retry after 60 seconds.",
                        "request_id": request_id,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            # Redis error — log and continue without rate limiting
            # Better to accept an event than to silently drop a valid payment event
            logger.warning(
                "webhook.rate_limit_redis_error",
                error=str(exc),
                note="Rate limit check skipped due to Redis error; proceeding",
            )
        finally:
            try:
                await redis_client.aclose()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Step 4: HMAC-SHA256 signature verification (FR-WEBHOOK-002, FR-WEBHOOK-003)
    # Fetch the merchant and decrypt the stored webhook secret.
    # Return HTTP 401 on failure; never expose the reason to the caller.
    # ------------------------------------------------------------------
    signature_header: str = request.headers.get("X-Razorpay-Signature", "").strip()

    # Fetch the Merchant record to get the encrypted webhook secret
    try:
        merchant_uuid = uuid.UUID(merchant_id)
    except ValueError:
        logger.warning(
            "webhook.invalid_merchant_id",
            merchant_id=merchant_id[:64],  # truncate for log safety
            source_ip=_get_source_ip(request),
        )
        return JSONResponse(
            status_code=401,
            content={
                "error": "Unauthorized",
                "detail": "Invalid merchant identifier",
                "request_id": request_id,
            },
        )

    merchant_row: Merchant | None = await session.get(Merchant, merchant_uuid)
    if merchant_row is None:
        logger.warning(
            "webhook.merchant_not_found",
            merchant_id_hash=_hash_merchant_id(merchant_id),
            source_ip=_get_source_ip(request),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        return JSONResponse(
            status_code=401,
            content={
                "error": "Unauthorized",
                "detail": "Signature verification failed",
                "request_id": request_id,
            },
        )

    # Decrypt the stored webhook secret for HMAC verification
    # The plaintext secret must NEVER be logged (NFR-OBS-007)
    try:
        raw_secret: str = decrypt_secret(merchant_row.webhook_secret, settings.APP_SECRET_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "webhook.secret_decryption_failed",
            merchant_id_hash=_hash_merchant_id(merchant_id),
            error_type=type(exc).__name__,
            note="Cannot verify webhook signature; rejecting",
        )
        return JSONResponse(
            status_code=401,
            content={
                "error": "Unauthorized",
                "detail": "Signature verification failed",
                "request_id": request_id,
            },
        )

    # Verify HMAC signature — constant-time comparison (FR-WEBHOOK-002)
    if not signature_header or not _verify_hmac(raw_body, raw_secret, signature_header):
        logger.warning(
            "webhook.signature_verification_failed",
            merchant_id_hash=_hash_merchant_id(merchant_id),
            source_ip=_get_source_ip(request),
            timestamp=datetime.now(timezone.utc).isoformat(),
            has_signature_header=bool(signature_header),
        )
        return JSONResponse(
            status_code=401,
            content={
                "error": "Unauthorized",
                "detail": "Signature verification failed",
                "request_id": request_id,
            },
        )

    logger.info(
        "webhook.signature_verified",
        merchant_id_hash=_hash_merchant_id(merchant_id),
    )

    # ------------------------------------------------------------------
    # Step 5: Schema-validate parsed payload (FR-WEBHOOK-012)
    # Log violations without crashing — a schema violation is observability
    # information, not a rejection reason (the event is still persisted).
    # ------------------------------------------------------------------
    if not _parsed_body:
        # JSON parsing failed earlier — log and continue (we cannot validate)
        logger.warning(
            "webhook.payload_json_parse_failed",
            merchant_id_hash=_hash_merchant_id(merchant_id),
            note="Payload could not be parsed as JSON; persisting with minimal fields",
        )
    else:
        is_valid, violations = _validate_payload_schema(_parsed_body)
        if not is_valid:
            for violation in violations:
                logger.warning(
                    "webhook.schema_violation",
                    merchant_id_hash=_hash_merchant_id(merchant_id),
                    violation=violation,
                )

    # ------------------------------------------------------------------
    # Step 6: Dedup check — SELECT WHERE (merchant_id, provider_event_id)
    # If a matching row exists, return HTTP 200 immediately (idempotent).
    # No new domain events or state transitions are produced. (FR-WEBHOOK-009)
    # ------------------------------------------------------------------
    existing_stmt = select(WebhookEvent).where(
        WebhookEvent.merchant_id == merchant_uuid,
        WebhookEvent.provider_event_id == provider_event_id,
    )
    existing_result = await session.execute(existing_stmt)
    existing_event: WebhookEvent | None = existing_result.scalar_one_or_none()

    if existing_event is not None:
        logger.info(
            "webhook.duplicate_detected",
            merchant_id_hash=_hash_merchant_id(merchant_id),
            provider_event_id=provider_event_id,
            existing_event_id=str(existing_event.id),
            processing_status=existing_event.processing_status,
        )
        _webhook_duplicate_counter.add(1, {"provider": "razorpay"})
        return JSONResponse(
            status_code=200,
            content={"status": "received"},
        )

    # ------------------------------------------------------------------
    # Steps 7+8: Atomic DB transaction
    # INSERT webhook_events (processing_status='PERSISTED')
    # INSERT outbox_events (enqueued=False)
    # BOTH in a single async with session.begin() block.
    # If either INSERT fails, the entire transaction rolls back (FR-WEBHOOK-007).
    # ------------------------------------------------------------------
    now = datetime.now(timezone.utc)

    # Build the provider_event_time from the payload's created_at field
    provider_event_time: datetime
    if unix_ts > 0:
        provider_event_time = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
    else:
        provider_event_time = now  # fallback if payload is missing/malformed

    # Generate a correlation_id to link all log entries for this event chain
    correlation_id = uuid.uuid4()
    structlog.contextvars.bind_contextvars(correlation_id=str(correlation_id))

    webhook_event_id: uuid.UUID
    try:
        async with session.begin():
            # --- Step 8a: INSERT webhook_events ---
            webhook_event = WebhookEvent(
                id=uuid.uuid4(),
                merchant_id=merchant_uuid,
                provider_event_id=provider_event_id,
                event_type=event_type or "unknown",
                processing_status="PERSISTED",
                entity_id=entity_id or None,
                entity_type=entity_type or None,
                event_timestamp=provider_event_time,
                retry_count=0,
                last_error=None,
                received_at=now,
                updated_at=now,
            )
            session.add(webhook_event)
            # Flush to get the generated ID before inserting outbox record
            await session.flush()
            webhook_event_id = webhook_event.id

            # --- Step 8b: INSERT outbox_events (enqueued=False) ---
            outbox_event = OutboxEvent(
                id=uuid.uuid4(),
                webhook_event_id=webhook_event_id,
                merchant_id=merchant_uuid,
                enqueued=False,
                enqueued_at=None,
                celery_task_id=None,
                created_at=now,
            )
            session.add(outbox_event)

            # Step 9: COMMIT is handled by session.begin() context manager exit
            # Both records are durable in PostgreSQL before we return HTTP 200.

    except IntegrityError:
        # Race condition: another request inserted the same (merchant_id, provider_event_id)
        # concurrently between our dedup check and our insert. This is safe to absorb
        # as a duplicate — idempotent behaviour.
        logger.info(
            "webhook.concurrent_duplicate_on_insert",
            merchant_id_hash=_hash_merchant_id(merchant_id),
            provider_event_id=provider_event_id,
            note="Race condition during insert; treating as duplicate",
        )
        _webhook_duplicate_counter.add(1, {"provider": "razorpay"})
        return JSONResponse(
            status_code=200,
            content={"status": "received"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "webhook.persistence_failed",
            merchant_id_hash=_hash_merchant_id(merchant_id),
            error_type=type(exc).__name__,
            error_message=str(exc),
            error_repr=repr(exc),
            provider_event_id=provider_event_id,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "detail": "Failed to persist webhook event",
                "request_id": request_id,
            },
        )

    logger.info(
        "webhook.persisted",
        merchant_id_hash=_hash_merchant_id(merchant_id),
        webhook_event_id=str(webhook_event_id),
        event_type=event_type,
        provider_event_id=provider_event_id,
        correlation_id=str(correlation_id),
    )

    # ------------------------------------------------------------------
    # Optionally persist raw payload (WEBHOOK_RETAIN_RAW_PAYLOAD flag)
    # Stored in a separate table with retention expiry (DATA-006)
    # NOT in the primary webhook_events table — avoids PII exposure in
    # the main data path (NFR-OBS-007)
    # ------------------------------------------------------------------
    if settings.WEBHOOK_RETAIN_RAW_PAYLOAD:
        await _persist_raw_payload(
            session=session,
            webhook_event_id=webhook_event_id,
            raw_body=raw_body,
            now=now,
        )

    # ------------------------------------------------------------------
    # Step 10: Return HTTP 200 {"status": "received"}
    # The event is durably persisted and in the outbox.
    # The outbox beat will enqueue a Celery task within 30 seconds.
    # FR-WEBHOOK-005: HTTP 200 returned before Razorpay's 5-second timeout.
    # FR-WEBHOOK-006: No AI/ML/external calls in this path.
    # ------------------------------------------------------------------
    return JSONResponse(
        status_code=200,
        content={"status": "received"},
    )


# ---------------------------------------------------------------------------
# Raw payload persistence (optional — WEBHOOK_RETAIN_RAW_PAYLOAD flag)
# ---------------------------------------------------------------------------


async def _persist_raw_payload(
    session: AsyncSession,
    webhook_event_id: uuid.UUID,
    raw_body: bytes,
    now: datetime,
) -> None:
    """Persist raw webhook bytes to the webhook_raw_payloads table.

    Only called when WEBHOOK_RETAIN_RAW_PAYLOAD=True. Stored in a separate
    table with a 7-day default retention (DATA-006). No public API exposes
    this table. Raw payloads are used for debugging only.

    This runs in its OWN transaction, AFTER the main commit, so a failure
    here does NOT roll back the already-committed WebhookEvent/OutboxEvent.
    """
    from datetime import timedelta

    from backend.domain.models import WebhookRawPayload

    # 7-day retention by default (DATA-006)
    retention_days = 7
    expires_at = now + timedelta(days=retention_days)

    try:
        async with session.begin():
            raw_payload = WebhookRawPayload(
                id=uuid.uuid4(),
                webhook_event_id=webhook_event_id,
                raw_body=raw_body,
                content_type="application/json",
                expires_at=expires_at,
                created_at=now,
            )
            session.add(raw_payload)
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: raw payload retention is best-effort
        logger.warning(
            "webhook.raw_payload_persistence_failed",
            webhook_event_id=str(webhook_event_id),
            error_type=type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Helper — extract source IP for audit logging (FR-WEBHOOK-003)
# ---------------------------------------------------------------------------


def _get_source_ip(request: Request) -> str:
    """Return the best-effort client IP for auth rejection audit logs.

    Prefers X-Forwarded-For (reverse-proxy deployments), falls back to
    the direct connection peer address. Returns "unknown" if neither is
    available.
    """
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"
