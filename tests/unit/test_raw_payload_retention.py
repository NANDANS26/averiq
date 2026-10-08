"""Unit tests for optional raw webhook payload retention (task 6.5).

Tests cover:
  1. Returns None when WEBHOOK_RETAIN_RAW_PAYLOAD is False (default).
  2. Stores the raw bytes (correct WebhookRawPayload written) when flag is True.
  3. expires_at is set to now + WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS.
  4. The content_type argument is preserved on the stored record.

All tests are pure unit tests — no real database I/O (AsyncSession is mocked).

Requirements: FR-WEBHOOK-012, DATA-006
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.webhook_processor.raw_payload_retention import maybe_store_raw_payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_session() -> MagicMock:
    """Return an AsyncSession mock with ``add`` and ``flush`` wired up."""
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Test 1 — Flag disabled: function returns None, nothing is added to session
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_returns_none_when_flag_disabled() -> None:
    """maybe_store_raw_payload returns None when WEBHOOK_RETAIN_RAW_PAYLOAD=False."""
    session = _make_mock_session()
    webhook_event_id = uuid.uuid4()
    raw_body = b'{"event": "payment.failed"}'

    with patch(
        "backend.webhook_processor.raw_payload_retention.settings"
    ) as mock_settings:
        mock_settings.WEBHOOK_RETAIN_RAW_PAYLOAD = False

        result = await maybe_store_raw_payload(
            session=session,
            webhook_event_id=webhook_event_id,
            raw_body=raw_body,
        )

    assert result is None
    session.add.assert_not_called()
    session.flush.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2 — Flag enabled: WebhookRawPayload is added to the session with correct data
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stores_raw_bytes_when_flag_enabled() -> None:
    """maybe_store_raw_payload adds a WebhookRawPayload to session when flag is True."""
    session = _make_mock_session()
    webhook_event_id = uuid.uuid4()
    raw_body = b'{"event": "payment.failed", "payload": {"payment": {"entity": {"id": "pay_123"}}}}'

    with patch(
        "backend.webhook_processor.raw_payload_retention.settings"
    ) as mock_settings:
        mock_settings.WEBHOOK_RETAIN_RAW_PAYLOAD = True
        mock_settings.WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS = 30

        result = await maybe_store_raw_payload(
            session=session,
            webhook_event_id=webhook_event_id,
            raw_body=raw_body,
        )

    assert result is not None
    # session.add was called with the new record
    session.add.assert_called_once_with(result)
    # session.flush was awaited once
    session.flush.assert_awaited_once()

    # Verify the stored record fields
    assert result.webhook_event_id == webhook_event_id
    assert result.raw_body == raw_body
    assert isinstance(result.id, uuid.UUID)


# ---------------------------------------------------------------------------
# Test 3 — expires_at is set to now + WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_expires_at_uses_retention_days_config() -> None:
    """expires_at = now + WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS (config-driven)."""
    session = _make_mock_session()
    webhook_event_id = uuid.uuid4()
    raw_body = b"raw bytes"
    retention_days = 14

    with patch(
        "backend.webhook_processor.raw_payload_retention.settings"
    ) as mock_settings:
        mock_settings.WEBHOOK_RETAIN_RAW_PAYLOAD = True
        mock_settings.WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS = retention_days

        before = datetime.now(timezone.utc)
        result = await maybe_store_raw_payload(
            session=session,
            webhook_event_id=webhook_event_id,
            raw_body=raw_body,
        )
        after = datetime.now(timezone.utc)

    assert result is not None
    assert result.expires_at is not None

    # expires_at should fall within [before + retention_days, after + retention_days]
    expected_lower = before + timedelta(days=retention_days)
    expected_upper = after + timedelta(days=retention_days)
    assert expected_lower <= result.expires_at <= expected_upper


# ---------------------------------------------------------------------------
# Test 4 — content_type is preserved on the stored record
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_content_type_is_preserved() -> None:
    """The content_type argument is stored verbatim on the WebhookRawPayload."""
    session = _make_mock_session()
    webhook_event_id = uuid.uuid4()
    raw_body = b'{"event": "payment.failed"}'
    custom_content_type = "application/x-www-form-urlencoded"

    with patch(
        "backend.webhook_processor.raw_payload_retention.settings"
    ) as mock_settings:
        mock_settings.WEBHOOK_RETAIN_RAW_PAYLOAD = True
        mock_settings.WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS = 30

        result = await maybe_store_raw_payload(
            session=session,
            webhook_event_id=webhook_event_id,
            raw_body=raw_body,
            content_type=custom_content_type,
        )

    assert result is not None
    assert result.content_type == custom_content_type


# ---------------------------------------------------------------------------
# Test 5 — default content_type is "application/json"
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_default_content_type_is_json() -> None:
    """When content_type is not supplied, it defaults to 'application/json'."""
    session = _make_mock_session()
    webhook_event_id = uuid.uuid4()
    raw_body = b'{"event": "subscription.charged"}'

    with patch(
        "backend.webhook_processor.raw_payload_retention.settings"
    ) as mock_settings:
        mock_settings.WEBHOOK_RETAIN_RAW_PAYLOAD = True
        mock_settings.WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS = 30

        result = await maybe_store_raw_payload(
            session=session,
            webhook_event_id=webhook_event_id,
            raw_body=raw_body,
            # content_type intentionally omitted to test default
        )

    assert result is not None
    assert result.content_type == "application/json"


# ---------------------------------------------------------------------------
# Test 6 — different retention days value is respected
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_expires_at_respects_non_default_retention_days() -> None:
    """Changing WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS shifts expires_at accordingly."""
    session_30 = _make_mock_session()
    session_90 = _make_mock_session()
    webhook_event_id = uuid.uuid4()
    raw_body = b"test"

    with patch(
        "backend.webhook_processor.raw_payload_retention.settings"
    ) as mock_settings:
        mock_settings.WEBHOOK_RETAIN_RAW_PAYLOAD = True
        mock_settings.WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS = 30

        result_30 = await maybe_store_raw_payload(
            session=session_30,
            webhook_event_id=webhook_event_id,
            raw_body=raw_body,
        )

    with patch(
        "backend.webhook_processor.raw_payload_retention.settings"
    ) as mock_settings:
        mock_settings.WEBHOOK_RETAIN_RAW_PAYLOAD = True
        mock_settings.WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS = 90

        result_90 = await maybe_store_raw_payload(
            session=session_90,
            webhook_event_id=webhook_event_id,
            raw_body=raw_body,
        )

    assert result_30 is not None
    assert result_90 is not None

    # 90-day result must expire after 30-day result
    assert result_90.expires_at > result_30.expires_at

    # Difference should be approximately 60 days (within a few seconds of test wall time)
    delta = result_90.expires_at - result_30.expires_at
    assert timedelta(days=59, hours=23) <= delta <= timedelta(days=60, seconds=10)
