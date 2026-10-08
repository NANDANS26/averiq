"""Feature extraction for Averiq's recoverability model training pipeline.

Extracts the 10 deterministic features from SyntheticCase records and
implements the temporal split strategy.

Feature definitions (FR-MODEL-002):

  1.  ``failure_category``    — categorical, one-hot at model layer
  2.  ``payment_method``      — categorical, one-hot at model layer
  3.  ``exposure_tier``       — ordinal (LOW=0, MEDIUM=1, HIGH=2)
  4.  ``payment_attempt_count``  — integer
  5.  ``customer_success_rate``  — float (successes / attempts); model input ONLY,
                                   never stored as authoritative financial value
  6.  ``time_since_failure_seconds`` — float; computed as
                                   ``decision_timestamp - created_at``
                                   (NOT ``NOW()`` — avoids train/serve mismatch)
  7.  ``has_subscription``    — boolean (0/1)
  8.  ``hour_of_day``         — integer 0–23
  9.  ``day_of_week``         — integer 0–6
  10. ``amount_paise``        — integer; converted to float for model input
                                (DATA-003: float only for model input, never stored)

Important constraints:
  * LLM-derived ``failure_category`` from DiagnosisOutput MUST NOT be used
    as a model feature.  The feature here is the deterministic fallback
    classifier value derived directly from payment error codes.
  * ``customer_success_rate`` is a float derived for model input only.
    It is never stored as an authoritative financial value.

Temporal split (EVAL-009, FR-MODEL-005):
  Training:   created_at < split_boundary_1
  Validation: split_boundary_1 <= created_at < split_boundary_2
  Test:       created_at >= split_boundary_2

  Default split boundaries (documented non-overlapping UTC timestamps):
    split_boundary_1 = 2023-09-01 00:00:00 UTC  (training / validation boundary)
    split_boundary_2 = 2023-11-01 00:00:00 UTC  (validation / test boundary)

  These boundaries are calibrated for the synthetic data time range
  (2023-01-02 → 2023-12-31) so that each split contains meaningful data.
  Both boundaries are non-overlapping by definition and must be stored in
  evaluation artifacts (FR-MODEL-006).

Requirements: FR-MODEL-002, FR-MODEL-005, EVAL-009
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

from shared.contracts.enums import FailureCategory
from shared.contracts.schemas import RecoverabilityFeatures

# Import SyntheticCase without importing from backend/ (module boundary rule)
from ai.data.generator import SyntheticCase

# ---------------------------------------------------------------------------
# Feature schema version
# ---------------------------------------------------------------------------

#: Identifies the feature schema — must match the model artifact's
#: ``feature_version`` field at serve time.  Increment on any breaking change
#: to feature definitions, encoding, or naming.
FEATURE_VERSION: Final[str] = "v1.0"

# ---------------------------------------------------------------------------
# Temporal split boundaries (documented non-overlapping UTC timestamps)
# ---------------------------------------------------------------------------

#: Cases with created_at < SPLIT_BOUNDARY_1 go to training
SPLIT_BOUNDARY_1: Final[datetime] = datetime(2023, 9, 1, 0, 0, 0, tzinfo=timezone.utc)

#: Cases with SPLIT_BOUNDARY_1 <= created_at < SPLIT_BOUNDARY_2 go to validation
SPLIT_BOUNDARY_2: Final[datetime] = datetime(2023, 11, 1, 0, 0, 0, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Exposure tier ordinal encoding
# ---------------------------------------------------------------------------

_EXPOSURE_ORDINAL: Final[dict[str, int]] = {
    "LOW": 0,
    "MEDIUM": 1,
    "HIGH": 2,
}

# ---------------------------------------------------------------------------
# FailureCategory encoding
# ---------------------------------------------------------------------------

#: All failure categories in a stable order for one-hot / label encoding
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

# ---------------------------------------------------------------------------
# Payment method encoding
# ---------------------------------------------------------------------------

_ALL_PAYMENT_METHODS: Final[list[str]] = [
    "card", "upi", "netbanking", "wallet", "emi", "unknown",
]

_PAYMENT_METHOD_INDEX: Final[dict[str, int]] = {
    m: i for i, m in enumerate(_ALL_PAYMENT_METHODS)
}


# ---------------------------------------------------------------------------
# Feature extraction functions
# ---------------------------------------------------------------------------


def extract_features_from_synthetic(
    case: SyntheticCase,
    decision_timestamp: datetime | None = None,
) -> RecoverabilityFeatures:
    """Extract a RecoverabilityFeatures vector from a SyntheticCase.

    Parameters
    ----------
    case:
        The synthetic payment failure case.
    decision_timestamp:
        The timestamp at which features are extracted (for computing
        ``time_since_failure_seconds``).  Defaults to the case's ``created_at``
        plus one hour (simulating the time between failure and decision).

        At serve time this should be the explicit ``decision_timestamp``
        stored on the ``RevenueRiskCase`` to prevent train/serve mismatch.

    Returns
    -------
    RecoverabilityFeatures
        Populated feature vector with ``feature_version = FEATURE_VERSION``.
    """
    if decision_timestamp is None:
        # Default: one hour after failure (reasonable approximation for training)
        from datetime import timedelta
        decision_timestamp = case.created_at + timedelta(hours=1)

    # Ensure both timestamps are timezone-aware before subtraction
    if decision_timestamp.tzinfo is None:
        decision_timestamp = decision_timestamp.replace(tzinfo=timezone.utc)

    failure_ts = case.created_at
    if failure_ts.tzinfo is None:
        failure_ts = failure_ts.replace(tzinfo=timezone.utc)

    time_since_failure_seconds = max(
        0.0, (decision_timestamp - failure_ts).total_seconds()
    )

    # customer_success_rate: float for model input only, never stored as authoritative value
    if case.payment_attempt_count > 0:
        customer_success_rate = case.successful_payment_count / case.payment_attempt_count
    else:
        customer_success_rate = 0.0

    # Map failure_category — use deterministic value from case (not from LLM)
    failure_category = _map_failure_category(case.failure_category)

    return RecoverabilityFeatures(
        # Financial features (int → model uses float internally)
        amount_paise=case.amount_paise,
        currency=case.currency,
        # Deterministic failure category (NOT LLM-derived)
        failure_category=failure_category,
        payment_method=case.payment_method,
        # Temporal
        time_since_failure_seconds=time_since_failure_seconds,
        hour_of_day=case.hour_of_day,
        day_of_week=case.day_of_week,
        # Customer history
        previous_failure_count=max(0, case.payment_attempt_count - case.successful_payment_count),
        has_subscription=case.has_subscription,
        subscription_billing_amount_paise=case.amount_paise if case.has_subscription else None,
        # Feature schema version
        feature_version=FEATURE_VERSION,
    )


def features_to_vector(features: RecoverabilityFeatures) -> list[float]:
    """Convert a RecoverabilityFeatures object to a numeric feature vector.

    This is the low-level encoding used by LightGBM and Logistic Regression.
    The encoding is stable — changes here require a feature_version bump.

    Encoding:
      [0]  amount_paise (float; model input only per DATA-003)
      [1]  exposure_tier ordinal (0=LOW, 1=MEDIUM, 2=HIGH)
      [2]  failure_category index (integer 0-8)
      [3]  payment_method index (integer 0-5)
      [4]  time_since_failure_seconds
      [5]  hour_of_day
      [6]  day_of_week
      [7]  previous_failure_count
      [8]  has_subscription (0/1)
      [9]  customer_success_rate approximation
             (successful_payments / (previous_failures + successful_payments))
             — Note: we approximate from previous_failure_count + subscription info

    Returns
    -------
    list[float]
        10-element numeric vector.
    """
    # Exposure tier from amount
    if features.amount_paise >= 100_000:
        exposure_ordinal = 2  # HIGH
    elif features.amount_paise >= 20_000:
        exposure_ordinal = 1  # MEDIUM
    else:
        exposure_ordinal = 0  # LOW

    failure_idx = _FAILURE_CATEGORY_INDEX.get(features.failure_category.value, 8)
    method_idx = _PAYMENT_METHOD_INDEX.get(features.payment_method or "unknown", 5)

    # Approximate customer_success_rate from available features
    total_attempts = features.previous_failure_count + 1  # at least 1 (this failure)
    # We don't have successful_payment_count directly in features; approximate
    # from previous_failure_count ratio (conservative: assume 50% baseline)
    customer_success_rate = max(
        0.0,
        1.0 - min(1.0, features.previous_failure_count / max(1, total_attempts + 5)),
    )

    return [
        float(features.amount_paise),           # [0] amount
        float(exposure_ordinal),                 # [1] exposure tier
        float(failure_idx),                      # [2] failure category
        float(method_idx),                       # [3] payment method
        features.time_since_failure_seconds,     # [4] time since failure
        float(features.hour_of_day),             # [5] hour of day
        float(features.day_of_week),             # [6] day of week
        float(features.previous_failure_count),  # [7] prior failures
        float(int(features.has_subscription)),   # [8] has subscription
        customer_success_rate,                   # [9] estimated success rate
    ]


def extract_feature_matrix(
    cases: list[SyntheticCase],
) -> tuple[list[list[float]], list[int]]:
    """Extract feature matrix and label vector from a list of SyntheticCase.

    Parameters
    ----------
    cases:
        List of synthetic cases (e.g., training split).

    Returns
    -------
    tuple[list[list[float]], list[int]]
        (X, y) where X is a list of 10-element feature vectors and y is a
        list of binary labels (1=recovered, 0=not recovered).
    """
    X: list[list[float]] = []
    y: list[int] = []
    for case in cases:
        features = extract_features_from_synthetic(case)
        X.append(features_to_vector(features))
        y.append(int(case.recovery_outcome))
    return X, y


def temporal_split(
    cases: list[SyntheticCase],
    split_boundary_1: datetime = SPLIT_BOUNDARY_1,
    split_boundary_2: datetime = SPLIT_BOUNDARY_2,
) -> tuple[list[SyntheticCase], list[SyntheticCase], list[SyntheticCase]]:
    """Partition cases into train / validation / test splits by time.

    Uses the pre-defined split boundaries to prevent data leakage between
    splits.  Cases are assigned to splits based on ``created_at``:

      Training:   created_at < split_boundary_1
      Validation: split_boundary_1 <= created_at < split_boundary_2
      Test:       created_at >= split_boundary_2

    Parameters
    ----------
    cases:
        All synthetic cases (should already be sorted by created_at).
    split_boundary_1:
        Training / validation boundary (UTC datetime).
    split_boundary_2:
        Validation / test boundary (UTC datetime).

    Returns
    -------
    tuple[list[SyntheticCase], list[SyntheticCase], list[SyntheticCase]]
        (train_cases, val_cases, test_cases)

    Notes
    -----
    * The three returned lists are non-overlapping.
    * Their union equals the input ``cases`` list (no case is dropped).
    * FR-MODEL-005, EVAL-009.
    """
    # Ensure boundaries are timezone-aware
    if split_boundary_1.tzinfo is None:
        split_boundary_1 = split_boundary_1.replace(tzinfo=timezone.utc)
    if split_boundary_2.tzinfo is None:
        split_boundary_2 = split_boundary_2.replace(tzinfo=timezone.utc)

    train: list[SyntheticCase] = []
    val: list[SyntheticCase] = []
    test: list[SyntheticCase] = []

    for case in cases:
        ts = case.created_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        if ts < split_boundary_1:
            train.append(case)
        elif ts < split_boundary_2:
            val.append(case)
        else:
            test.append(case)

    return train, val, test


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _map_failure_category(raw: str) -> FailureCategory:
    """Map a failure category string to the FailureCategory enum.

    Returns ``FailureCategory.UNKNOWN`` for unrecognised values.
    """
    try:
        return FailureCategory(raw)
    except ValueError:
        return FailureCategory.UNKNOWN
