"""Unit tests for RevenueRiskAssessor (task 8.1, FR-RISK-001 through FR-RISK-005).

Tests verify that the risk assessor:
  1. Returns payment_amount_at_risk equal to payment_amount_paise exactly (integer).
  2. Returns projected_subscription_revenue_at_risk as integer × cycles.
  3. Assigns exposure_tier correctly at threshold boundaries.
  4. Returns HIGH recovery_risk_tier for FRAUD_SUSPECTED regardless of probability.
  5. Assigns recovery_risk_tier correctly based on recovery_probability thresholds.
  6. Records risk_calculation_path correctly in calculation_metadata.
  7. Handles non-subscription payments (SINGLE_PAYMENT path).
  8. Handles subscription statuses that do NOT trigger projection.

All tests are pure unit tests — no database, no Redis, no network calls.
Requirements: FR-RISK-001 through FR-RISK-005, DATA-001, DATA-003.
"""

from __future__ import annotations

import pytest

from backend.decision_engine.risk_assessor import RevenueRiskAssessor, RiskAssessmentResult
from shared.contracts.enums import ExposureTier, FailureCategory, RecoveryRiskTier
from shared.contracts.schemas import PolicyConfig

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_policy(
    exposure_thresholds: dict[str, int] | None = None,
    version: str = "v1",
) -> PolicyConfig:
    """Build a minimal PolicyConfig for testing.

    Defaults use the same values as the assessor's own fallback defaults:
        HIGH   = 100_000 paise  (₹1,000)
        MEDIUM =  20_000 paise  (₹200)
    """
    if exposure_thresholds is None:
        exposure_thresholds = {"HIGH": 100_000, "MEDIUM": 20_000}
    return PolicyConfig(
        policy_id="test-policy-id",
        merchant_id="test-merchant-id",
        version=version,
        min_confidence=0.5,
        cooldown_seconds=300,
        max_retries={"RETRY": 3},
        max_amount={"INR": 500_000},
        human_approval_threshold=200_000,
        attribution_window_seconds=86_400,
        exposure_thresholds=exposure_thresholds,
    )


ASSESSOR = RevenueRiskAssessor()


# ---------------------------------------------------------------------------
# FR-RISK-001: payment_amount_at_risk equals payment_amount_paise exactly
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_payment_amount_at_risk_equals_input_paise() -> None:
    """FR-RISK-001: payment_amount_at_risk must equal payment_amount_paise exactly.

    The assessor must pass through the integer paise value without any
    transformation, rounding, or conversion to float.
    """
    policy = _make_policy()
    amount_paise = 75_000  # ₹750

    result = ASSESSOR.assess(
        payment_amount_paise=amount_paise,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.payment_amount_at_risk == amount_paise
    assert isinstance(result.payment_amount_at_risk, int)


@pytest.mark.unit
def test_payment_amount_at_risk_small_value() -> None:
    """FR-RISK-001: smallest valid amount (1 paise) is preserved exactly."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=1,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.payment_amount_at_risk == 1
    assert isinstance(result.payment_amount_at_risk, int)


@pytest.mark.unit
def test_payment_amount_at_risk_large_value() -> None:
    """FR-RISK-001: large integer amounts (no overflow risk in Python) are preserved."""
    policy = _make_policy()
    large_amount = 10_000_000  # ₹100,000

    result = ASSESSOR.assess(
        payment_amount_paise=large_amount,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.7,
        failure_category=None,
    )

    assert result.payment_amount_at_risk == large_amount
    assert isinstance(result.payment_amount_at_risk, int)


@pytest.mark.unit
def test_currency_is_recorded_on_result() -> None:
    """FR-RISK-001: currency field must accompany the amount on the result."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.currency == "INR"


# ---------------------------------------------------------------------------
# FR-RISK-002: projected_subscription_revenue_at_risk = billing_amount × cycles
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_projected_subscription_revenue_active_status() -> None:
    """FR-RISK-002: projected revenue is billing_amount × 3 for 'active' subscription."""
    policy = _make_policy()
    billing_amount = 50_000  # ₹500 per cycle
    expected_projected = billing_amount * RevenueRiskAssessor.SUBSCRIPTION_PROJECTION_CYCLES

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=billing_amount,
        subscription_status="active",
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.projected_subscription_revenue_at_risk == expected_projected
    assert isinstance(result.projected_subscription_revenue_at_risk, int)


@pytest.mark.unit
def test_projected_subscription_revenue_pending_status() -> None:
    """FR-RISK-002: projected revenue is computed for 'pending' subscription status too."""
    policy = _make_policy()
    billing_amount = 30_000

    result = ASSESSOR.assess(
        payment_amount_paise=30_000,
        currency="INR",
        subscription_billing_amount_paise=billing_amount,
        subscription_status="pending",
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    expected = billing_amount * RevenueRiskAssessor.SUBSCRIPTION_PROJECTION_CYCLES
    assert result.projected_subscription_revenue_at_risk == expected
    assert isinstance(result.projected_subscription_revenue_at_risk, int)


@pytest.mark.unit
def test_projected_subscription_revenue_is_exact_integer_multiplication() -> None:
    """FR-RISK-002: multiplication must be integer × integer — no float intermediate."""
    policy = _make_policy()
    billing_amount = 99_999  # Odd amount to surface any float rounding issues

    result = ASSESSOR.assess(
        payment_amount_paise=99_999,
        currency="INR",
        subscription_billing_amount_paise=billing_amount,
        subscription_status="active",
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    expected = billing_amount * 3  # 299_997 — exact integer
    assert result.projected_subscription_revenue_at_risk == expected
    assert isinstance(result.projected_subscription_revenue_at_risk, int)


@pytest.mark.unit
def test_no_projected_revenue_for_non_subscription_payment() -> None:
    """FR-RISK-002: projected_subscription_revenue_at_risk is None for standalone payments."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.projected_subscription_revenue_at_risk is None


@pytest.mark.unit
def test_no_projected_revenue_when_subscription_status_halted() -> None:
    """FR-RISK-002: halted subscription status does NOT trigger projection."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=50_000,
        subscription_status="halted",
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.projected_subscription_revenue_at_risk is None


@pytest.mark.unit
def test_no_projected_revenue_when_subscription_status_cancelled() -> None:
    """FR-RISK-002: cancelled subscription status does NOT trigger projection."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=50_000,
        subscription_status="cancelled",
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.projected_subscription_revenue_at_risk is None


@pytest.mark.unit
def test_no_projected_revenue_when_billing_amount_none_but_status_active() -> None:
    """FR-RISK-002: None billing_amount prevents projection even if status is active."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status="active",
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.projected_subscription_revenue_at_risk is None


# ---------------------------------------------------------------------------
# FR-RISK-003: exposure_tier threshold boundaries
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_exposure_tier_high_at_exact_high_threshold() -> None:
    """FR-RISK-003: amount exactly at the HIGH threshold → ExposureTier.HIGH."""
    high_threshold = 100_000
    policy = _make_policy(exposure_thresholds={"HIGH": high_threshold, "MEDIUM": 20_000})

    result = ASSESSOR.assess(
        payment_amount_paise=high_threshold,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.exposure_tier == ExposureTier.HIGH


@pytest.mark.unit
def test_exposure_tier_high_above_threshold() -> None:
    """FR-RISK-003: amount above HIGH threshold → ExposureTier.HIGH."""
    policy = _make_policy(exposure_thresholds={"HIGH": 100_000, "MEDIUM": 20_000})

    result = ASSESSOR.assess(
        payment_amount_paise=500_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.exposure_tier == ExposureTier.HIGH


@pytest.mark.unit
def test_exposure_tier_medium_just_below_high_threshold() -> None:
    """FR-RISK-003: amount just below HIGH but at/above MEDIUM → ExposureTier.MEDIUM."""
    policy = _make_policy(exposure_thresholds={"HIGH": 100_000, "MEDIUM": 20_000})

    result = ASSESSOR.assess(
        payment_amount_paise=99_999,  # 1 paise below HIGH threshold
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.exposure_tier == ExposureTier.MEDIUM


@pytest.mark.unit
def test_exposure_tier_medium_at_exact_medium_threshold() -> None:
    """FR-RISK-003: amount exactly at the MEDIUM threshold → ExposureTier.MEDIUM."""
    medium_threshold = 20_000
    policy = _make_policy(exposure_thresholds={"HIGH": 100_000, "MEDIUM": medium_threshold})

    result = ASSESSOR.assess(
        payment_amount_paise=medium_threshold,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.exposure_tier == ExposureTier.MEDIUM


@pytest.mark.unit
def test_exposure_tier_low_below_medium_threshold() -> None:
    """FR-RISK-003: amount below MEDIUM threshold → ExposureTier.LOW."""
    policy = _make_policy(exposure_thresholds={"HIGH": 100_000, "MEDIUM": 20_000})

    result = ASSESSOR.assess(
        payment_amount_paise=19_999,  # 1 paise below MEDIUM threshold
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.exposure_tier == ExposureTier.LOW


@pytest.mark.unit
def test_exposure_tier_low_for_small_amount() -> None:
    """FR-RISK-003: very small amount (1 paise) → ExposureTier.LOW."""
    policy = _make_policy(exposure_thresholds={"HIGH": 100_000, "MEDIUM": 20_000})

    result = ASSESSOR.assess(
        payment_amount_paise=1,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.exposure_tier == ExposureTier.LOW


@pytest.mark.unit
def test_exposure_tier_uses_policy_thresholds_not_hardcoded() -> None:
    """FR-RISK-003: exposure_tier must use the policy's thresholds, not hardcoded values."""
    # Set custom thresholds that differ from defaults
    custom_policy = _make_policy(
        exposure_thresholds={"HIGH": 500_000, "MEDIUM": 100_000}
    )

    # 100_000 would be HIGH with default thresholds, but is MEDIUM with custom
    result = ASSESSOR.assess(
        payment_amount_paise=100_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=custom_policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.exposure_tier == ExposureTier.MEDIUM


# ---------------------------------------------------------------------------
# FR-RISK-004: FRAUD_SUSPECTED always gives HIGH recovery_risk_tier
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fraud_suspected_always_high_recovery_risk_tier_high_probability() -> None:
    """FR-RISK-004: FRAUD_SUSPECTED → HIGH recovery_risk_tier even with high probability."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.95,  # Very high — would normally be LOW risk
        failure_category=FailureCategory.FRAUD_SUSPECTED,
    )

    assert result.recovery_risk_tier == RecoveryRiskTier.HIGH


@pytest.mark.unit
def test_fraud_suspected_always_high_recovery_risk_tier_medium_probability() -> None:
    """FR-RISK-004: FRAUD_SUSPECTED → HIGH recovery_risk_tier even at medium probability."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.45,  # Would normally be MEDIUM risk
        failure_category=FailureCategory.FRAUD_SUSPECTED,
    )

    assert result.recovery_risk_tier == RecoveryRiskTier.HIGH


@pytest.mark.unit
def test_fraud_suspected_always_high_recovery_risk_tier_zero_probability() -> None:
    """FR-RISK-004: FRAUD_SUSPECTED → HIGH recovery_risk_tier at zero probability."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.0,
        failure_category=FailureCategory.FRAUD_SUSPECTED,
    )

    assert result.recovery_risk_tier == RecoveryRiskTier.HIGH


# ---------------------------------------------------------------------------
# FR-RISK-004: recovery_risk_tier from probability thresholds
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_recovery_risk_tier_high_below_threshold() -> None:
    """FR-RISK-004: recovery_probability < 0.3 → RecoveryRiskTier.HIGH."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.29,
        failure_category=None,
    )

    assert result.recovery_risk_tier == RecoveryRiskTier.HIGH


@pytest.mark.unit
def test_recovery_risk_tier_medium_at_lower_boundary() -> None:
    """FR-RISK-004: recovery_probability == 0.3 (lower MEDIUM boundary) → MEDIUM."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.3,
        failure_category=None,
    )

    assert result.recovery_risk_tier == RecoveryRiskTier.MEDIUM


@pytest.mark.unit
def test_recovery_risk_tier_medium_in_range() -> None:
    """FR-RISK-004: 0.3 <= recovery_probability < 0.6 → RecoveryRiskTier.MEDIUM."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.45,
        failure_category=None,
    )

    assert result.recovery_risk_tier == RecoveryRiskTier.MEDIUM


@pytest.mark.unit
def test_recovery_risk_tier_medium_just_below_high_boundary() -> None:
    """FR-RISK-004: recovery_probability just below 0.6 → RecoveryRiskTier.MEDIUM."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.59,
        failure_category=None,
    )

    assert result.recovery_risk_tier == RecoveryRiskTier.MEDIUM


@pytest.mark.unit
def test_recovery_risk_tier_low_at_upper_boundary() -> None:
    """FR-RISK-004: recovery_probability == 0.6 (LOW boundary) → RecoveryRiskTier.LOW."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.6,
        failure_category=None,
    )

    assert result.recovery_risk_tier == RecoveryRiskTier.LOW


@pytest.mark.unit
def test_recovery_risk_tier_low_high_probability() -> None:
    """FR-RISK-004: high recovery_probability (> 0.6) → RecoveryRiskTier.LOW."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.95,
        failure_category=None,
    )

    assert result.recovery_risk_tier == RecoveryRiskTier.LOW


@pytest.mark.unit
def test_non_fraud_failure_category_does_not_override_tier() -> None:
    """FR-RISK-004: non-FRAUD_SUSPECTED categories use normal probability classification."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.9,  # HIGH probability → LOW risk tier
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
    )

    assert result.recovery_risk_tier == RecoveryRiskTier.LOW


# ---------------------------------------------------------------------------
# FR-RISK-005: risk_calculation_path in calculation_metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_risk_calculation_path_single_payment_no_subscription() -> None:
    """FR-RISK-005: SINGLE_PAYMENT path when no subscription present."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.risk_calculation_path == "SINGLE_PAYMENT"
    assert result.calculation_metadata["risk_calculation_path"] == "SINGLE_PAYMENT"


@pytest.mark.unit
def test_risk_calculation_path_subscription_projected_active() -> None:
    """FR-RISK-005: SUBSCRIPTION_PROJECTED path when subscription is active."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=50_000,
        subscription_status="active",
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.risk_calculation_path == "SUBSCRIPTION_PROJECTED"
    assert result.calculation_metadata["risk_calculation_path"] == "SUBSCRIPTION_PROJECTED"


@pytest.mark.unit
def test_calculation_metadata_contains_policy_version() -> None:
    """FR-RISK-005: calculation_metadata must record the policy version used."""
    policy = _make_policy(version="v42")

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.calculation_metadata["policy_version"] == "v42"


@pytest.mark.unit
def test_calculation_metadata_contains_exposure_thresholds() -> None:
    """FR-RISK-005: calculation_metadata must include the thresholds used for exposure_tier."""
    high_threshold = 200_000
    medium_threshold = 50_000
    policy = _make_policy(
        exposure_thresholds={"HIGH": high_threshold, "MEDIUM": medium_threshold}
    )

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.calculation_metadata["exposure_threshold_high"] == high_threshold
    assert result.calculation_metadata["exposure_threshold_medium"] == medium_threshold


@pytest.mark.unit
def test_calculation_metadata_contains_projection_cycles_for_subscription() -> None:
    """FR-RISK-005: when SUBSCRIPTION_PROJECTED, metadata must include projection_cycles."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=50_000,
        subscription_status="active",
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert "projection_cycles" in result.calculation_metadata
    assert result.calculation_metadata["projection_cycles"] == (
        RevenueRiskAssessor.SUBSCRIPTION_PROJECTION_CYCLES
    )


@pytest.mark.unit
def test_calculation_metadata_no_projection_cycles_for_single_payment() -> None:
    """FR-RISK-005: SINGLE_PAYMENT path must NOT include projection_cycles in metadata."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert "projection_cycles" not in result.calculation_metadata


# ---------------------------------------------------------------------------
# Edge cases and type safety
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_result_is_immutable_frozen_dataclass() -> None:
    """RiskAssessmentResult is a frozen dataclass — mutation must raise AttributeError."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    with pytest.raises((AttributeError, TypeError)):
        result.payment_amount_at_risk = 99_999  # type: ignore[misc]


@pytest.mark.unit
def test_invalid_payment_amount_raises_value_error() -> None:
    """FR-RISK-001: non-positive payment_amount_paise must raise ValueError."""
    policy = _make_policy()

    with pytest.raises(ValueError, match="payment_amount_paise"):
        ASSESSOR.assess(
            payment_amount_paise=0,  # zero is not valid
            currency="INR",
            subscription_billing_amount_paise=None,
            subscription_status=None,
            policy_config=policy,
            recovery_probability=0.5,
            failure_category=None,
        )


@pytest.mark.unit
def test_negative_payment_amount_raises_value_error() -> None:
    """FR-RISK-001: negative payment_amount_paise must raise ValueError."""
    policy = _make_policy()

    with pytest.raises(ValueError, match="payment_amount_paise"):
        ASSESSOR.assess(
            payment_amount_paise=-1,
            currency="INR",
            subscription_billing_amount_paise=None,
            subscription_status=None,
            policy_config=policy,
            recovery_probability=0.5,
            failure_category=None,
        )


@pytest.mark.unit
def test_subscription_created_status_does_not_trigger_projection() -> None:
    """FR-RISK-002: 'created' status does NOT qualify for projected revenue."""
    policy = _make_policy()

    result = ASSESSOR.assess(
        payment_amount_paise=50_000,
        currency="INR",
        subscription_billing_amount_paise=50_000,
        subscription_status="created",
        policy_config=policy,
        recovery_probability=0.5,
        failure_category=None,
    )

    assert result.projected_subscription_revenue_at_risk is None
    assert result.risk_calculation_path == "SINGLE_PAYMENT"


@pytest.mark.unit
def test_all_failure_categories_except_fraud_use_probability() -> None:
    """FR-RISK-004: non-FRAUD_SUSPECTED categories all follow the probability thresholds."""
    policy = _make_policy()
    non_fraud_categories = [c for c in FailureCategory if c != FailureCategory.FRAUD_SUSPECTED]

    for category in non_fraud_categories:
        result = ASSESSOR.assess(
            payment_amount_paise=50_000,
            currency="INR",
            subscription_billing_amount_paise=None,
            subscription_status=None,
            policy_config=policy,
            recovery_probability=0.9,  # Should yield LOW tier for non-fraud
            failure_category=category,
        )
        assert result.recovery_risk_tier == RecoveryRiskTier.LOW, (
            f"Expected LOW tier for {category} with probability=0.9"
        )
