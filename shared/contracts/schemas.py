"""
Pydantic data models for Averiq's cross-boundary communication layer.

All models in this module are:

* **Immutable** — ``model_config = ConfigDict(frozen=True)`` prevents
  accidental mutation after construction.
* **Pure data** — zero I/O, zero database access, zero implementation imports.
  Only ``pydantic``, ``typing``, ``datetime``, and the sibling ``enums``
  module are imported.
* **Self-documenting** — every field carries a description in its docstring or
  inline comment so that FastAPI's auto-generated OpenAPI spec is informative.

These models are the *only* types that cross the ``backend/ ↔ ai/`` boundary.
``backend/`` and ``ai/`` must not import each other's modules directly; they
communicate exclusively through the types defined here and the protocols
defined in ``protocols.py``.

Requirements: DATA-008, DATA-010, FR-DIAG-001.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.enums import (
    ExecutionCapability,
    ExposureTier,
    FailureCategory,
    InterventionType,
    OperatingMode,
    PolicyDecisionOutcome,
    RecoveryRiskTier,
    VerificationOutcome,
)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

#: A monetary amount expressed as an integer in the smallest indivisible unit
#: of the relevant currency (paise for INR, cents for USD, etc.).
#: Authoritative amounts MUST always be ``int`` — never ``float``.
#: DATA-001, DATA-003.
PaiseAmount = Annotated[int, Field(ge=0, description="Integer amount in minor currency units (e.g. paise for INR). Must never be a float.")]

#: ISO 4217 three-letter currency code (e.g. "INR", "USD").
#: DATA-002, DATA-005.
CurrencyCode = Annotated[str, Field(min_length=3, max_length=3, description="ISO 4217 three-letter currency code (e.g. 'INR').")]

#: A probability score in the closed interval [0.0, 1.0].
ProbabilityScore = Annotated[float, Field(ge=0.0, le=1.0, description="Probability value in [0.0, 1.0]. Not a financial amount.")]


# ---------------------------------------------------------------------------
# CaseContext
# ---------------------------------------------------------------------------


class CaseContext(BaseModel):
    """
    Input to the AI diagnosis step performed by the Root_Cause_Reasoner.

    This context is assembled from the ``RevenueRiskCase``, the associated
    ``Payment``, and optional ``Subscription`` / ``Customer`` records.  It
    is passed to ``AIProvider.diagnose()`` and to the deterministic fallback
    classifier.

    The AI provider MUST receive all untrusted data (error codes, customer
    data) through this structured model — never via string interpolation into
    a system prompt.  FR-DIAG-008, SEC-AI-002.
    """

    model_config = ConfigDict(frozen=True)

    # ---- Identifiers ----

    case_id: str = Field(
        description="Unique identifier for the RevenueRiskCase being diagnosed.",
    )
    merchant_id: str = Field(
        description="Merchant to which this case belongs. Used for isolation enforcement.",
    )
    payment_id: str = Field(
        description="Gateway payment identifier (e.g. Razorpay pay_XXXXXXXXXXXXXXXX).",
    )

    # ---- Payment financials ----

    amount_paise: int = Field(
        ge=1,
        description=(
            "Failed payment amount in minor currency units (paise for INR). "
            "Must be a positive integer — never a float. DATA-001."
        ),
    )
    currency: CurrencyCode = Field(
        description="ISO 4217 currency code for the payment amount. DATA-002.",
    )

    # ---- Failure signals ----

    error_code: str | None = Field(
        default=None,
        description=(
            "Gateway-provided error code (e.g. Razorpay 'BAD_REQUEST_ERROR'). "
            "Used by the deterministic fallback classifier. May be None if not returned."
        ),
    )
    error_description: str | None = Field(
        default=None,
        description=(
            "Human-readable gateway error description. "
            "Treated as untrusted user data — never interpolated into LLM system prompts."
        ),
    )
    failure_category: FailureCategory | None = Field(
        default=None,
        description=(
            "Pre-classified failure category if already available (e.g. from a prior "
            "deterministic pass). None if not yet classified."
        ),
    )

    # ---- Customer and subscription context ----

    customer_id: str | None = Field(
        default=None,
        description="Gateway customer identifier, if the payment is linked to a customer.",
    )
    subscription_id: str | None = Field(
        default=None,
        description=(
            "Gateway subscription identifier if the failed payment is a subscription charge. "
            "None for standalone payments."
        ),
    )
    previous_failure_count: int = Field(
        ge=0,
        description=(
            "Number of prior payment failures for this payment entity. "
            "Used as a signal for diagnosis confidence. Integer, never float."
        ),
    )

    # ---- Temporal ----

    created_at: datetime = Field(
        description=(
            "UTC timestamp when the RevenueRiskCase was created. "
            "Must be timezone-aware. DATA-004."
        ),
    )


# ---------------------------------------------------------------------------
# DiagnosisOutput
# ---------------------------------------------------------------------------


class DiagnosisOutput(BaseModel):
    """
    Structured output produced by the Root_Cause_Reasoner (via any
    ``AIProvider`` implementation or the deterministic fallback classifier).

    This schema is validated by Pydantic before the output is used in *any*
    downstream decision or persisted to the database.  If validation fails,
    the deterministic fallback is invoked and ``fallback_reason`` is set.
    FR-DIAG-003, FR-DIAG-004, SEC-AI-001.

    Important: ``failure_category`` from this schema is used **only** for
    operator-facing explanation and the decision trace.  It MUST NOT be used
    as an input feature to the Recoverability_Model.  The model uses
    deterministic, provider-supplied features (error_code, failure_code,
    payment_method, etc.) — see design section "Recoverability Model: Feature
    Set".
    """

    model_config = ConfigDict(frozen=True)

    failure_category: FailureCategory = Field(
        description=(
            "The root cause classification. Validated against the FailureCategory "
            "enum allowlist before use. Unknown values trigger the deterministic fallback. "
            "SEC-AI-001."
        ),
    )
    confidence_score: ProbabilityScore = Field(
        description=(
            "Confidence in the failure_category assignment, expressed as a float in "
            "[0.0, 1.0]. Not a financial value — never stored as an authoritative amount."
        ),
    )
    ai_source: str = Field(
        description=(
            "Identifies which component produced this diagnosis. "
            "One of: 'StubAIProvider', 'ExternalLLMProvider', 'DeterministicFallback'. "
            "Recorded in the AuditEvent and decision_trace. AI-PROV-002."
        ),
    )
    reasoning: str | None = Field(
        default=None,
        description=(
            "Human-readable explanation for the diagnosis. Present for ExternalLLMProvider "
            "and some StubAIProvider outputs; None for the deterministic fallback. "
            "Displayed in the operator-facing decision trace."
        ),
    )
    raw_response: str | None = Field(
        default=None,
        description=(
            "Sanitized raw LLM output retained for audit purposes only. "
            "MUST NOT be stored in the primary database or included in API responses "
            "after the audit write. Never propagated beyond the audit layer."
        ),
    )


# ---------------------------------------------------------------------------
# RecoverabilityFeatures
# ---------------------------------------------------------------------------


class RecoverabilityFeatures(BaseModel):
    """
    Input feature vector for the Recoverability_Model (LightGBM or Logistic
    Regression baseline).

    All features in this class are deterministic and provider-supplied — they
    are derived from gateway payment data, not from LLM output.  Using LLM
    output (``DiagnosisOutput.failure_category``) as a model feature is
    explicitly prohibited to prevent train/serve mismatch and LLM dependency
    in the prediction path.  FR-MODEL-002.

    The ``feature_version`` field must match the model artifact's expected
    feature version at serve time.  A mismatch must be treated as a hard
    error — do not silently produce incorrect predictions with mismatched
    features.
    """

    model_config = ConfigDict(frozen=True)

    # ---- Payment features ----

    amount_paise: int = Field(
        ge=1,
        description=(
            "Payment amount in minor currency units (paise). "
            "ML models may convert to float internally but must never store "
            "the float as an authoritative financial value. DATA-003."
        ),
    )
    currency: CurrencyCode = Field(
        description="ISO 4217 currency code. DATA-002.",
    )
    failure_category: FailureCategory = Field(
        description=(
            "The failure category as determined by the *deterministic fallback classifier* "
            "(derived from error_code / failure_code directly). "
            "This is a deterministic provider-supplied feature — "
            "NOT the LLM-produced DiagnosisOutput.failure_category."
        ),
    )
    payment_method: str | None = Field(
        default=None,
        description=(
            "Razorpay payment method string (e.g. 'card', 'upi', 'netbanking'). "
            "None if not available."
        ),
    )

    # ---- Temporal features ----

    time_since_failure_seconds: float = Field(
        ge=0.0,
        description=(
            "Seconds elapsed between the payment failure and the moment features were "
            "extracted (decision_timestamp - failure_timestamp). "
            "Computed using the explicit decision_timestamp stored on the case — "
            "NOT using runtime NOW(), to prevent train/serve mismatch. "
            "Float for model input only; never stored as an authoritative financial value."
        ),
    )
    hour_of_day: int = Field(
        ge=0,
        le=23,
        description="Hour of the day (0–23) when the payment failure occurred (UTC).",
    )
    day_of_week: int = Field(
        ge=0,
        le=6,
        description="Day of the week (0=Monday, 6=Sunday) when the payment failure occurred.",
    )

    # ---- Customer features ----

    previous_failure_count: int = Field(
        ge=0,
        description=(
            "Total number of prior payment failures for this payment entity. "
            "Integer, never float."
        ),
    )
    has_subscription: bool = Field(
        description="True if the failed payment is associated with a subscription.",
    )
    subscription_billing_amount_paise: int | None = Field(
        default=None,
        description=(
            "Regular billing amount for the associated subscription, in paise. "
            "None if the payment is not a subscription charge."
        ),
    )

    # ---- Model metadata ----

    feature_version: str = Field(
        description=(
            "Version identifier of the feature schema used to construct this vector "
            "(e.g. 'v1.0'). Must match the model artifact's expected feature_version "
            "at serve time.  A mismatch is a hard error."
        ),
    )


# ---------------------------------------------------------------------------
# PolicyConfig
# ---------------------------------------------------------------------------


class PolicyConfig(BaseModel):
    """
    Active policy snapshot passed to the Policy_Engine and Intervention_Engine.

    This is a *read-only* view of the active policy configuration — it is
    constructed once when the policy is loaded and passed as an immutable
    value through the decision pipeline.  All rule thresholds and limits are
    sourced from this object; no rule may reference a hardcoded constant.

    Policy lifecycle: draft → validated → active → retired.  Only one policy
    version may be in the ``active`` state at any time per merchant.
    FR-POLICY-012, FR-POLICY-013.
    """

    model_config = ConfigDict(frozen=True)

    policy_id: str = Field(description="Unique identifier for this policy record.")
    merchant_id: str = Field(description="Merchant to which this policy belongs.")
    version: str = Field(
        description="Human-readable version string (e.g. 'v3'). Recorded in every AuditEvent.",
    )

    # ---- Recoverability gate ----

    min_confidence: ProbabilityScore = Field(
        description=(
            "Minimum recovery_probability required for an intervention to proceed. "
            "Interventions with a lower score are blocked by the 'confidence_threshold' rule. "
            "FR-POLICY-002."
        ),
    )

    # ---- Rate and budget controls ----

    cooldown_seconds: int = Field(
        ge=0,
        description=(
            "Minimum seconds that must elapse between consecutive interventions on the "
            "same case.  The 'cooldown' rule blocks interventions attempted before this "
            "window expires.  FR-POLICY-005."
        ),
    )
    max_retries: dict[str, int] = Field(
        description=(
            "Maximum number of retry attempts per intervention_type string. "
            "e.g. {'RETRY': 3, 'PAYMENT_LINK': 2}. "
            "The 'retry_budget' rule blocks attempts at or above this count. "
            "FR-POLICY-004."
        ),
    )
    max_amount: dict[str, int] = Field(
        description=(
            "Maximum payment_amount_at_risk (in paise) per currency code for which "
            "automated interventions are permitted. "
            "e.g. {'INR': 500000} means automated interventions blocked above ₹5000. "
            "The 'amount_limit' rule enforces this.  FR-POLICY-003."
        ),
    )

    # ---- Human approval gate ----

    human_approval_threshold: int = Field(
        ge=0,
        description=(
            "Paise threshold above which an intervention must be reviewed by a human "
            "before execution.  The 'human_approval' rule routes interventions to the "
            "HUMAN_REVIEW queue when payment_amount_at_risk exceeds this value. "
            "FR-POLICY-007."
        ),
    )

    # ---- Attribution ----

    attribution_window_seconds: int = Field(
        ge=0,
        description=(
            "Seconds after intervention execution within which a payment recovery is "
            "classified as DIRECT_INTERVENTION.  Recoveries outside this window are "
            "classified as ORGANIC.  Default: 86400 (24 hours).  FR-ATTR-006."
        ),
    )

    # ---- Exposure tier thresholds ----

    exposure_thresholds: dict[str, int] = Field(
        description=(
            "Paise thresholds that map payment_amount_at_risk to an ExposureTier. "
            "e.g. {'HIGH': 100000, 'MEDIUM': 20000} means: "
            "  ≥ 100000 paise → HIGH, ≥ 20000 paise → MEDIUM, else LOW. "
            "Keys must be 'HIGH' and 'MEDIUM'.  FR-RISK-003."
        ),
    )


# ---------------------------------------------------------------------------
# InterventionCandidate
# ---------------------------------------------------------------------------


class InterventionCandidate(BaseModel):
    """
    A single candidate intervention produced by the Intervention_Engine.

    Candidates are ranked by ``expected_recovery_value`` (descending) before
    being passed to the Policy_Engine for evaluation.  FR-INTVN-001,
    FR-INTVN-002, FR-INTVN-004.

    ``expected_recovery_value`` is an integer in paise computed as::

        EV = floor(recovery_probability × payment_amount_at_risk) - cost - risk_penalty

    using ``decimal.Decimal`` with ``ROUND_FLOOR``.  The intermediate Decimal
    is discarded; only the ``int`` result is stored.  DATA-001, DATA-003.
    """

    model_config = ConfigDict(frozen=True)

    intervention_type: InterventionType = Field(
        description="The type of recovery action this candidate represents.",
    )
    execution_capability: ExecutionCapability = Field(
        description=(
            "What the Execution_Engine can actually do for this candidate. "
            "EXECUTABLE candidates result in direct gateway API calls; "
            "others produce recommendations or internal actions."
        ),
    )
    recovery_probability: ProbabilityScore = Field(
        description=(
            "P(recovery | context, action) produced by the Recoverability_Model. "
            "Not a financial value — used only for EV calculation."
        ),
    )
    expected_recovery_value: int = Field(
        description=(
            "Expected recovery value in integer paise: "
            "floor(P × amount_at_risk) - cost - risk_penalty. "
            "Negative values indicate the intervention is not expected to be profitable. "
            "Integer, never float. DATA-001, DATA-003."
        ),
    )
    reasoning: str | None = Field(
        default=None,
        description=(
            "Human-readable explanation of why this intervention was selected as a "
            "candidate.  Displayed in the operator-facing decision trace."
        ),
    )
    model_version: str = Field(
        description=(
            "Version of the Recoverability_Model artifact that produced "
            "recovery_probability.  Recorded in the AuditEvent.  AI-MODEL-002."
        ),
    )
