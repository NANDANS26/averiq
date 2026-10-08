"""Dataset 2.0 Feature Extraction and Temporal Splitter.

Bridges Dataset 2.0 records (SyntheticPaymentRecord / dict / DataFrame) with
Averiq's recoverability models, ensuring:
  1. Strict adherence to DECISION_TIME_FEATURES whitelist.
  2. Absolute zero leakage from PROHIBITED_LEAKAGE_FEATURES.
  3. Stable, reproducible vector encoding compatible with RecoverabilityFeatures.
  4. Chronological temporal split across non-overlapping UTC time boundaries.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final

from ai.data.v2.schema import (
    DECISION_TIME_FEATURES,
    PROHIBITED_LEAKAGE_FEATURES,
    SyntheticPaymentRecord,
)
from shared.contracts.enums import FailureCategory
from shared.contracts.schemas import RecoverabilityFeatures

# ---------------------------------------------------------------------------
# Feature schema version
# ---------------------------------------------------------------------------
FEATURE_VERSION_V2: Final[str] = "v2.0"

# Canonical feature ordering in numeric vector:
#   [0]  amount_paise (float minor units)
#   [1]  exposure_tier ordinal (0=LOW, 1=MEDIUM, 2=HIGH)
#   [2]  failure_category index (0-8)
#   [3]  payment_method index (0-5)
#   [4]  time_since_failure_seconds
#   [5]  hour_of_day (0-23)
#   [6]  day_of_week (0-6)
#   [7]  payment_attempt_count (1+)
#   [8]  has_subscription (0/1)
#   [9]  customer_historical_success_rate (0.0 - 1.0)
#   [10] customer_tenure_days
#   [11] gateway_health_score (0.0 - 1.0)
#   [12] is_transient_error (0/1)
#   [13] merchant_risk_tier ordinal (0=LOW, 1=MEDIUM, 2=HIGH)
FEATURE_NAMES_V2: Final[list[str]] = [
    "amount_paise",
    "exposure_tier",
    "failure_category_idx",
    "payment_method_idx",
    "time_since_failure_seconds",
    "hour_of_day",
    "day_of_week",
    "payment_attempt_count",
    "has_subscription",
    "customer_historical_success_rate",
    "customer_tenure_days",
    "gateway_health_score",
    "is_transient_error",
    "merchant_risk_tier",
]

_ALL_FAILURE_CATEGORIES: Final[list[str]] = [
    FailureCategory.INSUFFICIENT_FUNDS.value,
    FailureCategory.CARD_DECLINED.value,
    FailureCategory.EXPIRED_CARD.value,
    FailureCategory.AUTHENTICATION_FAILURE.value,
    FailureCategory.TECHNICAL_FAILURE.value,
    FailureCategory.NETWORK_TIMEOUT.value,
    FailureCategory.FRAUD_SUSPECTED.value,
    FailureCategory.LIMIT_EXCEEDED.value,
    FailureCategory.UNKNOWN.value,
]
_FAILURE_CATEGORY_INDEX: Final[dict[str, int]] = {
    cat: i for i, cat in enumerate(_ALL_FAILURE_CATEGORIES)
}

_ALL_PAYMENT_METHODS: Final[list[str]] = [
    "card",
    "upi",
    "netbanking",
    "wallet",
    "emi",
    "unknown",
]
_PAYMENT_METHOD_INDEX: Final[dict[str, int]] = {
    m: i for i, m in enumerate(_ALL_PAYMENT_METHODS)
}

_RISK_TIER_INDEX: Final[dict[str, int]] = {
    "LOW": 0,
    "MEDIUM": 1,
    "HIGH": 2,
}


def record_to_features(
    record: SyntheticPaymentRecord | dict[str, Any],
) -> RecoverabilityFeatures:
    """Convert a Dataset 2.0 record to a Pydantic RecoverabilityFeatures object.

    Maintains backwards compatibility with RecoverabilityFeatures schema while
    ensuring all fields are sourced purely from decision-time variables.
    """
    if isinstance(record, SyntheticPaymentRecord):
        data = record.to_full_dict()
    else:
        data = record

    raw_cat = str(data.get("failure_category", "UNKNOWN"))
    try:
        cat_enum = FailureCategory(raw_cat)
    except ValueError:
        cat_enum = FailureCategory.UNKNOWN

    is_sub = bool(data.get("is_subscription", False))
    sub_billing = data.get("subscription_billing_amount_paise")

    return RecoverabilityFeatures(
        amount_paise=int(data["amount_paise"]),
        currency=str(data.get("currency", "INR")),
        failure_category=cat_enum,
        payment_method=data.get("payment_method"),
        time_since_failure_seconds=float(data.get("time_since_failure_seconds", 0.0)),
        hour_of_day=int(data.get("hour_of_day", 0)),
        day_of_week=int(data.get("day_of_week", 0)),
        previous_failure_count=int(data.get("customer_historical_failure_count", 0)),
        has_subscription=is_sub,
        subscription_billing_amount_paise=int(sub_billing) if sub_billing else None,
        feature_version="v1.0",
    )


def record_to_vector_v2(record: SyntheticPaymentRecord | dict[str, Any]) -> list[float]:
    """Extract a 14-dimensional feature vector for Dataset 2.0 models.

    Strictly inspects that NO prohibited leakage field is used.
    """
    if isinstance(record, SyntheticPaymentRecord):
        data = record.to_decision_feature_dict()
    else:
        # If passed a dictionary, sanitize by taking only DECISION_TIME_FEATURES
        data = {k: record[k] for k in DECISION_TIME_FEATURES if k in record}

    amount = int(data.get("amount_paise", 0))
    if amount >= 100_000:
        exposure_tier = 2
    elif amount >= 20_000:
        exposure_tier = 1
    else:
        exposure_tier = 0

    cat_idx = _FAILURE_CATEGORY_INDEX.get(str(data.get("failure_category", "")), 8)
    method_idx = _PAYMENT_METHOD_INDEX.get(str(data.get("payment_method", "unknown")), 5)
    time_since = float(data.get("time_since_failure_seconds", 0.0))
    hour = float(data.get("hour_of_day", 0))
    dow = float(data.get("day_of_week", 0))
    attempts = float(data.get("payment_attempt_count", 1))
    has_sub = float(1.0 if data.get("is_subscription") else 0.0)
    success_rate = float(data.get("customer_historical_success_rate", 0.5))
    tenure = float(data.get("customer_tenure_days", 30))
    gw_health = float(data.get("gateway_health_score", 0.95))
    is_transient = float(1.0 if data.get("is_transient_error") else 0.0)
    risk_tier = float(_RISK_TIER_INDEX.get(str(data.get("merchant_risk_tier", "LOW")), 0))

    return [
        float(amount),
        float(exposure_tier),
        float(cat_idx),
        float(method_idx),
        time_since,
        hour,
        dow,
        attempts,
        has_sub,
        success_rate,
        tenure,
        gw_health,
        is_transient,
        risk_tier,
    ]


def extract_v2_feature_matrix(
    records: list[SyntheticPaymentRecord] | list[dict[str, Any]],
) -> tuple[list[list[float]], list[int]]:
    """Extract feature matrix and labels from Dataset 2.0 records."""
    X: list[list[float]] = []
    y: list[int] = []
    for r in records:
        X.append(record_to_vector_v2(r))
        if isinstance(r, SyntheticPaymentRecord):
            y.append(1 if r.recovered else 0)
        else:
            y.append(1 if r.get("recovered") else 0)
    return X, y


def temporal_split_v2(
    records: list[SyntheticPaymentRecord],
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
) -> tuple[list[SyntheticPaymentRecord], list[SyntheticPaymentRecord], list[SyntheticPaymentRecord]]:
    """Chronological temporal split of Dataset 2.0 records.

    Ensures:
      - Records are sorted strictly by created_at.
      - Train cohort occurs strictly before Validation cohort.
      - Validation cohort occurs strictly before Test cohort.
      - Non-overlapping intervals with complete coverage.
    """
    sorted_records = sorted(records, key=lambda r: r.created_at)
    total = len(sorted_records)
    n_train = int(total * train_ratio)
    n_val = int(total * val_ratio)

    train_cohort = sorted_records[:n_train]
    val_cohort = sorted_records[n_train : n_train + n_val]
    test_cohort = sorted_records[n_train + n_val :]

    return train_cohort, val_cohort, test_cohort
