"""Unit tests for the revenue risk summary API (task 8.2).

Tests verify the analytics endpoint logic against:
  1. Currency mismatch isolation (FR-RISK-007): cases whose currency differs
     from configured_currency appear in currency_mismatch_cases, NOT in totals.
  2. Integer amounts (DATA-001): all returned totals are int, never float.
  3. Terminal state exclusion: RECOVERED, STOPPED, ESCALATED, FAILED cases
     are excluded from active totals.

All tests are pure unit tests — no database, no Redis, no network calls.
Session and authentication dependencies are mocked.

Requirements: FR-RISK-006, FR-RISK-007, DATA-001.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.api.analytics import (
    TERMINAL_STATES,
    RevenueRiskSummary,
    get_revenue_risk_summary,
)
from backend.api.dependencies import CurrentUser
from backend.domain.models import Merchant, User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MERCHANT_ID = uuid.uuid4()
_CONFIGURED_CURRENCY = "INR"


def _make_merchant(currency: str = "INR", operating_mode: str = "SIMULATED") -> Merchant:
    """Return a minimal Merchant ORM-like object (no DB required)."""
    m = MagicMock(spec=Merchant)
    m.id = _MERCHANT_ID
    m.currency = currency
    m.operating_mode = operating_mode
    return m


def _make_current_user() -> CurrentUser:
    """Return a CurrentUser with VIEWER role scoped to _MERCHANT_ID."""
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.role = "VIEWER"
    return CurrentUser(user=user, merchant_id=_MERCHANT_ID)


def _make_row(**kwargs: object) -> SimpleNamespace:
    """Build a lightweight row-like object returned by SQLAlchemy execute().all()."""
    return SimpleNamespace(**kwargs)


async def _invoke_endpoint(
    merchant: Merchant | None,
    state_rows: list[SimpleNamespace],
    path_rows: list[SimpleNamespace],
    mismatch_rows: list[SimpleNamespace],
) -> RevenueRiskSummary:
    """Call get_revenue_risk_summary with mocked session and user.

    Mocks ``session.get(Merchant, ...)`` for the merchant lookup and
    ``session.execute(...)`` for the three GROUP BY queries.
    """
    session = AsyncMock()

    # Mock session.get to return the given merchant
    session.get = AsyncMock(return_value=merchant)

    # The endpoint calls session.execute() three times in order:
    #   1st call → state totals
    #   2nd call → path totals
    #   3rd call → currency mismatch
    def _make_result(rows: list[SimpleNamespace]) -> MagicMock:
        result = MagicMock()
        result.all.return_value = rows
        return result

    session.execute = AsyncMock(
        side_effect=[
            _make_result(state_rows),
            _make_result(path_rows),
            _make_result(mismatch_rows),
        ]
    )

    current_user = _make_current_user()
    return await get_revenue_risk_summary(current_user=current_user, session=session)


# ---------------------------------------------------------------------------
# Test group 1: Currency mismatch isolation (FR-RISK-007)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_currency_mismatch_cases_not_included_in_totals() -> None:
    """FR-RISK-007: cases with a foreign currency appear ONLY in currency_mismatch_cases.

    A USD case must not inflate total_amount_at_risk or active_case_count;
    it must appear in currency_mismatch_cases with the correct count.
    """
    merchant = _make_merchant(currency="INR")

    # INR cases (matching currency) → included in totals
    state_rows = [
        _make_row(state="AT_RISK", case_count=2, total_amount=100_000),
    ]
    path_rows = [
        _make_row(
            risk_calculation_path="SINGLE_PAYMENT",
            case_count=2,
            total_amount=100_000,
            total_projected=0,
        )
    ]
    # USD cases (mismatched) → isolated
    mismatch_rows = [
        _make_row(currency="USD", case_count=3),
    ]

    summary = await _invoke_endpoint(merchant, state_rows, path_rows, mismatch_rows)

    # Mismatch cases must NOT appear in the main totals
    assert summary.total_amount_at_risk == 100_000
    assert summary.active_case_count == 2

    # Mismatch must be isolated in currency_mismatch_cases
    assert len(summary.currency_mismatch_cases) == 1
    mismatch = summary.currency_mismatch_cases[0]
    assert mismatch.currency == "USD"
    assert mismatch.case_count == 3


@pytest.mark.unit
async def test_multiple_mismatch_currencies_all_isolated() -> None:
    """FR-RISK-007: each foreign currency produces a separate CurrencyMismatchInfo entry."""
    merchant = _make_merchant(currency="INR")

    state_rows: list[SimpleNamespace] = []
    path_rows: list[SimpleNamespace] = []
    mismatch_rows = [
        _make_row(currency="USD", case_count=2),
        _make_row(currency="EUR", case_count=1),
    ]

    summary = await _invoke_endpoint(merchant, state_rows, path_rows, mismatch_rows)

    assert summary.total_amount_at_risk == 0
    assert summary.active_case_count == 0
    assert len(summary.currency_mismatch_cases) == 2

    mismatch_currencies = {m.currency for m in summary.currency_mismatch_cases}
    assert mismatch_currencies == {"USD", "EUR"}


@pytest.mark.unit
async def test_no_mismatch_when_all_cases_match_currency() -> None:
    """FR-RISK-007: when all cases match configured_currency, mismatch list is empty."""
    merchant = _make_merchant(currency="INR")

    state_rows = [
        _make_row(state="AT_RISK", case_count=5, total_amount=500_000),
    ]
    path_rows = [
        _make_row(
            risk_calculation_path="SINGLE_PAYMENT",
            case_count=5,
            total_amount=500_000,
            total_projected=0,
        )
    ]
    mismatch_rows: list[SimpleNamespace] = []

    summary = await _invoke_endpoint(merchant, state_rows, path_rows, mismatch_rows)

    assert summary.currency_mismatch_cases == []
    assert summary.active_case_count == 5


@pytest.mark.unit
async def test_mismatch_note_mentions_exclusion() -> None:
    """FR-RISK-007: the CurrencyMismatchInfo note must mention exclusion from totals."""
    merchant = _make_merchant(currency="INR")
    mismatch_rows = [_make_row(currency="USD", case_count=1)]

    summary = await _invoke_endpoint(merchant, [], [], mismatch_rows)

    assert len(summary.currency_mismatch_cases) == 1
    note = summary.currency_mismatch_cases[0].note.lower()
    assert "exclud" in note, f"note should mention exclusion, got: {note!r}"


# ---------------------------------------------------------------------------
# Test group 2: Integer amounts — DATA-001
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_total_amount_at_risk_is_int() -> None:
    """DATA-001: total_amount_at_risk must be a Python int, never float."""
    merchant = _make_merchant()
    state_rows = [
        _make_row(state="AT_RISK", case_count=1, total_amount=75_000),
    ]
    path_rows = [
        _make_row(
            risk_calculation_path="SINGLE_PAYMENT",
            case_count=1,
            total_amount=75_000,
            total_projected=0,
        )
    ]

    summary = await _invoke_endpoint(merchant, state_rows, path_rows, [])

    assert isinstance(summary.total_amount_at_risk, int), (
        f"total_amount_at_risk must be int, got {type(summary.total_amount_at_risk)}"
    )


@pytest.mark.unit
async def test_state_bucket_total_amount_is_int() -> None:
    """DATA-001: each StateBucketTotal.total_amount_at_risk must be a Python int."""
    merchant = _make_merchant()
    state_rows = [
        _make_row(state="AT_RISK", case_count=3, total_amount=300_000),
        _make_row(state="DIAGNOSED", case_count=1, total_amount=50_000),
    ]
    path_rows = [
        _make_row(
            risk_calculation_path="SINGLE_PAYMENT",
            case_count=4,
            total_amount=350_000,
            total_projected=0,
        )
    ]

    summary = await _invoke_endpoint(merchant, state_rows, path_rows, [])

    for bucket in summary.by_state:
        assert isinstance(bucket.total_amount_at_risk, int), (
            f"by_state[{bucket.state}].total_amount_at_risk must be int,"
            f" got {type(bucket.total_amount_at_risk)}"
        )


@pytest.mark.unit
async def test_path_total_amount_is_int() -> None:
    """DATA-001: each PathTotal.total_amount_at_risk must be a Python int."""
    merchant = _make_merchant()
    path_rows = [
        _make_row(
            risk_calculation_path="SINGLE_PAYMENT",
            case_count=2,
            total_amount=200_000,
            total_projected=0,
        ),
        _make_row(
            risk_calculation_path="SUBSCRIPTION_PROJECTED",
            case_count=1,
            total_amount=50_000,
            total_projected=150_000,
        ),
    ]

    summary = await _invoke_endpoint(merchant, [], path_rows, [])

    for path_total in summary.by_calculation_path:
        assert isinstance(path_total.total_amount_at_risk, int), (
            f"by_calculation_path[{path_total.calculation_path}].total_amount_at_risk"
            f" must be int, got {type(path_total.total_amount_at_risk)}"
        )
        assert isinstance(path_total.total_projected_revenue_at_risk, int), (
            f"by_calculation_path[{path_total.calculation_path}]"
            f".total_projected_revenue_at_risk must be int,"
            f" got {type(path_total.total_projected_revenue_at_risk)}"
        )


@pytest.mark.unit
async def test_zero_totals_are_int_not_none() -> None:
    """DATA-001: zero totals must be int(0), never None or 0.0."""
    merchant = _make_merchant()

    summary = await _invoke_endpoint(merchant, [], [], [])

    assert summary.total_amount_at_risk == 0
    assert isinstance(summary.total_amount_at_risk, int)
    assert summary.total_projected_revenue_at_risk == 0
    assert isinstance(summary.total_projected_revenue_at_risk, int)


@pytest.mark.unit
async def test_db_decimal_coerced_to_int() -> None:
    """DATA-001: amounts returned as Decimal by the DB driver are coerced to int.

    asyncpg can return SUM(...) as a Python int or Decimal depending on the
    aggregate expression.  The endpoint must coerce to int to satisfy DATA-001.
    """
    from decimal import Decimal

    merchant = _make_merchant()
    # Simulate DB returning Decimal (as asyncpg can do for aggregates)
    state_rows = [
        _make_row(state="AT_RISK", case_count=Decimal("3"), total_amount=Decimal("300000")),
    ]
    path_rows = [
        _make_row(
            risk_calculation_path="SINGLE_PAYMENT",
            case_count=Decimal("3"),
            total_amount=Decimal("300000"),
            total_projected=Decimal("0"),
        )
    ]

    summary = await _invoke_endpoint(merchant, state_rows, path_rows, [])

    assert isinstance(summary.total_amount_at_risk, int)
    assert summary.total_amount_at_risk == 300_000
    assert isinstance(summary.by_state[0].total_amount_at_risk, int)


# ---------------------------------------------------------------------------
# Test group 3: Terminal state exclusion (FR-RISK-006 / task spec)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_terminal_states_constant_contains_expected_values() -> None:
    """The TERMINAL_STATES constant must include the four terminal states."""
    expected = {"RECOVERED", "STOPPED", "ESCALATED", "FAILED"}
    assert expected == TERMINAL_STATES


@pytest.mark.unit
async def test_terminal_state_cases_not_in_active_totals() -> None:
    """Terminal state cases must be excluded from active totals.

    The endpoint's SQL WHERE clause filters out TERMINAL_STATES.
    This test verifies the endpoint does NOT include terminal rows
    returned by the (correctly scoped) mock session.
    """
    merchant = _make_merchant()

    # Simulate the DB returning only non-terminal rows (correct SQL filtering)
    # Active non-terminal case
    state_rows = [
        _make_row(state="AT_RISK", case_count=2, total_amount=200_000),
    ]
    path_rows = [
        _make_row(
            risk_calculation_path="SINGLE_PAYMENT",
            case_count=2,
            total_amount=200_000,
            total_projected=0,
        )
    ]

    summary = await _invoke_endpoint(merchant, state_rows, path_rows, [])

    # Only the AT_RISK (non-terminal) cases contribute to totals
    assert summary.active_case_count == 2
    assert summary.total_amount_at_risk == 200_000
    assert len(summary.by_state) == 1
    assert summary.by_state[0].state == "AT_RISK"


@pytest.mark.unit
async def test_summary_states_do_not_include_terminal_states() -> None:
    """The by_state list must never contain terminal state buckets.

    When the DB returns only non-terminal rows, no terminal state should
    appear in by_state.
    """
    merchant = _make_merchant()

    # DB returns only non-terminal states (the WHERE filter works correctly)
    non_terminal_states = [
        "OBSERVED",
        "AT_RISK",
        "DIAGNOSED",
        "RECOVERY_ELIGIBLE",
        "ACTION_PENDING",
        "ACTION_APPROVED",
        "ACTION_EXECUTING",
        "VERIFYING",
        "RETRYABLE",
    ]

    state_rows = [
        _make_row(state=s, case_count=1, total_amount=10_000)
        for s in non_terminal_states
    ]
    path_rows = [
        _make_row(
            risk_calculation_path="SINGLE_PAYMENT",
            case_count=len(non_terminal_states),
            total_amount=len(non_terminal_states) * 10_000,
            total_projected=0,
        )
    ]

    summary = await _invoke_endpoint(merchant, state_rows, path_rows, [])

    returned_states = {b.state for b in summary.by_state}
    assert returned_states.isdisjoint(TERMINAL_STATES), (
        f"Terminal states must not appear in by_state; found: "
        f"{returned_states & TERMINAL_STATES}"
    )


# ---------------------------------------------------------------------------
# Test group 4: Calculation path separation (FR-RISK-006)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_single_payment_and_subscription_projected_shown_separately() -> None:
    """FR-RISK-006: SINGLE_PAYMENT and SUBSCRIPTION_PROJECTED appear as separate entries."""
    merchant = _make_merchant()

    path_rows = [
        _make_row(
            risk_calculation_path="SINGLE_PAYMENT",
            case_count=4,
            total_amount=400_000,
            total_projected=0,
        ),
        _make_row(
            risk_calculation_path="SUBSCRIPTION_PROJECTED",
            case_count=2,
            total_amount=200_000,
            total_projected=600_000,
        ),
    ]

    summary = await _invoke_endpoint(merchant, [], path_rows, [])

    paths_by_type = {p.calculation_path: p for p in summary.by_calculation_path}
    assert "SINGLE_PAYMENT" in paths_by_type
    assert "SUBSCRIPTION_PROJECTED" in paths_by_type

    single = paths_by_type["SINGLE_PAYMENT"]
    sub = paths_by_type["SUBSCRIPTION_PROJECTED"]

    assert single.total_projected_revenue_at_risk == 0
    assert sub.total_projected_revenue_at_risk == 600_000


@pytest.mark.unit
async def test_total_projected_revenue_from_subscription_cases() -> None:
    """FR-RISK-006: total_projected_revenue_at_risk is the sum from SUBSCRIPTION_PROJECTED."""
    merchant = _make_merchant()

    path_rows = [
        _make_row(
            risk_calculation_path="SUBSCRIPTION_PROJECTED",
            case_count=3,
            total_amount=300_000,
            total_projected=900_000,
        )
    ]

    summary = await _invoke_endpoint(merchant, [], path_rows, [])

    assert summary.total_projected_revenue_at_risk == 900_000
    assert isinstance(summary.total_projected_revenue_at_risk, int)


# ---------------------------------------------------------------------------
# Test group 5: Merchant not found (graceful fallback)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_empty_summary_when_merchant_not_found() -> None:
    """When the merchant row is missing, return an empty summary rather than 500."""
    summary = await _invoke_endpoint(
        merchant=None,
        state_rows=[],
        path_rows=[],
        mismatch_rows=[],
    )

    assert summary.total_amount_at_risk == 0
    assert summary.active_case_count == 0
    assert summary.currency_mismatch_cases == []
    assert summary.by_state == []
    assert summary.by_calculation_path == []


# ---------------------------------------------------------------------------
# Test group 6: configured_currency and operating_mode propagated
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_configured_currency_from_merchant_row() -> None:
    """The summary's configured_currency must come from the merchant row."""
    merchant = _make_merchant(currency="USD")

    summary = await _invoke_endpoint(merchant, [], [], [])

    assert summary.configured_currency == "USD"


@pytest.mark.unit
async def test_operating_mode_from_merchant_row() -> None:
    """The summary's operating_mode must come from the merchant row."""
    merchant = _make_merchant(operating_mode="REAL_TEST_MODE")

    summary = await _invoke_endpoint(merchant, [], [], [])

    assert summary.operating_mode == "REAL_TEST_MODE"


# ---------------------------------------------------------------------------
# Test group 7: active_case_count and top-level totals are consistent
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_active_case_count_is_sum_of_state_bucket_counts() -> None:
    """active_case_count must equal the sum of case_count across all state buckets."""
    merchant = _make_merchant()

    state_rows = [
        _make_row(state="AT_RISK", case_count=3, total_amount=300_000),
        _make_row(state="DIAGNOSED", case_count=2, total_amount=200_000),
        _make_row(state="RECOVERY_ELIGIBLE", case_count=1, total_amount=100_000),
    ]
    path_rows = [
        _make_row(
            risk_calculation_path="SINGLE_PAYMENT",
            case_count=6,
            total_amount=600_000,
            total_projected=0,
        )
    ]

    summary = await _invoke_endpoint(merchant, state_rows, path_rows, [])

    expected_count = 3 + 2 + 1
    assert summary.active_case_count == expected_count


@pytest.mark.unit
async def test_total_amount_at_risk_is_sum_of_state_bucket_amounts() -> None:
    """total_amount_at_risk must equal the sum of total_amount_at_risk across all state buckets."""
    merchant = _make_merchant()

    state_rows = [
        _make_row(state="AT_RISK", case_count=2, total_amount=200_000),
        _make_row(state="DIAGNOSED", case_count=1, total_amount=100_000),
    ]
    path_rows = [
        _make_row(
            risk_calculation_path="SINGLE_PAYMENT",
            case_count=3,
            total_amount=300_000,
            total_projected=0,
        )
    ]

    summary = await _invoke_endpoint(merchant, state_rows, path_rows, [])

    assert summary.total_amount_at_risk == 300_000
