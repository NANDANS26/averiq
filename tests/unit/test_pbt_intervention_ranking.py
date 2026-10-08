"""Property-Based Tests: Intervention Ranking Invariant (Property 10).

Property 10: Intervention Ranking Invariant
  For any non-empty list of candidates, after rank() the list is sorted descending by EV.

Feature: averiq, Property 10: Intervention Ranking Invariant

**Validates: Requirements FR-INTVN-004**
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shared.contracts.enums import ExecutionCapability, InterventionType
from shared.contracts.schemas import InterventionCandidate
from backend.decision_engine.ranking import rank_candidates


# ---------------------------------------------------------------------------
# Property 10: ranked list is sorted descending by expected_recovery_value
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(
    st.lists(
        st.builds(
            InterventionCandidate,
            intervention_type=st.sampled_from(InterventionType),
            execution_capability=st.sampled_from(ExecutionCapability),
            recovery_probability=st.floats(
                min_value=0.0,
                max_value=1.0,
                allow_nan=False,
                allow_infinity=False,
            ),
            expected_recovery_value=st.integers(
                min_value=-1_000_000, max_value=10_000_000
            ),
            model_version=st.just("v1.0"),
        ),
        min_size=1,
    )
)
@settings(max_examples=300)
def test_p10_ranked_list_is_sorted_descending(
    candidates: list[InterventionCandidate],
) -> None:
    """For any non-empty list of candidates, rank_candidates returns a list sorted
    descending by expected_recovery_value.

    Feature: averiq, Property 10: Intervention Ranking Invariant

    **Validates: Requirements FR-INTVN-004**

    Invariant: candidates[i].expected_recovery_value >= candidates[i+1].expected_recovery_value
    for all valid i in the ranked list.
    """
    ranked = rank_candidates(candidates)

    # Length must be preserved
    assert len(ranked) == len(candidates), (
        f"rank_candidates changed list length: {len(candidates)} -> {len(ranked)}"
    )

    # Must be sorted in descending order
    for i in range(len(ranked) - 1):
        assert ranked[i].expected_recovery_value >= ranked[i + 1].expected_recovery_value, (
            f"Ranking violated at index {i}: "
            f"ranked[{i}].expected_recovery_value={ranked[i].expected_recovery_value} "
            f"< ranked[{i+1}].expected_recovery_value={ranked[i+1].expected_recovery_value}"
        )
