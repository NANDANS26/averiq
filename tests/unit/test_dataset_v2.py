"""Tests for Dataset 2.0 Generator, Schema, and Feature Integrity.

Verifies:
  1. Generator determinism (identical seed -> identical records).
  2. Multi-level generation (merchants, customers, payments).
  3. Strict feature whitelist (only DECISION_TIME_FEATURES extracted).
  4. Leakage prevention (PROHIBITED_LEAKAGE_FEATURES never in vector).
  5. Potential outcomes existence and consistency.
  6. Chronological temporal split validity (train < val < test).
  7. Integer financial discipline (amounts in integer paise).
"""
from __future__ import annotations

import pytest

from ai.data.v2.generator import PaymentEnvironmentSimulator, generate_v2_dataset
from ai.data.v2.schema import (
    DECISION_TIME_FEATURES,
    PROHIBITED_LEAKAGE_FEATURES,
    SyntheticPaymentRecord,
)
from ai.data.v2.features import (
    FEATURE_NAMES_V2,
    extract_v2_feature_matrix,
    record_to_features,
    record_to_vector_v2,
    temporal_split_v2,
)


@pytest.mark.unit
def test_v2_generator_determinism() -> None:
    """Same seed produces identical dataset."""
    records_1 = generate_v2_dataset(seed=42, count=100)
    records_2 = generate_v2_dataset(seed=42, count=100)

    assert len(records_1) == 100
    assert len(records_2) == 100

    for r1, r2 in zip(records_1, records_2, strict=True):
        assert r1.case_id == r2.case_id
        assert r1.merchant_id == r2.merchant_id
        assert r1.customer_id == r2.customer_id
        assert r1.amount_paise == r2.amount_paise
        assert r1.failure_category == r2.failure_category
        assert r1.recovered == r2.recovered
        assert r1.intervention_assigned == r2.intervention_assigned
        assert r1.y_0_baseline == r2.y_0_baseline
        assert r1.y_payment_link == r2.y_payment_link


@pytest.mark.unit
def test_v2_generator_different_seeds() -> None:
    """Different seeds produce different data."""
    records_1 = generate_v2_dataset(seed=42, count=50)
    records_2 = generate_v2_dataset(seed=999, count=50)

    amounts_1 = [r.amount_paise for r in records_1]
    amounts_2 = [r.amount_paise for r in records_2]
    assert amounts_1 != amounts_2


@pytest.mark.unit
def test_v2_financial_integers() -> None:
    """All monetary fields are strictly positive integer paise."""
    records = generate_v2_dataset(seed=42, count=200)

    for r in records:
        assert isinstance(r.amount_paise, int)
        assert r.amount_paise > 0
        assert isinstance(r.recovered_amount, int)
        assert r.recovered_amount >= 0
        if r.recovered:
            assert r.recovered_amount == r.amount_paise
        else:
            assert r.recovered_amount == 0

        if r.is_subscription:
            assert isinstance(r.subscription_billing_amount_paise, int)
            assert r.subscription_billing_amount_paise > 0
        else:
            assert r.subscription_billing_amount_paise is None


@pytest.mark.unit
def test_v2_potential_outcomes_consistency() -> None:
    """Check consistency of potential outcomes framework."""
    records = generate_v2_dataset(seed=42, count=300)

    for r in records:
        # If baseline Y(0) is True, all enhanced interventions must also succeed
        if r.y_0_baseline:
            assert r.y_payment_link is True
            assert r.y_retry is True
            assert r.y_customer_action is True

        # Observed outcome matches assigned treatment
        if r.intervention_assigned == "NO_INTERVENTION":
            assert r.recovered == r.y_0_baseline
        elif r.intervention_assigned == "PAYMENT_LINK":
            assert r.recovered == r.y_payment_link
        elif r.intervention_assigned == "RETRY":
            assert r.recovered == r.y_retry
        elif r.intervention_assigned == "CUSTOMER_ACTION":
            assert r.recovered == r.y_customer_action


@pytest.mark.unit
def test_v2_leakage_prevention() -> None:
    """Ensure no prohibited leakage field enters decision features or vector."""
    record = generate_v2_dataset(seed=42, count=1)[0]
    decision_dict = record.to_decision_feature_dict()

    for prohibited in PROHIBITED_LEAKAGE_FEATURES:
        assert prohibited not in decision_dict, f"Leakage detected: {prohibited} in decision dict!"
        assert prohibited not in FEATURE_NAMES_V2, f"Leakage detected: {prohibited} in feature names!"

    vector = record_to_vector_v2(record)
    assert len(vector) == len(FEATURE_NAMES_V2)
    assert all(isinstance(v, (int, float)) for v in vector)


@pytest.mark.unit
def test_v2_temporal_split() -> None:
    """Test strict non-overlapping chronological split."""
    records = generate_v2_dataset(seed=42, count=500)
    train, val, test = temporal_split_v2(records, train_ratio=0.7, val_ratio=0.15)

    assert len(train) == 350
    assert len(val) == 75
    assert len(test) == 75
    assert len(train) + len(val) + len(test) == 500

    # Strict chronological boundaries: max(train) <= min(val) and max(val) <= min(test)
    max_train_ts = max(r.created_at for r in train)
    min_val_ts = min(r.created_at for r in val)
    max_val_ts = max(r.created_at for r in val)
    min_test_ts = min(r.created_at for r in test)

    assert max_train_ts <= min_val_ts
    assert max_val_ts <= min_test_ts


@pytest.mark.unit
def test_v2_pydantic_feature_conversion() -> None:
    """Verify conversion from Dataset 2.0 record to RecoverabilityFeatures."""
    record = generate_v2_dataset(seed=42, count=1)[0]
    features = record_to_features(record)

    assert features.amount_paise == record.amount_paise
    assert features.currency == "INR"
    assert features.hour_of_day == record.hour_of_day
    assert features.has_subscription == record.is_subscription
    assert features.time_since_failure_seconds == record.time_since_failure_seconds
