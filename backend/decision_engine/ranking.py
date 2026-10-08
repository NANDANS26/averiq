"""Intervention candidate ranking.

Sorts InterventionCandidate objects by expected_recovery_value descending.
Candidates with equal EV preserve their relative insertion order (stable sort).

The ranked list is passed to PolicyEngine for evaluation.
EV is computed before ranking — PolicyEngine filtering happens AFTER ranking.

Requirements: FR-INTVN-004
"""
from __future__ import annotations

from shared.contracts.schemas import InterventionCandidate


def rank_candidates(
    candidates: list[InterventionCandidate],
) -> list[InterventionCandidate]:
    """Sort candidates by expected_recovery_value descending (stable sort).

    Args:
        candidates: list of InterventionCandidate objects with EV already computed.

    Returns:
        New list sorted descending by expected_recovery_value.
        Original list is not mutated.
        Empty list returns empty list.
    """
    return sorted(candidates, key=lambda c: c.expected_recovery_value, reverse=True)
