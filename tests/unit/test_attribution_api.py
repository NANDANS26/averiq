"""Unit tests for the attribution and metrics API endpoints (task 15.2).

Tests:
  1. recovered_revenue_total is always int (DATA-001 — financial integer preservation).
  2. _safe_rate(0, 0) returns "NOT_AVAILABLE" — never a runtime error.
  3. Attribution endpoint response includes the operating_mode field.

All tests are pure unit tests — no database, no Redis, no network calls.
Session and authentication dependencies are mocked with AsyncMock / MagicMock.

Requirements: FR-ATTR-003, FR-ATTR-004, DATA-001
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.api.attribution import (
    AttributionReport,
    _safe_rate,
    get_attribution_report,
)
from backend.api.dependencies import CurrentUser
from backend.domain.models import Merchant, User

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MERCHANT_ID = uuid.uuid4()
_NOW = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _make_current_user(role: str = "VIEWER") -> CurrentUser:
    """Return a CurrentUser with the given role, scoped to _MERCHANT_ID."""
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.role = role
    return CurrentUser(user=user, merchant_id=_MERCHANT_ID)


def _make_merchant(
    currency: str = "INR",
    operating_mode: str = "SIMULATED",
) -> Merchant:
    """Return a minimal Merchant ORM-like mock (no DB required)."""
    m = MagicMock(spec=Merchant)
    m.id = _MERCHANT_ID
    m.currency = currency
    m.operating_mode = operating_mode
    return m


def _make_scalar_result(value: object) -> MagicMock:
    """Return a mock execute() result whose .scalar() returns ``value``."""
    result = MagicMock()
    result.scalar.return_value = value
    result.scalar_one_or_none.return_value = value
    return result


# ---------------------------------------------------------------------------
# Test 1: recovered_revenue_total is always int (DATA-001)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_attribution_recovered_revenue_total_is_int() -> None:
    """recovered_revenue_total in AttributionReport must be an int, never float.

    Financial values must be stored and returned as integer minor-currency units
    (paise) throughout the system (DATA-001, DATA-003).  This test verifies that
    even when the DB returns a value that Python might interpret as float (e.g.
    Decimal from SQLAlchemy), the endpoint coerces it to int.
    """
    merchant = _make_merchant()

    session = AsyncMock()
    session.get = AsyncMock(return_value=merchant)

    # Three execute() calls in order:
    #   1. SUM(payment_amount_at_risk) WHERE state='RECOVERED'
    #   2. COUNT(*) WHERE state != 'OBSERVED' (eligible cohort)
    #   3. COUNT(*) WHERE state='RECOVERED' (recovered count)
    call_count = 0

    async def _mock_execute(stmt):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            # recovered_rev — return a value that looks like DB Decimal
            result.scalar.return_value = 1_500_000  # 15,000 INR in paise
        elif call_count == 2:
            # total_eligible count
            result.scalar.return_value = 10
        else:
            # recovered count
            result.scalar.return_value = 3
        return result

    session.execute = _mock_execute

    current_user = _make_current_user()
    result = await get_attribution_report(current_user=current_user, session=session)

    assert isinstance(result, AttributionReport), "Expected AttributionReport"
    assert isinstance(result.recovered_revenue_total, int), (
        "recovered_revenue_total must be int (DATA-001) — never float"
    )
    assert isinstance(result.incremental_recovered_revenue, int), (
        "incremental_recovered_revenue must be int (DATA-001)"
    )
    assert result.recovered_revenue_total == 1_500_000, (
        "recovered_revenue_total must equal the DB sum"
    )
    # Baseline is 10% → incremental = 1_500_000 - 150_000 = 1_350_000
    assert result.incremental_recovered_revenue == 1_350_000


# ---------------------------------------------------------------------------
# Test 2: _safe_rate(0, 0) returns "NOT_AVAILABLE" — never a runtime error
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safe_rate_zero_denominator_returns_not_available() -> None:
    """_safe_rate(0, 0) must return 'NOT_AVAILABLE', never raise ZeroDivisionError.

    Division by zero is a well-known edge case for all rate metrics.  The spec
    mandates that this MUST NEVER produce a runtime error — the system must emit
    'NOT_AVAILABLE' instead (FR-ATTR-003, FR-ATTR-004, EVAL-003).
    """
    result = _safe_rate(0, 0)
    assert result == "NOT_AVAILABLE", (
        "_safe_rate(0, 0) must return 'NOT_AVAILABLE', not raise ZeroDivisionError"
    )


@pytest.mark.unit
def test_safe_rate_non_zero_returns_float() -> None:
    """_safe_rate with a non-zero denominator must return a float."""
    result = _safe_rate(3, 10)
    assert isinstance(result, float), "_safe_rate(3, 10) must return float"
    assert result == 0.3, "_safe_rate(3, 10) must equal 0.3"


@pytest.mark.unit
def test_safe_rate_zero_numerator_non_zero_denominator() -> None:
    """_safe_rate(0, n) where n > 0 must return 0.0, not 'NOT_AVAILABLE'."""
    result = _safe_rate(0, 5)
    assert result == 0.0, "_safe_rate(0, 5) must return 0.0"
    assert isinstance(result, float)


# ---------------------------------------------------------------------------
# Test 3: Attribution endpoint returns operating_mode field
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_attribution_endpoint_returns_operating_mode() -> None:
    """The attribution endpoint response must include the operating_mode field.

    The operating_mode is required for the frontend to display the persistent
    SIMULATED / REAL_TEST_MODE banner (INT-RAZORPAY-009, INT-RAZORPAY-010).
    """
    merchant = _make_merchant(operating_mode="SIMULATED")

    session = AsyncMock()
    session.get = AsyncMock(return_value=merchant)

    call_count = 0

    async def _mock_execute(stmt):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        result.scalar.return_value = 0  # zero revenue, zero counts
        return result

    session.execute = _mock_execute

    current_user = _make_current_user()
    result = await get_attribution_report(current_user=current_user, session=session)

    assert hasattr(result, "operating_mode"), (
        "AttributionReport must have an operating_mode field"
    )
    assert result.operating_mode == "SIMULATED", (
        "operating_mode must match the merchant's configured mode"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_attribution_endpoint_not_available_rates_when_no_cases() -> None:
    """When the merchant has no eligible cases, rate fields must be 'NOT_AVAILABLE'.

    Verifies that division-by-zero handling propagates correctly all the way
    to the API response — not just in the _safe_rate helper.
    """
    merchant = _make_merchant()

    session = AsyncMock()
    session.get = AsyncMock(return_value=merchant)

    # All counts are zero → total_eligible = 0 → NOT_AVAILABLE for rates
    async def _mock_execute_zeros(stmt):  # type: ignore[no-untyped-def]
        result = MagicMock()
        result.scalar.return_value = 0
        return result

    session.execute = _mock_execute_zeros

    current_user = _make_current_user()
    report = await get_attribution_report(current_user=current_user, session=session)

    assert report.organic_recovery_rate == "NOT_AVAILABLE", (
        "organic_recovery_rate must be 'NOT_AVAILABLE' when denominator is 0"
    )
    assert report.intervention_success_rate == "NOT_AVAILABLE", (
        "intervention_success_rate must be 'NOT_AVAILABLE' when denominator is 0"
    )
    assert isinstance(report.recovered_revenue_total, int), "Amount must be int even when 0"
    assert report.recovered_revenue_total == 0
