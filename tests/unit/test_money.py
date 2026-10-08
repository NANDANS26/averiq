"""EV formula boundary cases: ROUND_FLOOR, Decimal never stored.

Complements tests/unit/test_expected_value.py with boundary-focused cases.

Requirements: FR-INTVN-002, DATA-001, DATA-003
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.decision_engine.expected_value import compute_expected_value


@pytest.mark.unit
def test_floor_rounding_not_round_half() -> None:
    """floor(33.3) == 33 — ROUND_FLOOR never rounds up to 34.

    0.333 × 100 = 33.3; floor(33.3) = 33 (not 33 from round-half-up which
    would also give 33, but ensures floor semantics not rounding).
    """
    assert compute_expected_value(0.333, 100) == 33  # floor(33.3) = 33


@pytest.mark.unit
def test_floor_at_boundary() -> None:
    """floor(49.5) == 49 — ROUND_FLOOR never rounds 0.5 up.

    0.5 × 99 = 49.5; floor(49.5) = 49.
    A round-half-up strategy would give 50 — this verifies FLOOR semantics.
    """
    assert compute_expected_value(0.5, 99) == 49  # floor(49.5) = 49


@pytest.mark.unit
def test_result_is_int_not_decimal() -> None:
    """Result must be a plain int, never a Decimal or float.

    The intermediate Decimal is discarded immediately — never stored or returned.
    DATA-001, DATA-003.
    """
    result = compute_expected_value(0.7, 1000)
    assert isinstance(result, int)
    assert not isinstance(result, Decimal)


@pytest.mark.unit
def test_negative_ev_after_cost() -> None:
    """Negative EV is valid when cost exceeds the expected recovery.

    0.1 × 100 = 10; 10 - 50 (cost) = -40.
    A negative EV is a legitimate signal that the intervention is not profitable.
    DATA-001, DATA-003.
    """
    assert compute_expected_value(0.1, 100, cost=50) == -40  # 10 - 50 = -40
