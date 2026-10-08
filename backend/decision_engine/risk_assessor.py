"""Revenue Risk Assessor.

Computes the financial exposure of a failed payment case and classifies it
into risk tiers for prioritization.

All financial computations use integer arithmetic (paise). No floating-point
is used for amount calculations. DATA-001, DATA-003.

Requirements: FR-RISK-001 through FR-RISK-005
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from shared.contracts.enums import ExposureTier, FailureCategory, RecoveryRiskTier
from shared.contracts.schemas import PolicyConfig


@dataclass(frozen=True)
class RiskAssessmentResult:
    """Output of the revenue risk assessor.

    All financial amounts are stored as integer paise — never float.
    DATA-001, DATA-003.
    """

    # FR-RISK-001: integer paise, never float
    payment_amount_at_risk: int
    """The failed payment amount in integer minor currency units (paise).
    Directly sourced from payments.amount — no transformation applied."""

    currency: str
    """ISO 4217 three-letter currency code accompanying the amount. DATA-002, DATA-005."""

    # FR-RISK-002: integer paise; None for non-subscription payments
    projected_subscription_revenue_at_risk: Optional[int]
    """
    Projected subscription revenue at risk expressed as integer paise.

    Set only when a Subscription is present and its status is 'active' or
    'pending'.  Computed as:
        subscription_billing_amount_paise × SUBSCRIPTION_PROJECTION_CYCLES

    None for standalone (non-subscription) payments.
    """

    # FR-RISK-005: which calculation path was used
    risk_calculation_path: str
    """
    Which calculation path was used to derive the revenue-at-risk figures.

    One of:
        "SINGLE_PAYMENT"         — no subscription context; only the failed
                                   payment amount is at risk.
        "SUBSCRIPTION_PROJECTED" — subscription present and active/pending;
                                   projected future billing cycles included.

    Recorded in case_metadata per FR-RISK-005.
    """

    # FR-RISK-003: derived from policy thresholds
    exposure_tier: ExposureTier
    """
    Financial size classification of the case.

    HIGH / MEDIUM / LOW based on payment_amount_at_risk vs. the configurable
    thresholds stored in the active Policy (exposure_thresholds).
    Reflects financial size only — not recovery difficulty.
    FR-RISK-003.
    """

    # FR-RISK-004: derived from recoverability model + failure_category
    recovery_risk_tier: RecoveryRiskTier
    """
    Estimated recovery difficulty classification.

    Derived from recovery_probability (Recoverability_Model output) with an
    additional override: FRAUD_SUSPECTED failure_category always forces HIGH.
    Distinct from exposure_tier.
    FR-RISK-004.
    """

    # Metadata for case_metadata JSONB field
    calculation_metadata: dict = field(default_factory=dict)
    """
    Structured metadata documenting the assumptions used in this assessment.

    Always contains:
        risk_calculation_path     — "SINGLE_PAYMENT" | "SUBSCRIPTION_PROJECTED"
        policy_version            — policy version string used for thresholds
        exposure_threshold_high   — paise threshold for HIGH tier
        exposure_threshold_medium — paise threshold for MEDIUM tier

    For SUBSCRIPTION_PROJECTED also contains:
        projection_cycles         — number of future billing cycles projected
                                    (always SUBSCRIPTION_PROJECTION_CYCLES)

    Stored in revenue_risk_cases.case_metadata (JSONB).
    FR-RISK-002, FR-RISK-005.
    """


class RevenueRiskAssessor:
    """Computes revenue exposure and risk tiers for a failed payment.

    Usage::

        assessor = RevenueRiskAssessor()
        result = assessor.assess(
            payment_amount_paise=50_000,
            currency="INR",
            subscription_billing_amount_paise=50_000,
            subscription_status="active",
            policy_config=active_policy,
            recovery_probability=0.75,
            failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        )

    All financial arithmetic operates exclusively on Python ``int`` values
    representing minor currency units (paise).  No ``float`` is used for
    authoritative amounts.  DATA-001, DATA-003.
    """

    # Default number of future billing cycles to project for subscription
    # revenue-at-risk.  Sourced from this constant at serve time; can be
    # overridden via a subclass or future policy field if needed.
    SUBSCRIPTION_PROJECTION_CYCLES: int = 3

    # Subscription statuses that indicate an ongoing billing relationship.
    # Only these statuses qualify for projected revenue calculation (FR-RISK-002).
    ACTIVE_SUBSCRIPTION_STATUSES: frozenset[str] = frozenset({"active", "pending"})

    def assess(
        self,
        *,
        payment_amount_paise: int,
        currency: str,
        subscription_billing_amount_paise: Optional[int],
        subscription_status: Optional[str],
        policy_config: PolicyConfig,
        recovery_probability: float,
        # 0.0–1.0 from Recoverability_Model; NOT stored as a financial amount
        failure_category: Optional[FailureCategory],
    ) -> RiskAssessmentResult:
        """Assess revenue risk for a failed payment case.

        Args:
            payment_amount_paise:
                The failed payment amount in integer paise (FR-RISK-001).
                Must be a positive integer — never a float.
            currency:
                ISO 4217 currency code (e.g. "INR").
            subscription_billing_amount_paise:
                Regular billing amount for the associated subscription, in
                integer paise.  None if the payment is not a subscription
                charge.
            subscription_status:
                Razorpay subscription status string (e.g. "active",
                "pending", "halted", "cancelled").  None if not a
                subscription payment.
            policy_config:
                Active policy snapshot.  Provides exposure_thresholds for
                tier classification and version for metadata.  FR-RISK-003.
            recovery_probability:
                Float 0.0–1.0 produced by the Recoverability_Model.  Used
                ONLY for recovery_risk_tier classification — never treated as
                a financial amount.  FR-RISK-004.
            failure_category:
                Optional diagnosed failure category.  FRAUD_SUSPECTED always
                forces recovery_risk_tier = HIGH regardless of probability.
                FR-RISK-004.

        Returns:
            RiskAssessmentResult containing all computed risk fields and the
            calculation_metadata dict suitable for storage in
            revenue_risk_cases.case_metadata.

        Raises:
            ValueError: If payment_amount_paise is not a positive integer.
        """
        if not isinstance(payment_amount_paise, int) or payment_amount_paise <= 0:
            raise ValueError(
                f"payment_amount_paise must be a positive integer, got: "
                f"{payment_amount_paise!r}"
            )

        # --- FR-RISK-001: payment_amount_at_risk is the direct integer paise value ---
        payment_amount_at_risk: int = payment_amount_paise

        # --- FR-RISK-002 + FR-RISK-005: projected subscription revenue and path ---
        projected_subscription_revenue_at_risk: Optional[int] = None
        risk_calculation_path: str

        is_active_subscription = (
            subscription_billing_amount_paise is not None
            and subscription_status is not None
            and subscription_status.lower() in self.ACTIVE_SUBSCRIPTION_STATUSES
        )

        if is_active_subscription:
            # Integer multiplication only — no float arithmetic for amounts.
            # DATA-001, DATA-003.
            assert subscription_billing_amount_paise is not None  # type narrowing
            projected_subscription_revenue_at_risk = (
                subscription_billing_amount_paise * self.SUBSCRIPTION_PROJECTION_CYCLES
            )
            risk_calculation_path = "SUBSCRIPTION_PROJECTED"
        else:
            risk_calculation_path = "SINGLE_PAYMENT"

        # --- FR-RISK-003: exposure_tier from policy thresholds ---
        exposure_tier = self._compute_exposure_tier(
            amount_paise=payment_amount_at_risk,
            thresholds=policy_config.exposure_thresholds,
        )

        # --- FR-RISK-004: recovery_risk_tier from recoverability model + failure_category ---
        recovery_risk_tier = self._compute_recovery_risk_tier(
            recovery_probability=recovery_probability,
            failure_category=failure_category,
        )

        # --- FR-RISK-005: record risk_calculation_path in calculation_metadata ---
        calculation_metadata: dict = {
            "risk_calculation_path": risk_calculation_path,
            "policy_version": policy_config.version,
            "exposure_threshold_high": policy_config.exposure_thresholds.get(
                "HIGH", 100_000
            ),
            "exposure_threshold_medium": policy_config.exposure_thresholds.get(
                "MEDIUM", 20_000
            ),
        }
        if risk_calculation_path == "SUBSCRIPTION_PROJECTED":
            calculation_metadata["projection_cycles"] = self.SUBSCRIPTION_PROJECTION_CYCLES

        return RiskAssessmentResult(
            payment_amount_at_risk=payment_amount_at_risk,
            currency=currency,
            projected_subscription_revenue_at_risk=projected_subscription_revenue_at_risk,
            risk_calculation_path=risk_calculation_path,
            exposure_tier=exposure_tier,
            recovery_risk_tier=recovery_risk_tier,
            calculation_metadata=calculation_metadata,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_exposure_tier(
        self,
        amount_paise: int,
        thresholds: dict[str, int],
    ) -> ExposureTier:
        """Classify ``amount_paise`` into an ExposureTier using policy thresholds.

        Threshold semantics (FR-RISK-003):
            amount_paise >= HIGH threshold  → ExposureTier.HIGH
            amount_paise >= MEDIUM threshold → ExposureTier.MEDIUM
            otherwise                        → ExposureTier.LOW

        Default thresholds if not configured in the policy:
            HIGH   = 100_000 paise  (₹1,000 for INR)
            MEDIUM =  20_000 paise  (₹200  for INR)

        Args:
            amount_paise: Payment amount in integer paise.
            thresholds:   Policy ``exposure_thresholds`` dict.

        Returns:
            ExposureTier enum value.
        """
        high_threshold: int = thresholds.get("HIGH", 100_000)
        medium_threshold: int = thresholds.get("MEDIUM", 20_000)

        if amount_paise >= high_threshold:
            return ExposureTier.HIGH
        elif amount_paise >= medium_threshold:
            return ExposureTier.MEDIUM
        else:
            return ExposureTier.LOW

    def _compute_recovery_risk_tier(
        self,
        recovery_probability: float,
        failure_category: Optional[FailureCategory],
    ) -> RecoveryRiskTier:
        """Classify recovery difficulty into a RecoveryRiskTier.

        Classification rules (FR-RISK-004):
            FRAUD_SUSPECTED failure_category       → always HIGH (hard override)
            recovery_probability < 0.3             → HIGH   (hard to recover)
            0.3 <= recovery_probability < 0.6      → MEDIUM
            recovery_probability >= 0.6            → LOW    (likely to recover)

        The FRAUD_SUSPECTED override applies regardless of the model's
        probability score because fraudulent transactions are operationally
        treated as unrecoverable.

        Args:
            recovery_probability:
                Float 0.0–1.0 from the Recoverability_Model.  Never treated
                as a financial amount.
            failure_category:
                Optional diagnosed failure category.  None is treated as no
                special override.

        Returns:
            RecoveryRiskTier enum value.
        """
        # Hard override: FRAUD_SUSPECTED → always HIGH recovery risk
        if failure_category is FailureCategory.FRAUD_SUSPECTED:
            return RecoveryRiskTier.HIGH

        # Probability-based classification
        if recovery_probability < 0.3:
            return RecoveryRiskTier.HIGH
        elif recovery_probability < 0.6:
            return RecoveryRiskTier.MEDIUM
        else:
            return RecoveryRiskTier.LOW
