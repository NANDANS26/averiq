"""VerificationEngine — reconciles external operation outcomes with the gateway.

Dispatches to the appropriate ReconciliationStrategy based on intervention type.

ReconciliationStrategies:
  PaymentLinkReconciliationStrategy:
    - GET /v1/payment_links/:link_id → check status='paid' AND amount_paid > 0
    - GET /v1/payments/:payment_id → check status='captured'
    - RECOVERED only when BOTH conditions true (link paid AND payment captured)
    - Link created only → STILL_FAILED (not recovered; case stays VERIFYING)
    - Timeout → TIMEOUT (never FAILED from timeout alone)

  SubscriptionReconciliationStrategy:
    - GET /v1/subscriptions/:subscription_id
    - Active subscription alone NOT sufficient — must confirm overdue payment collected
    - Halted/cancelled → STILL_FAILED; pending → TIMEOUT (reschedule)
    - NOTE: active subscription ≠ overdue payment collected

  InternalActionReconciliationStrategy:
    - No external call; check internal workflow status
    - Returns RECOVERED if case reached expected state

  RecommendationOnlyReconciliationStrategy:
    - No external verification; outcome = PARTIAL (RECOMMENDATION_DELIVERED semantics)

VerificationOutcome enum values: RECOVERED, STILL_FAILED, TIMEOUT, PARTIAL

Requirements: FR-VERIFY-001 through FR-VERIFY-005
"""
from __future__ import annotations

import asyncio
from typing import Protocol

import structlog

from backend.gateway.abstract import GatewayAbstraction
from shared.contracts.enums import VerificationOutcome

logger = structlog.get_logger(__name__)


class ReconciliationStrategy(Protocol):
    """Protocol for per-intervention-type reconciliation strategies.

    Each strategy queries the gateway (or internal state) and returns a
    VerificationOutcome without making any domain state changes.
    """

    async def reconcile(
        self,
        external_operation_id: str,
        gateway: GatewayAbstraction,
        timeout_seconds: float,
    ) -> VerificationOutcome:
        """Reconcile the external operation and return its current outcome.

        Args:
            external_operation_id: The gateway-side reference ID for this
                operation (e.g., Razorpay payment link ID or subscription ID).
            gateway: The GatewayAbstraction implementation to query.
            timeout_seconds: Maximum seconds to wait for a gateway response.
                On asyncio.TimeoutError the strategy must return TIMEOUT.

        Returns:
            A VerificationOutcome describing the current state of the operation.
        """
        ...


class PaymentLinkReconciliationStrategy:
    """Verify payment link recovery.

    RECOVERED only when BOTH conditions hold:
      1. GET /v1/payment_links/:link_id → status='paid' AND amount_paid > 0
      2. GET /v1/payments/:payment_id → status='captured'

    Critical invariants (FR-VERIFY-001, FR-VERIFY-002):
      - link_created only → STILL_FAILED (not recovered; case stays VERIFYING)
      - link_paid but payment ID not yet available → PARTIAL (re-verify)
      - link_paid + payment captured → RECOVERED
      - Gateway timeout at either step → TIMEOUT (never FAILED from timeout alone)
    """

    async def reconcile(
        self,
        external_operation_id: str,
        gateway: GatewayAbstraction,
        timeout_seconds: float = 30.0,
    ) -> VerificationOutcome:
        """Reconcile a PAYMENT_LINK intervention outcome.

        Steps:
          1. GET /v1/payment_links/:link_id
             - status != 'paid' or amount_paid == 0 → STILL_FAILED
          2. Extract payment_id from link response.
             - No payment_id yet → PARTIAL (link paid but payment not yet captured)
          3. GET /v1/payments/:payment_id
             - status == 'captured' → RECOVERED
             - otherwise → PARTIAL (link paid; payment pending capture)

        Requirements: FR-VERIFY-001, FR-VERIFY-002
        """
        log = logger.bind(
            strategy="PaymentLinkReconciliationStrategy",
            external_operation_id=external_operation_id,
        )

        # Step 1: Fetch the payment link status.
        try:
            link_data = await asyncio.wait_for(
                gateway.get_payment_link(external_operation_id),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            log.warning("Payment link fetch timed out")
            return VerificationOutcome.TIMEOUT

        link_status = link_data.get("status", "")
        amount_paid = link_data.get("amount_paid", 0)

        # Link has not yet been paid — case is not recovered.
        if link_status != "paid" or not amount_paid:
            log.info(
                "Payment link not yet paid",
                link_status=link_status,
                amount_paid=amount_paid,
            )
            return VerificationOutcome.STILL_FAILED

        # Step 2: Extract the associated payment ID from the link response.
        # Razorpay embeds the payment list under link_data["payments"]["items"].
        payment_id: str | None = None
        payments_data = link_data.get("payments")
        if isinstance(payments_data, dict):
            items = payments_data.get("items", [])
            if items and isinstance(items, list):
                payment_id = items[0].get("id")

        if not payment_id:
            # Link is paid but the payment record is not yet available.
            # This is a transient intermediate state — re-verify later.
            log.info("Payment link paid but no payment_id found yet; returning PARTIAL")
            return VerificationOutcome.PARTIAL

        # Step 3: Confirm the associated payment has been captured.
        try:
            payment_data = await asyncio.wait_for(
                gateway.get_payment(payment_id),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            log.warning("Payment fetch timed out", payment_id=payment_id)
            return VerificationOutcome.TIMEOUT

        if payment_data.get("status") == "captured":
            log.info("Payment captured; marking RECOVERED", payment_id=payment_id)
            return VerificationOutcome.RECOVERED

        # Payment exists but has not reached 'captured' status yet.
        log.info(
            "Payment not yet captured; returning PARTIAL",
            payment_id=payment_id,
            payment_status=payment_data.get("status"),
        )
        return VerificationOutcome.PARTIAL


class SubscriptionReconciliationStrategy:
    """Verify subscription recovery.

    IMPORTANT: An active subscription status alone is NOT sufficient to mark
    RECOVERED. The specific overdue payment must be confirmed as collected.
    (FR-VERIFY-003)

    Status mapping:
      active + payment confirmed → RECOVERED
      active (no payment confirmed) → PARTIAL (re-verify to confirm capture)
      halted / cancelled / completed → STILL_FAILED
      pending → TIMEOUT semantics (unresolved; schedule re-verification)
    """

    async def reconcile(
        self,
        external_operation_id: str,
        gateway: GatewayAbstraction,
        timeout_seconds: float = 30.0,
    ) -> VerificationOutcome:
        """Reconcile a subscription-based intervention outcome.

        1. GET /v1/subscriptions/:subscription_id
        2. Map subscription status to VerificationOutcome:
           - 'halted' | 'cancelled' | 'completed' → STILL_FAILED
           - 'pending'                             → TIMEOUT (reschedule)
           - 'active'                              → PARTIAL
             (active subscription is necessary but NOT sufficient for RECOVERED;
             a future implementation should confirm the overdue invoice payment)

        Requirements: FR-VERIFY-001, FR-VERIFY-003
        """
        log = logger.bind(
            strategy="SubscriptionReconciliationStrategy",
            external_operation_id=external_operation_id,
        )

        try:
            sub_data = await asyncio.wait_for(
                gateway.get_subscription(external_operation_id),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            log.warning("Subscription fetch timed out")
            return VerificationOutcome.TIMEOUT

        sub_status = sub_data.get("status", "")
        log.info("Subscription status fetched", sub_status=sub_status)

        # Terminal failure states — no recovery possible.
        if sub_status in ("halted", "cancelled", "completed"):
            return VerificationOutcome.STILL_FAILED

        if sub_status == "pending":
            # Subscription is still pending (Razorpay internal retry not yet complete).
            # Treat as unresolved — schedule re-verification.
            return VerificationOutcome.TIMEOUT

        if sub_status == "active":
            # The subscription relationship is restored, but this alone does NOT
            # confirm that the specific overdue payment was collected.
            #
            # A full implementation would look up the associated invoice / charge
            # and verify the overdue amount was captured.  Until that confirmation
            # is available, return PARTIAL to signal "re-verify to confirm payment
            # captured".  This ensures the case does not prematurely reach RECOVERED.
            #
            # FR-VERIFY-003: "Active subscription alone NOT sufficient."
            return VerificationOutcome.PARTIAL

        # Unknown status — cannot determine outcome; schedule re-verification.
        log.warning("Unrecognised subscription status; returning TIMEOUT", sub_status=sub_status)
        return VerificationOutcome.TIMEOUT


class InternalActionReconciliationStrategy:
    """Verify internal actions — no external gateway call required.

    Internal actions (PAUSE_SUBSCRIPTION, ESCALATE_TO_HUMAN, STOP) are
    executed entirely within the Averiq system.  Verification consists of
    checking whether the case reached its expected internal state.

    Since this strategy is invoked only after the Execution_Engine has
    recorded a SUCCEEDED status for the internal operation, it returns
    RECOVERED unconditionally.

    Requirements: FR-VERIFY-004, FR-VERIFY-005
    """

    async def reconcile(
        self,
        external_operation_id: str,
        gateway: GatewayAbstraction,
        timeout_seconds: float = 30.0,
    ) -> VerificationOutcome:
        """Internal actions are considered recovered once the workflow completes."""
        logger.bind(
            strategy="InternalActionReconciliationStrategy",
            external_operation_id=external_operation_id,
        ).info("Internal action verified; returning RECOVERED")
        return VerificationOutcome.RECOVERED


class RecommendationOnlyReconciliationStrategy:
    """No external verification for RECOMMENDATION_ONLY interventions.

    When no programmatic execution path exists (e.g., RETRY for a standalone
    failed payment), Averiq produces a human-readable recommendation.
    There is nothing to verify externally; the outcome is treated as
    PARTIAL to signal "completed but not independently verifiable".

    Requirements: FR-VERIFY-005
    """

    async def reconcile(
        self,
        external_operation_id: str,
        gateway: GatewayAbstraction,
        timeout_seconds: float = 30.0,
    ) -> VerificationOutcome:
        """Recommendation delivered — no external verification possible.

        Returns PARTIAL to signal "completed but unverifiable externally".
        This maps to RECOMMENDATION_DELIVERED semantics in the design doc.
        """
        logger.bind(
            strategy="RecommendationOnlyReconciliationStrategy",
            external_operation_id=external_operation_id,
        ).info("Recommendation-only intervention; returning PARTIAL")
        return VerificationOutcome.PARTIAL


# ---------------------------------------------------------------------------
# Strategy dispatch map
# ---------------------------------------------------------------------------
# Keys are ExecutionCapability string values (from shared/contracts/enums.py).
# The map is intentionally open for extension: any unmapped capability falls
# back to RecommendationOnlyReconciliationStrategy.

_STRATEGY_MAP: dict[str, type] = {
    # EXECUTABLE — creates a Razorpay Payment Link
    "CREATE_PAYMENT_LINK": PaymentLinkReconciliationStrategy,
    # INTERNAL_ACTION — Razorpay auto-retries subscriptions; Averiq verifies
    "RETRY_CHARGE": SubscriptionReconciliationStrategy,
    # INTERNAL_ACTION — subscription paused within the system
    "PAUSE_SUBSCRIPTION": InternalActionReconciliationStrategy,
    # INTERNAL_ACTION — cancel subscription
    "CANCEL_SUBSCRIPTION": InternalActionReconciliationStrategy,
    # RECOMMENDATION_ONLY / INTERNAL_ACTION — notification or no-op
    "SEND_NOTIFICATION": RecommendationOnlyReconciliationStrategy,
    "NO_OP": RecommendationOnlyReconciliationStrategy,
}


# ---------------------------------------------------------------------------
# VerificationEngine
# ---------------------------------------------------------------------------


class VerificationEngine:
    """Dispatches outcome reconciliation to the appropriate per-type strategy.

    Instantiated with a GatewayAbstraction and an optional gateway timeout.
    Callers invoke ``verify(action_type, external_operation_id)`` and receive
    a ``VerificationOutcome``.

    The engine does NOT mutate any domain state — it only queries the gateway
    and returns the outcome.  State transitions are the caller's responsibility.

    Requirements: FR-VERIFY-001 through FR-VERIFY-005
    """

    def __init__(
        self,
        gateway: GatewayAbstraction,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._gateway = gateway
        self._timeout = timeout_seconds

    def get_strategy(self, action_type: str) -> ReconciliationStrategy:
        """Return the ReconciliationStrategy instance for the given action_type.

        ``action_type`` must be an ``ExecutionCapability`` string value.
        Unknown types fall back to ``RecommendationOnlyReconciliationStrategy``.
        """
        strategy_class = _STRATEGY_MAP.get(
            action_type, RecommendationOnlyReconciliationStrategy
        )
        return strategy_class()  # type: ignore[return-value]

    async def verify(
        self,
        action_type: str,
        external_operation_id: str,
    ) -> VerificationOutcome:
        """Verify the outcome of an executed intervention.

        Args:
            action_type: The ``ExecutionCapability`` string for the operation
                (e.g., "CREATE_PAYMENT_LINK", "RETRY_CHARGE").
            external_operation_id: The gateway-side reference ID recorded
                at execution time.

        Returns:
            A ``VerificationOutcome`` reflecting the current gateway state.
        """
        strategy = self.get_strategy(action_type)
        log = logger.bind(
            action_type=action_type,
            external_operation_id=external_operation_id,
            strategy=type(strategy).__name__,
        )
        log.info("Starting verification")

        outcome = await strategy.reconcile(
            external_operation_id=external_operation_id,
            gateway=self._gateway,
            timeout_seconds=self._timeout,
        )

        log.info("Verification complete", outcome=outcome.value)
        return outcome
