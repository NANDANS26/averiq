"""Unit tests for intervention candidate ranking.

Tests for backend/decision_engine/ranking.py.
Requirements: FR-INTVN-004.
"""
from __future__ import annotations

import pytest

from shared.contracts.enums import ExecutionCapability, InterventionType
from shared.contracts.schemas import InterventionCandidate
from backend.decision_engine.ranking import rank_candidates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(
    ev: int,
    intervention_type: InterventionType = InterventionType.PAYMENT_LINK,
    execution_capability: ExecutionCapability = ExecutionCapability.CREATE_PAYMENT_LINK,
) -> InterventionCandidate:
    """Create a minimal InterventionCandidate with the given expected_recovery_value."""
    return InterventionCandidate(
        intervention_type=intervention_type,
        execution_capability=execution_capability,
        recovery_probability=0.5,
        expected_recovery_value=ev,
        model_version="v1.0",
    )


# ---------------------------------------------------------------------------
# Test 1: Sorted descending by expected_recovery_value
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rank_candidates_sorted_descending() -> None:
    """Candidates are sorted in descending order of expected_recovery_value."""
    candidates = [
        _make_candidate(1000),
        _make_candidate(5000),
        _make_candidate(2500),
    ]
    ranked = rank_candidates(candidates)
    assert [c.expected_recovery_value for c in ranked] == [5000, 2500, 1000]


# ---------------------------------------------------------------------------
# Test 2: Empty list returns empty list
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rank_candidates_empty_list() -> None:
    """Empty input returns an empty list."""
    result = rank_candidates([])
    assert result == []


# ---------------------------------------------------------------------------
# Test 3: Single item returns single-item list
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rank_candidates_single_item() -> None:
    """A single-element list is returned unchanged."""
    candidate = _make_candidate(42_000)
    result = rank_candidates([candidate])
    assert len(result) == 1
    assert result[0].expected_recovery_value == 42_000


# ---------------------------------------------------------------------------
# Test 4: Equal EV preserves insertion order (stable sort)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rank_candidates_stable_sort_for_equal_ev() -> None:
    """Candidates with equal EV preserve their relative insertion order."""
    # Use different intervention types to distinguish them after sorting
    c1 = InterventionCandidate(
        intervention_type=InterventionType.PAYMENT_LINK,
        execution_capability=ExecutionCapability.CREATE_PAYMENT_LINK,
        recovery_probability=0.5,
        expected_recovery_value=3000,
        model_version="v1.0",
    )
    c2 = InterventionCandidate(
        intervention_type=InterventionType.RETRY,
        execution_capability=ExecutionCapability.NO_OP,
        recovery_probability=0.4,
        expected_recovery_value=3000,
        model_version="v1.0",
    )
    c3 = InterventionCandidate(
        intervention_type=InterventionType.STOP,
        execution_capability=ExecutionCapability.NO_OP,
        recovery_probability=0.0,
        expected_recovery_value=3000,
        model_version="v1.0",
    )
    ranked = rank_candidates([c1, c2, c3])
    # All have equal EV; insertion order must be preserved
    assert ranked[0].intervention_type == InterventionType.PAYMENT_LINK
    assert ranked[1].intervention_type == InterventionType.RETRY
    assert ranked[2].intervention_type == InterventionType.STOP


# ---------------------------------------------------------------------------
# Test 5: Original list is not mutated
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rank_candidates_does_not_mutate_original() -> None:
    """rank_candidates returns a new list and does not modify the input list."""
    original = [
        _make_candidate(100),
        _make_candidate(500),
        _make_candidate(300),
    ]
    original_order = [c.expected_recovery_value for c in original]

    rank_candidates(original)

    # Original list order must be unchanged
    assert [c.expected_recovery_value for c in original] == original_order


# ---------------------------------------------------------------------------
# Test 6: Works with negative EV values (STOP has EV=0, others may be negative)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rank_candidates_handles_negative_ev() -> None:
    """Negative EV values are valid and sorted correctly (descending)."""
    stop_candidate = InterventionCandidate(
        intervention_type=InterventionType.STOP,
        execution_capability=ExecutionCapability.NO_OP,
        recovery_probability=0.0,
        expected_recovery_value=0,
        model_version="v1.0",
    )
    negative_ev = _make_candidate(-5_000)
    positive_ev = _make_candidate(10_000)

    ranked = rank_candidates([negative_ev, stop_candidate, positive_ev])

    assert ranked[0].expected_recovery_value == 10_000
    assert ranked[1].expected_recovery_value == 0
    assert ranked[2].expected_recovery_value == -5_000
