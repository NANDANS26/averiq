"""
Shared enum definitions for Averiq.

All enums are pure Python `enum.Enum` subclasses with no I/O or
implementation-layer imports.  They are the single authoritative source of
truth for every domain vocabulary term used across ``backend/`` and ``ai/``.
"""

from __future__ import annotations

from enum import Enum


# ---------------------------------------------------------------------------
# Failure diagnosis
# ---------------------------------------------------------------------------


class FailureCategory(str, Enum):
    """
    Canonical failure categories produced by the Root_Cause_Reasoner.

    Covers the nine classifications required by FR-DIAG-002.  The
    ``UNKNOWN`` value is the safe default when no other category can be
    assigned confidently.

    Note: The requirements document (FR-DIAG-002) lists the categories using
    Razorpay-centric terminology (CARD_EXPIRED, CARD_BLOCKED, NETWORK_ERROR,
    BANK_DECLINE, DUPLICATE_PAYMENT, SUBSCRIPTION_HALTED).  The task spec
    maps these to the nine values below, which align with the design document
    and the ``FailureCategory`` enum referenced throughout.  The mapping is
    preserved verbatim from the task spec to avoid drift.
    """

    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    """The customer's account or card did not have sufficient balance."""

    CARD_DECLINED = "CARD_DECLINED"
    """The card was declined by the issuing bank (generic decline)."""

    EXPIRED_CARD = "EXPIRED_CARD"
    """The card used for payment has passed its expiry date."""

    AUTHENTICATION_FAILURE = "AUTHENTICATION_FAILURE"
    """3-D Secure or other authentication step failed or was abandoned."""

    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    """An internal or bank-side technical error caused the failure."""

    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    """A network timeout occurred during payment processing."""

    FRAUD_SUSPECTED = "FRAUD_SUSPECTED"
    """The transaction was blocked due to suspected fraudulent activity."""

    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    """A velocity, daily, or single-transaction limit was exceeded."""

    UNKNOWN = "UNKNOWN"
    """Failure reason could not be reliably determined from available signals."""


# ---------------------------------------------------------------------------
# Case lifecycle
# ---------------------------------------------------------------------------


class CaseState(str, Enum):
    """
    All valid states in a ``RevenueRiskCase`` lifecycle.

    Matches the 13-state definition from FR-STATE-001 and the State Machine
    section of the design document.  Terminal states are: ``RECOVERED``,
    ``STOPPED``, ``ESCALATED``, and ``FAILED``.
    """

    OBSERVED = "OBSERVED"
    """Case created; awaiting initial risk assessment."""

    AT_RISK = "AT_RISK"
    """Revenue confirmed at risk; awaiting diagnosis."""

    DIAGNOSED = "DIAGNOSED"
    """Root cause identified by the Root_Cause_Reasoner."""

    RECOVERY_ELIGIBLE = "RECOVERY_ELIGIBLE"
    """Recoverability score meets the configured threshold; eligible for intervention."""

    ACTION_PENDING = "ACTION_PENDING"
    """Intervention selected; awaiting Policy_Engine evaluation."""

    ACTION_APPROVED = "ACTION_APPROVED"
    """Policy_Engine approved the intervention; awaiting execution."""

    ACTION_EXECUTING = "ACTION_EXECUTING"
    """Execution_Engine is actively executing the intervention."""

    VERIFYING = "VERIFYING"
    """Awaiting outcome confirmation from the payment gateway."""

    RETRYABLE = "RETRYABLE"
    """Outcome requires re-evaluation; exits to RECOVERY_ELIGIBLE subject to policy budget."""

    # --- Terminal states ---

    RECOVERED = "RECOVERED"
    """Payment confirmed recovered.  Terminal state."""

    STOPPED = "STOPPED"
    """Policy blocked all interventions or STOP intervention was selected.  Terminal state."""

    ESCALATED = "ESCALATED"
    """Retry budget exhausted or verification permanently ambiguous.  Terminal state."""

    FAILED = "FAILED"
    """Known, unrecoverable execution error.  Terminal state."""

    UNKNOWN_TERMINAL = "UNKNOWN_TERMINAL"
    """
    Catch-all terminal state used when the system cannot classify the final
    outcome into any of the four standard terminal states.  Should be rare.
    Terminal state.
    """


# ---------------------------------------------------------------------------
# Intervention
# ---------------------------------------------------------------------------


class InterventionType(str, Enum):
    """
    The type of recovery action that can be applied to a ``RevenueRiskCase``.

    Maps directly to the Intervention Capability Register in the design
    document and FR-INTVN-001.
    """

    PAYMENT_LINK = "PAYMENT_LINK"
    """Create a Razorpay Payment Link for the customer to pay again (EXECUTABLE / VERIFIED)."""

    RETRY = "RETRY"
    """
    Retry the payment.

    For *standalone* failed payments this is RECOMMENDATION_ONLY / NOT_SUPPORTED —
    Razorpay has no API to retry a failed payment.  For subscriptions in
    ``pending`` state this is INTERNAL_ACTION / UNKNOWN.
    """

    CUSTOMER_ACTION = "CUSTOMER_ACTION"
    """
    Direct the customer to take an action such as updating their payment method
    for a subscription (EXECUTABLE / VERIFIED for subscription card-change flow).
    """

    PAUSE_SUBSCRIPTION = "PAUSE_SUBSCRIPTION"
    """Pause a subscription to prevent further failed charges."""

    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"
    """Route the case to a human reviewer (INTERNAL_ACTION)."""

    STOP = "STOP"
    """Stop all recovery attempts for the case (INTERNAL_ACTION)."""

    WAIVER = "WAIVER"
    """Waive the outstanding amount as a goodwill gesture."""

    CUSTOM_NOTIFICATION = "CUSTOM_NOTIFICATION"
    """Send a custom notification to the customer."""


class ExecutionCapability(str, Enum):
    """
    What the Execution_Engine can actually do for a given intervention.

    Mirrors the capability register in the design document and FR-INTVN-001.
    """

    CREATE_PAYMENT_LINK = "CREATE_PAYMENT_LINK"
    """Call ``POST /v1/payment_links`` on Razorpay to create a Payment Link."""

    RETRY_CHARGE = "RETRY_CHARGE"
    """
    Retry a subscription charge.

    Only valid for subscriptions in ``pending`` state — Razorpay handles this
    internally.  NOT valid for standalone failed payments.
    """

    SEND_NOTIFICATION = "SEND_NOTIFICATION"
    """Send a notification to the customer via the configured channel."""

    PAUSE_SUBSCRIPTION = "PAUSE_SUBSCRIPTION"
    """Pause a Razorpay subscription."""

    CANCEL_SUBSCRIPTION = "CANCEL_SUBSCRIPTION"
    """Cancel a Razorpay subscription."""

    CAPTURE_PAYMENT = "CAPTURE_PAYMENT"
    """
    Capture an *authorized* payment via ``POST /v1/payments/:id/capture``.

    CRITICAL: This MUST NOT be called on a payment in ``failed`` status.
    Only valid for payments in ``authorized`` status.
    """

    NO_OP = "NO_OP"
    """
    No external operation is performed.  Used for RECOMMENDATION_ONLY and
    INTERNAL_ACTION interventions where Averiq does not call a gateway API.
    """


class ProviderSupportStatus(str, Enum):
    """
    The verified status of a gateway operation in the Razorpay API Capability
    Register.

    Used to track which operations are safe to execute programmatically.
    """

    SUPPORTED = "SUPPORTED"
    """Confirmed in official Razorpay documentation (maps to VERIFIED in the register)."""

    UNSUPPORTED = "UNSUPPORTED"
    """Confirmed to NOT exist in the Razorpay API (maps to NOT_SUPPORTED)."""

    REQUIRES_MANUAL = "REQUIRES_MANUAL"
    """
    Not confirmed in documentation or requires a manual / dashboard step
    (maps to UNVERIFIED or UNKNOWN in the register).
    """


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------


class ExposureTier(str, Enum):
    """
    Financial exposure classification based on ``payment_amount_at_risk``.

    Thresholds are configured in the active Policy — not hardcoded here.
    FR-RISK-003.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RecoveryRiskTier(str, Enum):
    """
    Estimated recovery difficulty tier derived from the Recoverability_Model
    output and the diagnosed ``FailureCategory``.

    Distinct from ``ExposureTier`` — reflects difficulty, not financial size.
    FR-RISK-004.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# Users and access control
# ---------------------------------------------------------------------------


class UserRole(str, Enum):
    """
    RBAC roles as defined in SEC-AUTH-002 and the permissions matrix.

    The ``SYSTEM`` role is used exclusively for internal service-to-service
    calls and MUST NOT be assignable to human users via any API endpoint.
    """

    ADMIN = "ADMIN"
    """Full system access including Policy management and user administration."""

    OPERATOR = "OPERATOR"
    """Can approve/reject human-review interventions and perform limited write operations."""

    VIEWER = "VIEWER"
    """Read-only access to cases, audit events, and analytics."""

    SYSTEM = "SYSTEM"
    """
    Reserved for internal service-to-service operations only.

    MUST NOT be assigned to human users via any API or administrative interface.
    Validation at the API layer MUST reject any request payload that sets
    ``role = 'SYSTEM'`` from a human caller.
    """


# ---------------------------------------------------------------------------
# Operating modes
# ---------------------------------------------------------------------------


class OperatingMode(str, Enum):
    """
    The operating mode of the Averiq instance, controlling which gateway
    implementation is used and how results are labeled in the UI and logs.

    All UI displays, log entries, and API responses MUST include this label
    when any data is produced or consumed.
    """

    SIMULATED = "SIMULATED"
    """
    Uses ``FakeRazorpayGateway`` — no external API calls are made.
    Safe for development, demos, and bulk evaluation runs.
    """

    REAL_TEST_MODE = "REAL_TEST_MODE"
    """
    Uses ``RazorpayTestGateway`` with real Razorpay test-mode credentials.
    Clearly labeled throughout.  Subject to Razorpay test-mode limits
    (e.g., 30 Payment Links per business).
    """

    DEMO_FIXTURE = "DEMO_FIXTURE"
    """
    A deterministic, pre-seeded dataset used to demonstrate specific system
    behaviours.  All records are labeled ``is_demo_fixture=True``.
    """


# ---------------------------------------------------------------------------
# Policy decisions
# ---------------------------------------------------------------------------


class PolicyDecisionOutcome(str, Enum):
    """
    The outcome of a Policy_Engine evaluation for a single intervention
    candidate.

    FR-POLICY-001, FR-POLICY-009.
    """

    APPROVED = "APPROVED"
    """No blocking rules fired; the intervention may proceed to execution."""

    BLOCKED = "BLOCKED"
    """One or more blocking rules fired; the intervention is not permitted."""

    HUMAN_REVIEW = "HUMAN_REVIEW"
    """
    The ``human_approval`` rule fired; the intervention is routed to the
    Human Review queue before any execution is permitted.
    """


# ---------------------------------------------------------------------------
# Revenue attribution
# ---------------------------------------------------------------------------


class AttributionClass(str, Enum):
    """
    How a recovered payment is attributed to Averiq's actions.

    FR-ATTR-002.
    """

    DIRECT_INTERVENTION = "DIRECT_INTERVENTION"
    """
    Recovery occurred within the configured ``attribution_window`` after an
    Averiq intervention was executed.

    This is an incremental measurement relative to a defined baseline cohort,
    NOT a causal claim.
    """

    ORGANIC = "ORGANIC"
    """Payment recovered without any Averiq intervention executed within the window."""

    UNKNOWN_ATTRIBUTION = "UNKNOWN_ATTRIBUTION"
    """Attribution is ambiguous; cannot be confidently classified."""


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class VerificationOutcome(str, Enum):
    """
    The outcome of a single verification attempt by the Verification_Engine.

    FR-VERIFY-004 through FR-VERIFY-007.
    """

    RECOVERED = "RECOVERED"
    """Gateway confirmed the expected success condition for the intervention type."""

    STILL_FAILED = "STILL_FAILED"
    """Gateway returned a status indicating failure or non-completion."""

    TIMEOUT = "TIMEOUT"
    """
    No definitive status was received within the configured verification window.

    TIMEOUT ≠ FAILED.  Requires re-verification or escalation — never assume
    FAILED from a timeout alone.
    """

    PARTIAL = "PARTIAL"
    """
    An intermediate state was observed (e.g., Payment Link paid but underlying
    payment not yet captured).  Triggers re-verification.
    """
