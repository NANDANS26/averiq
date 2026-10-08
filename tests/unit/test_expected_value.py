"""Unit tests for expected value (EV) computation.

Tests for backend/decision_engine/expected_value.py.
Requirements: FR-INTVN-002, DATA-001, DATA-003.
"""
from __future__ import annotations

import pytest

from backend.decision_engine.expected_value import compute_expected_value


# ---------------------------------------------------------------------------
# Test 1: Basic 50% probability
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ev_half_probability() -> None:
    """compute_expected_value(0.5, 100000) == 50000."""
    result = compute_expected_value(0.5, 100_000)
    assert result == 50_000


# ---------------------------------------------------------------------------
# Test 2: Full probability (1.0)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ev_full_probability() -> None:
    """compute_expected_value(1.0, 100000) == 100000."""
    result = compute_expected_value(1.0, 100_000)
    assert result == 100_000


# ---------------------------------------------------------------------------
# Test 3: Zero probability
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ev_zero_probability() -> None:
    """compute_expected_value(0.0, 100000) == 0."""
    result = compute_expected_value(0.0, 100_000)
    assert result == 0


# ---------------------------------------------------------------------------
# Test 4: Returns int always (not float, not Decimal)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ev_returns_int_type() -> None:
    """Result must be int — never float, never Decimal."""
    result = compute_expected_value(0.5, 100_000)
    assert isinstance(result, int), f"Expected int, got {type(result)}"
    assert not isinstance(result, float), "Must not return float"


# ---------------------------------------------------------------------------
# Test 5: Cost is subtracted
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ev_cost_subtracted() -> None:
    """compute_expected_value(1.0, 100000, cost=5000) == 95000."""
    result = compute_expected_value(1.0, 100_000, cost=5_000)
    assert result == 95_000


# ---------------------------------------------------------------------------
# Test 6: Risk penalty is subtracted
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ev_risk_penalty_subtracted() -> None:
    """compute_expected_value(1.0, 100000, risk_penalty=3000) == 97000."""
    result = compute_expected_value(1.0, 100_000, risk_penalty=3_000)
    assert result == 97_000


# ---------------------------------------------------------------------------
# Test 7: Uses ROUND_FLOOR (not round-half-up)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ev_uses_round_floor() -> None:
    """compute_expected_value(0.333, 100) == 33 (floor, not round to 33.3 → 33)."""
    # 0.333 * 100 = 33.3; floor(33.3) = 33
    result = compute_expected_value(0.333, 100)
    assert result == 33, f"Expected 33 (ROUND_FLOOR), got {result}"


# ---------------------------------------------------------------------------
# Test 8: Raises ValueError for probability > 1.0
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ev_raises_for_probability_above_one() -> None:
    """ValueError when recovery_probability > 1.0."""
    with pytest.raises(ValueError, match="recovery_probability"):
        compute_expected_value(1.1, 100_000)


# ---------------------------------------------------------------------------
# Test 9: Raises ValueError for amount <= 0
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ev_raises_for_non_positive_amount() -> None:
    """ValueError when payment_amount_at_risk <= 0."""
    with pytest.raises(ValueError, match="payment_amount_at_risk"):
        compute_expected_value(0.5, 0)

    with pytest.raises(ValueError, match="payment_amount_at_risk"):
        compute_expected_value(0.5, -1)


# ---------------------------------------------------------------------------
# Test 10: Result of floor(0.1 × 99999) is int
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ev_fractional_floor_is_int() -> None:
    """floor(0.1 × 99999) must be an int (not Decimal, not float)."""
    # 0.1 * 99999 = 9999.9; floor = 9999
    result = compute_expected_value(0.1, 99_999)
    assert isinstance(result, int), f"Expected int, got {type(result)}"
    assert result == 9_999, f"Expected 9999, got {result}"
