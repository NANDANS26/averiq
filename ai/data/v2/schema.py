"""Canonical schema and contract definitions for Dataset 2.0.

Defines:
  1. FeatureClassification: Explicit boundary between decision-time and post-outcome/post-intervention fields.
  2. SyntheticPaymentRecord: Rich canonical record representing a simulated failure case with potential outcomes.
  3. Canonical feature list and prohibited leakage list.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Final


class FeatureAvailability(str, Enum):
    AVAILABLE_AT_DECISION_TIME = "AVAILABLE_AT_DECISION_TIME"
    POST_INTERVENTION = "POST_INTERVENTION"
    POST_OUTCOME = "POST_OUTCOME"
    POTENTIAL_OUTCOME_GROUND_TRUTH = "POTENTIAL_OUTCOME_GROUND_TRUTH"


# ---------------------------------------------------------------------------
# Strict Feature Whitelist: Available at decision time ONLY
# ---------------------------------------------------------------------------
DECISION_TIME_FEATURES: Final[list[str]] = [
    # Payment dimensions
    "amount_paise",
    "currency",
    "payment_method",
    "payment_attempt_count",
    "is_subscription",
    "subscription_billing_amount_paise",
    # Failure taxonomy
    "failure_category",
    "error_code",
    "is_transient_error",
    # Customer history & behavior (pre-failure observations only)
    "customer_tenure_days",
    "customer_historical_success_rate",
    "customer_historical_failure_count",
    "customer_prior_recovery_rate",
    # Merchant context
    "merchant_risk_tier",
    "merchant_failure_baseline",
    # Operational & Temporal
    "time_since_failure_seconds",
    "hour_of_day",
    "day_of_week",
    "gateway_health_score",
]

# ---------------------------------------------------------------------------
# Strict Leakage Blacklist: MUST NEVER ENTER THE FEATURE MATRIX
# ---------------------------------------------------------------------------
PROHIBITED_LEAKAGE_FEATURES: Final[list[str]] = [
    # Observed outcomes
    "recovered",
    "recovered_outcome",
    "recovered_amount",
    "recovery_time_seconds",
    "recovery_source",
    "recovery_timestamp",
    # Post-intervention execution / verification
    "intervention_assigned",
    "treatment_assigned",
    "external_operation_status",
    "gateway_reported_status",
    "verification_outcome",
    "verification_attempt_count",
    # Potential outcomes / simulation ground truth
    "y_0_baseline",
    "y_payment_link",
    "y_retry",
    "y_customer_action",
    "true_incremental_uplift",
    # Unobservable latents
    "latent_customer_intent",
    "latent_temporary_liquidity",
    "latent_issuer_availability",
]


@dataclass(frozen=True)
class SyntheticPaymentRecord:
    """Rich canonical payment failure record representing the full synthetic environment."""

    # Identifiers
    case_id: str
    merchant_id: str
    customer_id: str
    payment_id: str

    # Temporal anchor
    created_at: datetime
    decision_timestamp: datetime

    # Decision-time observable features
    amount_paise: int
    currency: str
    payment_method: str
    payment_attempt_count: int
    is_subscription: bool
    subscription_billing_amount_paise: int | None

    failure_category: str
    error_code: str
    is_transient_error: bool

    customer_tenure_days: int
    customer_historical_success_rate: float
    customer_historical_failure_count: int
    customer_prior_recovery_rate: float

    merchant_risk_tier: str
    merchant_failure_baseline: float

    time_since_failure_seconds: float
    hour_of_day: int
    day_of_week: int
    gateway_health_score: float

    # Treatment (Intervention Assigned)
    intervention_assigned: str  # "NO_INTERVENTION", "PAYMENT_LINK", "RETRY", "CUSTOMER_ACTION"
    treatment_propensity: float

    # Observed outcome under assigned intervention
    recovered: bool
    recovered_amount: int
    recovery_time_seconds: float | None

    # Counterfactual / Potential outcomes (GROUND TRUTH ONLY - NEVER MODEL INPUT)
    y_0_baseline: bool
    y_payment_link: bool
    y_retry: bool
    y_customer_action: bool
    true_incremental_uplift: float

    # Latent unobservables (GROUND TRUTH ONLY)
    latent_customer_intent: float
    latent_temporary_liquidity: float
    latent_issuer_availability: float

    def to_decision_feature_dict(self) -> dict[str, Any]:
        """Return strictly the decision-time feature subset."""
        return {k: getattr(self, k) for k in DECISION_TIME_FEATURES}

    def to_full_dict(self) -> dict[str, Any]:
        """Convert entire record into dictionary for storage."""
        return {
            "case_id": self.case_id,
            "merchant_id": self.merchant_id,
            "customer_id": self.customer_id,
            "payment_id": self.payment_id,
            "created_at": self.created_at.isoformat(),
            "decision_timestamp": self.decision_timestamp.isoformat(),
            "amount_paise": self.amount_paise,
            "currency": self.currency,
            "payment_method": self.payment_method,
            "payment_attempt_count": self.payment_attempt_count,
            "is_subscription": self.is_subscription,
            "subscription_billing_amount_paise": self.subscription_billing_amount_paise,
            "failure_category": self.failure_category,
            "error_code": self.error_code,
            "is_transient_error": self.is_transient_error,
            "customer_tenure_days": self.customer_tenure_days,
            "customer_historical_success_rate": self.customer_historical_success_rate,
            "customer_historical_failure_count": self.customer_historical_failure_count,
            "customer_prior_recovery_rate": self.customer_prior_recovery_rate,
            "merchant_risk_tier": self.merchant_risk_tier,
            "merchant_failure_baseline": self.merchant_failure_baseline,
            "time_since_failure_seconds": self.time_since_failure_seconds,
            "hour_of_day": self.hour_of_day,
            "day_of_week": self.day_of_week,
            "gateway_health_score": self.gateway_health_score,
            "intervention_assigned": self.intervention_assigned,
            "treatment_propensity": self.treatment_propensity,
            "recovered": self.recovered,
            "recovered_amount": self.recovered_amount,
            "recovery_time_seconds": self.recovery_time_seconds,
            "y_0_baseline": self.y_0_baseline,
            "y_payment_link": self.y_payment_link,
            "y_retry": self.y_retry,
            "y_customer_action": self.y_customer_action,
            "true_incremental_uplift": self.true_incremental_uplift,
            "latent_customer_intent": self.latent_customer_intent,
            "latent_temporary_liquidity": self.latent_temporary_liquidity,
            "latent_issuer_availability": self.latent_issuer_availability,
        }
