"""Property-Based Tests: Recoverability Model Determinism (Property 4).

Property 4: Recoverability Model Determinism
  For any valid RecoverabilityFeatures and fixed artifact, predict(features) called twice
  returns equal scores within documented tolerance.

Feature: averiq, Property 4: Recoverability Model Determinism
Requirements: FR-MODEL-010, NFR-TEST-006

**Validates: Requirements FR-MODEL-010, NFR-TEST-006**
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from shared.contracts.enums import FailureCategory
from shared.contracts.schemas import RecoverabilityFeatures

# ---------------------------------------------------------------------------
# Shared strategy for generating RecoverabilityFeatures
# ---------------------------------------------------------------------------

recoverable_features_strategy = st.builds(
    RecoverabilityFeatures,
    amount_paise=st.integers(min_value=1, max_value=10_000_000),
    currency=st.just("INR"),
    failure_category=st.sampled_from(FailureCategory),
    payment_method=st.one_of(st.none(), st.sampled_from(["card", "upi", "netbanking", "wallet"])),
    time_since_failure_seconds=st.floats(
        min_value=0.0, max_value=86400.0, allow_nan=False, allow_infinity=False
    ),
    hour_of_day=st.integers(min_value=0, max_value=23),
    day_of_week=st.integers(min_value=0, max_value=6),
    previous_failure_count=st.integers(min_value=0, max_value=20),
    has_subscription=st.booleans(),
    subscription_billing_amount_paise=st.one_of(
        st.none(), st.integers(min_value=1, max_value=10_000_000)
    ),
    feature_version=st.just("v1.0"),
)


# ---------------------------------------------------------------------------
# P4a — Untrained model returns consistent default (0.5)
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(recoverable_features_strategy)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_p4_untrained_model_returns_consistent_default(
    features: RecoverabilityFeatures,
) -> None:
    """Untrained model must return the same value (0.5) on every call for the same input.

    Feature: averiq, Property 4: Recoverability Model Determinism

    **Validates: Requirements FR-MODEL-010, NFR-TEST-006**
    """
    from ai.models.logistic_regression import LogisticRegressionBaseline

    model = LogisticRegressionBaseline(model_version="test-lr")

    result1 = model.predict(features)
    result2 = model.predict(features)
    assert result1 == result2, f"Untrained model non-deterministic: {result1} != {result2}"
    assert result1 == 0.5


# ---------------------------------------------------------------------------
# P4b — features_to_vector is deterministic for same input
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(recoverable_features_strategy)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_p4_features_to_vector_is_deterministic(
    features: RecoverabilityFeatures,
) -> None:
    """features_to_vector must return the same 10-element vector for the same input.

    Feature: averiq, Property 4: Recoverability Model Determinism

    **Validates: Requirements FR-MODEL-010, NFR-TEST-006**
    """
    from ai.training.feature_extraction import features_to_vector

    vec1 = features_to_vector(features)
    vec2 = features_to_vector(features)

    assert vec1 == vec2, f"features_to_vector non-deterministic: {vec1} != {vec2}"
    assert len(vec1) == 10, f"Expected 10 features, got {len(vec1)}"


# ---------------------------------------------------------------------------
# P4c — predict returns float in [0.0, 1.0]
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(recoverable_features_strategy)
@settings(max_examples=200)
def test_p4_predict_returns_float_in_range(features: RecoverabilityFeatures) -> None:
    """predict() must return a float in [0.0, 1.0] for any valid features.

    Feature: averiq, Property 4: Recoverability Model Determinism

    **Validates: Requirements FR-MODEL-010, NFR-TEST-006**
    """
    from ai.models.logistic_regression import LogisticRegressionBaseline

    model = LogisticRegressionBaseline(model_version="test")

    result = model.predict(features)
    assert isinstance(result, float), f"Expected float, got {type(result)}"
    assert 0.0 <= result <= 1.0, f"Probability {result} is outside [0.0, 1.0]"


# ---------------------------------------------------------------------------
# P4d — predict_batch returns same length as input
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(st.lists(recoverable_features_strategy, min_size=1, max_size=20))
@settings(max_examples=100)
def test_p4_predict_batch_returns_same_length(
    features_list: list[RecoverabilityFeatures],
) -> None:
    """predict_batch must return a list of the same length as the input.

    Feature: averiq, Property 4: Recoverability Model Determinism

    **Validates: Requirements FR-MODEL-010, NFR-TEST-006**
    """
    from ai.models.logistic_regression import LogisticRegressionBaseline

    model = LogisticRegressionBaseline(model_version="test")

    results = model.predict_batch(features_list)
    assert len(results) == len(features_list)
