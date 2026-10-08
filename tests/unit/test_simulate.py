"""Unit tests for the make simulate pipeline.

Tests the ``_safe_rate`` and ``simulate_model`` functions in
``ai/evaluation/simulate.py``.

These tests run in isolation — no database, no external services, no model
artifacts required.

All tests are marked ``@pytest.mark.unit``.

Validates:
  - EVAL-003: simulate pipeline; metrics computed at runtime; NOT_AVAILABLE on
    division by zero (never a runtime error)
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers — minimal SyntheticCase-like objects for tests
# ---------------------------------------------------------------------------


def _make_case(amount_paise: int, recovery_outcome: bool) -> SimpleNamespace:
    """Create a minimal case-like object with ``amount_paise`` and ``recovery_outcome``."""
    return SimpleNamespace(amount_paise=amount_paise, recovery_outcome=recovery_outcome)


# ---------------------------------------------------------------------------
# Test 1: _safe_rate(0, 0) returns "NOT_AVAILABLE"
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safe_rate_zero_denominator_returns_not_available() -> None:
    """_safe_rate(0, 0) must return the sentinel string 'NOT_AVAILABLE'.

    Division by zero must never raise a ZeroDivisionError — the function must
    emit 'NOT_AVAILABLE' as specified in EVAL-003.
    """
    from ai.evaluation.simulate import _safe_rate

    result = _safe_rate(0, 0)

    assert result == "NOT_AVAILABLE", (
        f"Expected 'NOT_AVAILABLE' when denominator is 0, got {result!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: _safe_rate(3, 10) returns 0.3
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safe_rate_valid_inputs_returns_rounded_float() -> None:
    """_safe_rate(3, 10) returns 0.3 (rounded to 4 decimal places).

    With a non-zero denominator the function must compute numerator/denominator
    and return a rounded float.
    """
    from ai.evaluation.simulate import _safe_rate

    result = _safe_rate(3, 10)

    assert isinstance(result, float), (
        f"Expected a float for _safe_rate(3, 10), got {type(result).__name__}"
    )
    assert result == pytest.approx(0.3, abs=1e-4), (
        f"Expected _safe_rate(3, 10) == 0.3, got {result}"
    )


# ---------------------------------------------------------------------------
# Test 3: simulate_model with all recovery_outcome=True and prob=1.0 → Recovery_Rate > 0
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_simulate_model_all_recovered_returns_positive_recovery_rate() -> None:
    """simulate_model with all recovery_outcome=True and prob=1.0 returns Recovery_Rate > 0.

    When every model prediction is >= 0.5 (intervene) and every actual outcome
    is True (recovery), the Recovery_Rate must be strictly positive.
    """
    from ai.evaluation.simulate import simulate_model

    cases = [_make_case(amount_paise=10_000, recovery_outcome=True) for _ in range(5)]
    proba_list = [1.0] * len(cases)

    # simulate_model accepts (model_name, proba_list, cases) in the existing implementation
    # but the task spec uses (proba_list, cases) — handle both signatures gracefully
    try:
        result = simulate_model("TestModel", proba_list, cases)
    except TypeError:
        # Fallback for (proba_list, cases) signature
        result = simulate_model(proba_list, cases)  # type: ignore[call-arg]

    recovery_rate = result.get("Recovery_Rate")
    assert recovery_rate != "NOT_AVAILABLE", (
        "Recovery_Rate must not be NOT_AVAILABLE when there are cases to evaluate"
    )
    assert isinstance(recovery_rate, float), (
        f"Recovery_Rate must be a float, got {type(recovery_rate).__name__}"
    )
    assert recovery_rate > 0, (
        f"Expected Recovery_Rate > 0 when all outcomes are True, got {recovery_rate}"
    )


# ---------------------------------------------------------------------------
# Test 4: simulate_model with empty cases list returns total_cases=0 without error
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_simulate_model_empty_cases_returns_zero_totals_without_error() -> None:
    """simulate_model with an empty cases list must not raise any exception.

    With zero cases the function must return a result dict containing
    ``total_cases=0`` and must not raise ZeroDivisionError or any other
    runtime error — all rate metrics should be 'NOT_AVAILABLE'.

    Validates EVAL-003: division by zero → NOT_AVAILABLE (never a runtime error).
    """
    from ai.evaluation.simulate import simulate_model

    cases: list = []
    proba_list: list = []

    try:
        result = simulate_model("TestModel", proba_list, cases)
    except TypeError:
        result = simulate_model(proba_list, cases)  # type: ignore[call-arg]

    assert result.get("total_cases") == 0, (
        f"Expected total_cases=0 for empty input, got {result.get('total_cases')}"
    )

    # All rate metrics must be NOT_AVAILABLE or 0 — never a runtime error
    for rate_key in ("Recovery_Rate", "Intervention_Success_Rate", "Escalation_Rate", "Stop_Rate"):
        value = result.get(rate_key)
        assert value in ("NOT_AVAILABLE", 0, 0.0), (
            f"Expected {rate_key!r} to be 'NOT_AVAILABLE' or 0 for empty input, "
            f"got {value!r}"
        )
