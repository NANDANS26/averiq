"""Property-Based Tests: Synthetic Data Generator Determinism (Property 12).

Property 12: Synthetic Data Generator Determinism
  For any seed S and count N, generate(S, N) called twice returns identical datasets.

Feature: averiq, Property 12: Synthetic Data Generator Determinism
Requirements: EVAL-006

**Validates: Requirements EVAL-006**
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from ai.data.generator import generate, SyntheticCase


@pytest.mark.pbt
@pytest.mark.unit
@given(
    seed=st.integers(min_value=0, max_value=2**31 - 1),
    count=st.integers(min_value=1, max_value=500),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_p12_generator_determinism(seed: int, count: int) -> None:
    """Feature: averiq, Property 12: Synthetic Data Generator Determinism

    For any (seed, count) pair, calling generate() twice returns identical results.
    This verifies that the generator is a pure function of (seed, count).

    **Validates: Requirements EVAL-006**
    """
    cases_a = generate(seed=seed, count=count)
    cases_b = generate(seed=seed, count=count)

    assert len(cases_a) == len(cases_b) == count

    for i, (a, b) in enumerate(zip(cases_a, cases_b)):
        assert a.amount_paise == b.amount_paise, f"amount_paise differs at index {i}"
        assert a.failure_category == b.failure_category, f"failure_category differs at index {i}"
        assert a.recovery_outcome == b.recovery_outcome, f"recovery_outcome differs at index {i}"
        assert a.payment_method == b.payment_method, f"payment_method differs at index {i}"
        assert a.hour_of_day == b.hour_of_day, f"hour_of_day differs at index {i}"
        assert a.day_of_week == b.day_of_week, f"day_of_week differs at index {i}"


@pytest.mark.pbt
@pytest.mark.unit
@given(seed=st.integers(min_value=0, max_value=2**31 - 1))
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_p12_count_1_is_always_valid(seed: int) -> None:
    """Even count=1 must be deterministic and return exactly 1 case.

    **Validates: Requirements EVAL-006**
    """
    result_a = generate(seed=seed, count=1)
    result_b = generate(seed=seed, count=1)
    assert len(result_a) == 1
    assert len(result_b) == 1
    assert result_a[0].recovery_outcome == result_b[0].recovery_outcome
    assert result_a[0].amount_paise == result_b[0].amount_paise


@pytest.mark.pbt
@pytest.mark.unit
@given(
    seed=st.integers(min_value=0, max_value=2**31 - 1),
    count=st.integers(min_value=1, max_value=200),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_p12_all_amounts_are_positive_integers(seed: int, count: int) -> None:
    """For any dataset, all amount_paise values must be positive integers (DATA-001).

    **Validates: Requirements DATA-001, EVAL-006**
    """
    cases = generate(seed=seed, count=count)
    for i, c in enumerate(cases):
        assert isinstance(c.amount_paise, int), f"Case {i}: amount_paise must be int"
        assert c.amount_paise > 0, f"Case {i}: amount_paise must be > 0"


@pytest.mark.pbt
@pytest.mark.unit
@given(
    seed=st.integers(min_value=0, max_value=2**31 - 1),
    count=st.integers(min_value=1, max_value=200),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_p12_recovery_outcome_is_binary(seed: int, count: int) -> None:
    """For any dataset, all recovery_outcome values must be bool (0 or 1).

    **Validates: Requirements EVAL-006, EVAL-008**
    """
    cases = generate(seed=seed, count=count)
    for i, c in enumerate(cases):
        assert isinstance(c.recovery_outcome, bool), f"Case {i}: recovery_outcome must be bool"
