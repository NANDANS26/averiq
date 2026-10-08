"""Unit tests for the make train pipeline.

Tests the synthetic data generator, temporal split, and feature extraction
in isolation — no database or external service dependencies.

All tests are marked ``@pytest.mark.unit``.

Validates:
  - Requirements EVAL-001 (make train pipeline)
  - Requirements AI-MODEL-001 (model lifecycle: trained)
  - Requirements EVAL-006 (reproducibility: same seed → identical dataset)
  - Requirements FR-MODEL-005 (temporal split: non-overlapping, correct boundaries)
  - Requirements FR-MODEL-002 (feature extraction: RecoverabilityFeatures with feature_version)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure project root is in sys.path so all package imports resolve correctly
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Test 1: Reproducibility — same seed produces identical dataset
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_generate_reproducibility_same_seed() -> None:
    """generate(seed=42, count=100) called twice returns identical results.

    Validates:
      - EVAL-006: same seed → identical synthetic dataset on every invocation
      - Property 12 (design.md): Synthetic Data Generator Determinism
    """
    from ai.data.generator import generate

    cases_a = generate(seed=42, count=100)
    cases_b = generate(seed=42, count=100)

    assert len(cases_a) == len(cases_b) == 100

    for a, b in zip(cases_a, cases_b, strict=True):
        # Every field must be identical
        assert a.case_id == b.case_id, "case_id mismatch"
        assert a.merchant_id == b.merchant_id, "merchant_id mismatch"
        assert a.customer_id == b.customer_id, "customer_id mismatch"
        assert a.created_at == b.created_at, "created_at mismatch"
        assert a.amount_paise == b.amount_paise, "amount_paise mismatch"
        assert a.failure_category == b.failure_category, "failure_category mismatch"
        assert a.payment_method == b.payment_method, "payment_method mismatch"
        assert a.recovery_outcome == b.recovery_outcome, "recovery_outcome mismatch"


@pytest.mark.unit
def test_generate_different_seeds_produce_different_results() -> None:
    """Different seeds should produce different datasets.

    This is a sanity check — it would be extremely suspicious if two
    different seeds produced identical 100-case datasets.
    """
    from ai.data.generator import generate

    cases_42 = generate(seed=42, count=100)
    cases_99 = generate(seed=99, count=100)

    # At least some outcomes or amounts should differ
    outcomes_42 = [c.recovery_outcome for c in cases_42]
    outcomes_99 = [c.recovery_outcome for c in cases_99]
    assert outcomes_42 != outcomes_99, (
        "Different seeds produced identical recovery outcomes — "
        "the generator may not be using the seed correctly"
    )


# ---------------------------------------------------------------------------
# Test 2: Temporal split — non-overlapping, complete coverage
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_temporal_split_non_overlapping_summing_to_total() -> None:
    """temporal_split on 100 cases returns non-overlapping sets summing to 100.

    Validates:
      - FR-MODEL-005: temporal split; test set contains only cases after training cases
      - EVAL-009: documented non-overlapping time window boundaries
    """
    from ai.data.generator import generate
    from ai.training.feature_extraction import temporal_split, SPLIT_BOUNDARY_1, SPLIT_BOUNDARY_2

    cases = generate(seed=42, count=100)
    train_cases, val_cases, test_cases = temporal_split(cases)

    # The union must equal the full input
    total = len(train_cases) + len(val_cases) + len(test_cases)
    assert total == 100, f"Expected 100 total, got {total}"

    # Train cases must all be before SPLIT_BOUNDARY_1
    for c in train_cases:
        assert c.created_at < SPLIT_BOUNDARY_1, (
            f"Train case {c.case_id} has created_at={c.created_at} "
            f"which is not before SPLIT_BOUNDARY_1={SPLIT_BOUNDARY_1}"
        )

    # Val cases must be in [SPLIT_BOUNDARY_1, SPLIT_BOUNDARY_2)
    for c in val_cases:
        assert SPLIT_BOUNDARY_1 <= c.created_at < SPLIT_BOUNDARY_2, (
            f"Val case {c.case_id} has created_at={c.created_at} outside val window"
        )

    # Test cases must be at or after SPLIT_BOUNDARY_2
    for c in test_cases:
        assert c.created_at >= SPLIT_BOUNDARY_2, (
            f"Test case {c.case_id} has created_at={c.created_at} "
            f"before SPLIT_BOUNDARY_2={SPLIT_BOUNDARY_2}"
        )


@pytest.mark.unit
def test_temporal_split_no_overlap_between_splits() -> None:
    """No case appears in more than one split."""
    from ai.data.generator import generate
    from ai.training.feature_extraction import temporal_split

    cases = generate(seed=42, count=100)
    train_cases, val_cases, test_cases = temporal_split(cases)

    train_ids = {c.case_id for c in train_cases}
    val_ids = {c.case_id for c in val_cases}
    test_ids = {c.case_id for c in test_cases}

    # No intersection between any pair of splits
    assert train_ids.isdisjoint(val_ids), "Train and val splits overlap!"
    assert train_ids.isdisjoint(test_ids), "Train and test splits overlap!"
    assert val_ids.isdisjoint(test_ids), "Val and test splits overlap!"


# ---------------------------------------------------------------------------
# Test 3: Feature extraction — RecoverabilityFeatures with correct version
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_extract_features_returns_recoverable_features_with_version() -> None:
    """extract_features_from_synthetic returns RecoverabilityFeatures with feature_version='v1.0'.

    Validates:
      - FR-MODEL-002: features include required fields
      - feature_version field is correctly set to 'v1.0'
    """
    from ai.data.generator import generate
    from ai.training.feature_extraction import extract_features_from_synthetic, FEATURE_VERSION
    from shared.contracts.schemas import RecoverabilityFeatures

    cases = generate(seed=42, count=10)
    for case in cases:
        features = extract_features_from_synthetic(case)

        # Must return a RecoverabilityFeatures instance
        assert isinstance(features, RecoverabilityFeatures)

        # feature_version must be the current version
        assert features.feature_version == "v1.0", (
            f"Expected feature_version='v1.0', got '{features.feature_version}'"
        )
        assert features.feature_version == FEATURE_VERSION

        # Required fields must be present and valid
        assert features.amount_paise > 0, "amount_paise must be positive"
        assert features.currency == "INR", "currency must be INR"
        assert 0 <= features.hour_of_day <= 23, "hour_of_day out of range"
        assert 0 <= features.day_of_week <= 6, "day_of_week out of range"
        assert features.previous_failure_count >= 0
        assert features.time_since_failure_seconds >= 0.0
        assert isinstance(features.has_subscription, bool)


@pytest.mark.unit
def test_extract_features_consistent_feature_version() -> None:
    """All extracted features from the same dataset share the same feature_version."""
    from ai.data.generator import generate
    from ai.training.feature_extraction import extract_features_from_synthetic

    cases = generate(seed=7, count=50)
    versions = {extract_features_from_synthetic(c).feature_version for c in cases}

    assert len(versions) == 1, (
        f"Expected a single feature_version across all cases, got: {versions}"
    )
    assert "v1.0" in versions


@pytest.mark.unit
def test_extract_feature_matrix_returns_correct_shape() -> None:
    """extract_feature_matrix returns (X, y) with correct dimensions."""
    from ai.data.generator import generate
    from ai.training.feature_extraction import extract_feature_matrix

    cases = generate(seed=42, count=20)
    X, y = extract_feature_matrix(cases)

    assert len(X) == 20, f"Expected 20 rows, got {len(X)}"
    assert len(y) == 20, f"Expected 20 labels, got {len(y)}"

    # Each feature vector must have exactly 10 elements
    for i, vec in enumerate(X):
        assert len(vec) == 10, f"Row {i}: expected 10 features, got {len(vec)}"

    # Labels must be binary (0 or 1)
    for i, label in enumerate(y):
        assert label in (0, 1), f"Label {i}: expected 0 or 1, got {label}"


# ---------------------------------------------------------------------------
# Test 4: Generate minimum count (smoke test)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_generate_minimum_count() -> None:
    """generate(seed=42, count=1) returns exactly 1 case without error."""
    from ai.data.generator import generate

    cases = generate(seed=42, count=1)
    assert len(cases) == 1
    assert cases[0].currency == "INR"
    assert cases[0].amount_paise > 0


@pytest.mark.unit
def test_generate_invalid_count_raises() -> None:
    """generate(seed=42, count=0) raises ValueError."""
    from ai.data.generator import generate

    with pytest.raises(ValueError, match="count must be >= 1"):
        generate(seed=42, count=0)


# ---------------------------------------------------------------------------
# Test 5: No label leakage — outcome is not deterministic from amount alone
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_no_trivial_label_leakage_recovery_varies() -> None:
    """Recovery outcome varies across cases — it is not trivially deterministic.

    This is a statistical smoke test for label leakage: if all cases had the
    same recovery_outcome, something is clearly wrong.  A proper label
    leakage test would use information gain, but this basic check catches
    degenerate generators.
    """
    from ai.data.generator import generate

    cases = generate(seed=42, count=200)
    outcomes = [c.recovery_outcome for c in cases]

    n_positive = sum(outcomes)
    n_negative = len(outcomes) - n_positive

    # Both classes must be present in a dataset of 200 cases
    assert n_positive > 0, "All cases are negative — label generation may be broken"
    assert n_negative > 0, "All cases are positive — label generation may be broken"

    # Realistic range check: recovery rate should be between 5% and 95%
    recovery_rate = n_positive / len(outcomes)
    assert 0.05 <= recovery_rate <= 0.95, (
        f"Recovery rate {recovery_rate:.1%} is outside the realistic range [5%, 95%]. "
        "Check the label generation function."
    )
