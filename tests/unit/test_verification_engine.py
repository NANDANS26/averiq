"""Unit tests for VerificationEngine and ReconciliationStrategies.

Tests cover the 6 core strategy behaviours defined in the task spec:
  1. PaymentLink status='created' → STILL_FAILED
  2. PaymentLink status='paid' but no payment_id → PARTIAL
  3. Subscription status='halted' → STILL_FAILED
  4. Subscription status='active' → PARTIAL (not yet RECOVERED)
  5. InternalAction → RECOVERED
  6. RecommendationOnly → PARTIAL

All tests use mocked GatewayAbstraction — no network I/O.
All tests are marked @pytest.mark.unit and @pytest.mark.asyncio.

Requirements: FR-VERIFY-001 through FR-VERIFY-005
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.verification_engine.engine import (
    InternalActionReconciliationStrategy,
    PaymentLinkReconciliationStrategy,
    RecommendationOnlyReconciliationStrategy,
    SubscriptionReconciliationStrategy,
    VerificationEngine,
)
from shared.contracts.enums import VerificationOutcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gateway(
    payment_link_data: dict | None = None,
    payment_data: dict | None = None,
    subscription_data: dict | None = None,
) -> MagicMock:
    """Return a mock GatewayAbstraction with pre-configured async methods."""
    gateway = MagicMock()
    gateway.get_payment_link = AsyncMock(return_value=payment_link_data or {})
    gateway.get_payment = AsyncMock(return_value=payment_data or {})
    gateway.get_subscription = AsyncMock(return_value=subscription_data or {})
    return gateway


# ---------------------------------------------------------------------------
# Test 1: PaymentLink status='created' → STILL_FAILED
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_payment_link_created_returns_still_failed() -> None:
    """PaymentLink strategy returns STILL_FAILED when link status is 'created'.

    A link in 'created' state has not been paid — the case is not recovered.
    The strategy must NOT call get_payment() in this path.

    Requirements: FR-VERIFY-001, FR-VERIFY-002
    """
    gateway = _make_gateway(
        payment_link_data={"status": "created", "amount_paid": 0}
    )
    strategy = PaymentLinkReconciliationStrategy()

    outcome = await strategy.reconcile(
        external_operation_id="plink_test_001",
        gateway=gateway,
        timeout_seconds=5.0,
    )

    assert outcome == VerificationOutcome.STILL_FAILED
    gateway.get_payment.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: PaymentLink status='paid' but no payment_id → PARTIAL
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_payment_link_paid_no_payment_id_returns_partial() -> None:
    """PaymentLink strategy returns PARTIAL when link is paid but payment_id unavailable.

    The link has been paid (status='paid', amount_paid > 0) but no payment
    record is embedded yet — a transient intermediate state.  PARTIAL signals
    that re-verification is required.

    Requirements: FR-VERIFY-001, FR-VERIFY-002
    """
    gateway = _make_gateway(
        payment_link_data={
            "status": "paid",
            "amount_paid": 50000,
            "payments": {"items": []},  # no payment record yet
        }
    )
    strategy = PaymentLinkReconciliationStrategy()

    outcome = await strategy.reconcile(
        external_operation_id="plink_test_002",
        gateway=gateway,
        timeout_seconds=5.0,
    )

    assert outcome == VerificationOutcome.PARTIAL
    gateway.get_payment.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Subscription status='halted' → STILL_FAILED
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_subscription_halted_returns_still_failed() -> None:
    """Subscription strategy returns STILL_FAILED for halted subscriptions.

    A halted subscription is a terminal failure state — no recovery possible
    without external intervention.

    Requirements: FR-VERIFY-001, FR-VERIFY-003
    """
    gateway = _make_gateway(
        subscription_data={"status": "halted"}
    )
    strategy = SubscriptionReconciliationStrategy()

    outcome = await strategy.reconcile(
        external_operation_id="sub_test_001",
        gateway=gateway,
        timeout_seconds=5.0,
    )

    assert outcome == VerificationOutcome.STILL_FAILED


# ---------------------------------------------------------------------------
# Test 4: Subscription status='active' → PARTIAL (not yet RECOVERED)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_subscription_active_returns_partial_not_recovered() -> None:
    """Subscription strategy returns PARTIAL (not RECOVERED) for active subscriptions.

    An active subscription is necessary but NOT sufficient to mark the case
    as RECOVERED.  The overdue payment must still be confirmed as collected.
    PARTIAL signals that re-verification is required.

    FR-VERIFY-003: 'Active subscription alone NOT sufficient.'

    Requirements: FR-VERIFY-001, FR-VERIFY-003
    """
    gateway = _make_gateway(
        subscription_data={"status": "active"}
    )
    strategy = SubscriptionReconciliationStrategy()

    outcome = await strategy.reconcile(
        external_operation_id="sub_test_002",
        gateway=gateway,
        timeout_seconds=5.0,
    )

    assert outcome == VerificationOutcome.PARTIAL
    assert outcome != VerificationOutcome.RECOVERED


# ---------------------------------------------------------------------------
# Test 5: InternalAction → RECOVERED
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_internal_action_returns_recovered() -> None:
    """InternalAction strategy always returns RECOVERED without calling the gateway.

    Internal actions (e.g., PAUSE_SUBSCRIPTION) are executed entirely within
    the system.  Once the execution engine records success, verification is
    trivially RECOVERED.

    Requirements: FR-VERIFY-004
    """
    gateway = _make_gateway()
    strategy = InternalActionReconciliationStrategy()

    outcome = await strategy.reconcile(
        external_operation_id="internal_op_001",
        gateway=gateway,
        timeout_seconds=5.0,
    )

    assert outcome == VerificationOutcome.RECOVERED
    gateway.get_payment_link.assert_not_called()
    gateway.get_payment.assert_not_called()
    gateway.get_subscription.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6: RecommendationOnly → PARTIAL
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recommendation_only_returns_partial() -> None:
    """RecommendationOnly strategy always returns PARTIAL — no external verification.

    Recommendation-only interventions (e.g., SEND_NOTIFICATION, NO_OP) have
    no external outcome to verify.  PARTIAL maps to RECOMMENDATION_DELIVERED
    semantics: the action was taken but its effect is not independently
    verifiable.

    Requirements: FR-VERIFY-005
    """
    gateway = _make_gateway()
    strategy = RecommendationOnlyReconciliationStrategy()

    outcome = await strategy.reconcile(
        external_operation_id="notif_op_001",
        gateway=gateway,
        timeout_seconds=5.0,
    )

    assert outcome == VerificationOutcome.PARTIAL
    gateway.get_payment_link.assert_not_called()
    gateway.get_payment.assert_not_called()
    gateway.get_subscription.assert_not_called()


# ---------------------------------------------------------------------------
# Bonus: VerificationEngine.verify dispatches correctly
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verification_engine_dispatches_to_correct_strategy() -> None:
    """VerificationEngine.verify dispatches to PaymentLinkReconciliationStrategy.

    Smoke-tests the top-level entry point: given action_type='CREATE_PAYMENT_LINK'
    and a link in 'created' state, the engine returns STILL_FAILED via the
    PaymentLink strategy.

    Requirements: FR-VERIFY-001
    """
    gateway = _make_gateway(
        payment_link_data={"status": "created", "amount_paid": 0}
    )
    engine = VerificationEngine(gateway=gateway, timeout_seconds=5.0)

    outcome = await engine.verify(
        action_type="CREATE_PAYMENT_LINK",
        external_operation_id="plink_dispatch_001",
    )

    assert outcome == VerificationOutcome.STILL_FAILED
