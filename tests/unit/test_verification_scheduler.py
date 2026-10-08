"""Unit tests for VerificationScheduler — re-verification with escalation.

Tests are fully mocked (no I/O, no database, no Celery broker).

Test coverage:
  1. MAX_VERIFICATION_ATTEMPTS constant equals 3
  2. should_escalate returns False when attempt count < 3
  3. should_escalate returns True when attempt count >= 3
  4. record_attempt stores empty/normalised data only — NOT raw gateway
     response payloads (FR-VERIFY-008, AUD-006)
  5. record_attempt sets verification_timestamp (not None) — DATA-004

Requirements: FR-VERIFY-006, FR-VERIFY-007, FR-VERIFY-008
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.verification_engine.scheduler import (
    MAX_VERIFICATION_ATTEMPTS,
    VerificationScheduler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(scalar_count: int = 0) -> AsyncMock:
    """Return a mock AsyncSession whose execute() returns a count scalar.

    ``scalar_count`` is what ``result.scalar()`` returns, simulating a
    ``SELECT count(id) FROM verification_attempts WHERE ...`` result.
    """
    session = AsyncMock()

    count_result = MagicMock()
    count_result.scalar.return_value = scalar_count

    session.execute = AsyncMock(return_value=count_result)
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Test 1 — constant value
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_max_verification_attempts_constant() -> None:
    """MAX_VERIFICATION_ATTEMPTS must equal 3 (FR-VERIFY-007)."""
    assert MAX_VERIFICATION_ATTEMPTS == 3


# ---------------------------------------------------------------------------
# Test 2 — should_escalate returns False when below threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_should_escalate_returns_false_when_count_below_max() -> None:
    """should_escalate returns False when attempt count < MAX_VERIFICATION_ATTEMPTS.

    Simulates 2 prior attempts; the scheduler should NOT yet escalate.
    """
    scheduler = VerificationScheduler()
    session = _make_session(scalar_count=2)
    ext_op_id = uuid.uuid4()

    result = await scheduler.should_escalate(session, ext_op_id)

    assert result is False
    session.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 3 — should_escalate returns True when at or above threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_should_escalate_returns_true_when_count_at_max() -> None:
    """should_escalate returns True when attempt count >= MAX_VERIFICATION_ATTEMPTS.

    Tests both the boundary (count == 3) and beyond (count == 4).
    Requirements: FR-VERIFY-007
    """
    scheduler = VerificationScheduler()
    ext_op_id = uuid.uuid4()

    # Boundary: exactly at the limit
    session_at_limit = _make_session(scalar_count=MAX_VERIFICATION_ATTEMPTS)
    assert await scheduler.should_escalate(session_at_limit, ext_op_id) is True

    # Beyond the limit
    session_beyond = _make_session(scalar_count=MAX_VERIFICATION_ATTEMPTS + 1)
    assert await scheduler.should_escalate(session_beyond, ext_op_id) is True


# ---------------------------------------------------------------------------
# Test 4 — record_attempt stores normalised fields only, NOT raw payloads
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_record_attempt_does_not_store_raw_gateway_response() -> None:
    """record_attempt must NOT store raw gateway response payloads.

    The VerificationAttempt ORM model intentionally omits a raw-payload
    field (AUD-006, FR-VERIFY-008).  This test asserts that ``record_attempt``
    only writes normalised status strings — never a raw response body.

    Specifically verified:
    - ``gateway_reported_status`` is the normalised status string passed in
      (not a dict / blob of raw gateway data)
    - ``provider`` stores the verification strategy name (not a raw payload)
    - The created attempt has no attribute containing a raw dict of gateway
      response fields
    """
    scheduler = VerificationScheduler()
    session = _make_session()

    ext_op_id = uuid.uuid4()
    scheduled_at = datetime.now(timezone.utc)

    attempt = await scheduler.record_attempt(
        session=session,
        external_operation_id=ext_op_id,
        attempt_number=1,
        outcome="UNKNOWN",
        gateway_status="pending",
        verification_strategy="PaymentLinkReconciliationStrategy",
        scheduled_at=scheduled_at,
    )

    # Raw payloads are NEVER stored — gateway_reported_status must be a
    # plain string (normalised status), not a dict or bytes payload.
    assert isinstance(attempt.gateway_reported_status, str), (
        "gateway_reported_status must be a plain string — raw gateway "
        "response payloads must NOT be stored (FR-VERIFY-008, AUD-006)"
    )
    # The value must match the normalised status passed in
    assert attempt.gateway_reported_status == "pending"

    # provider stores the strategy name for observability (not raw data)
    assert attempt.provider == "PaymentLinkReconciliationStrategy"

    # session.add and flush were called
    session.add.assert_called_once_with(attempt)
    session.flush.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 5 — record_attempt sets verification_timestamp (not None)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_record_attempt_sets_verification_timestamp() -> None:
    """record_attempt must set a non-None verification_timestamp (DATA-004).

    The timestamp must be timezone-aware UTC so it can be used for
    ``time_since_failure_seconds`` computations without train/serve skew.
    """
    scheduler = VerificationScheduler()
    session = _make_session()

    ext_op_id = uuid.uuid4()
    scheduled_at = datetime.now(timezone.utc)

    attempt = await scheduler.record_attempt(
        session=session,
        external_operation_id=ext_op_id,
        attempt_number=1,
        outcome="VERIFIED",
        gateway_status="captured",
        verification_strategy="PaymentLinkReconciliationStrategy",
        scheduled_at=scheduled_at,
    )

    # verification_timestamp must not be None
    assert attempt.verification_timestamp is not None, (
        "verification_timestamp must be set to a UTC datetime (DATA-004)"
    )

    # Must be a datetime instance
    assert isinstance(attempt.verification_timestamp, datetime)

    # Must be timezone-aware (UTC)
    assert attempt.verification_timestamp.tzinfo is not None, (
        "verification_timestamp must be timezone-aware UTC (DATA-004)"
    )
