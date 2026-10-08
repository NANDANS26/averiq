"""Reconciliation strategy integration tests.

Tests all ReconciliationStrategy implementations and VerificationEngine
dispatch using mocked gateway objects. No real HTTP calls are made.

Requirements: FR-VERIFY-001 through FR-VERIFY-005
"""
from __future__ import annotations

import asyncio
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
    *,
    link_data: dict | None = None,
    payment_data: dict | None = None,
    sub_data: dict | None = None,
) -> MagicMock:
    """Return a minimal GatewayAbstraction mock."""
    gw = MagicMock()
    gw.get_payment_link = AsyncMock(return_value=link_data or {})
    gw.get_payment = AsyncMock(return_value=payment_data or {})
    gw.get_subscription = AsyncMock(return_value=sub_data or {})
    return gw


# ---------------------------------------------------------------------------
# PaymentLinkReconciliationStrategy
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_payment_link_paid_and_captured_returns_recovered() -> None:
    """Payment link status='paid' with a captured payment → RECOVERED.

    BOTH conditions must hold: link paid AND payment captured.

    Requirements: FR-VERIFY-001, FR-VERIFY-002
    """
    gw = _make_gateway(
        link_data={
            "status": "paid",
            "amount_paid": 50000,
            "payments": {"items": [{"id": "pay_123"}]},
        },
        payment_data={"status": "captured"},
    )

    result = await PaymentLinkReconciliationStrategy().reconcile("plink_1", gw, 5.0)

    assert result == VerificationOutcome.RECOVERED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_payment_link_created_returns_still_failed() -> None:
    """Payment link status='created' (not yet paid) → STILL_FAILED.

    A link that has merely been created is not sufficient for recovery.

    Requirements: FR-VERIFY-001, FR-VERIFY-002
    """
    gw = _make_gateway(link_data={"status": "created", "amount_paid": 0})

    result = await PaymentLinkReconciliationStrategy().reconcile("plink_1", gw, 5.0)

    assert result == VerificationOutcome.STILL_FAILED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_payment_link_timeout_returns_timeout() -> None:
    """Gateway timeout on payment link fetch → TIMEOUT (never FAILED).

    Requirements: FR-VERIFY-001
    """
    gw = MagicMock()
    gw.get_payment_link = AsyncMock(side_effect=asyncio.TimeoutError())

    result = await PaymentLinkReconciliationStrategy().reconcile("plink_1", gw, 5.0)

    assert result == VerificationOutcome.TIMEOUT


@pytest.mark.unit
@pytest.mark.asyncio
async def test_payment_link_paid_but_no_payment_id_returns_partial() -> None:
    """Payment link paid but no payment_id available yet → PARTIAL.

    Link paid is necessary but not sufficient; the payment capture must also
    be confirmed.  If the payment record is not yet available, return PARTIAL
    to trigger a re-verification.

    Requirements: FR-VERIFY-001, FR-VERIFY-002
    """
    gw = _make_gateway(
        link_data={
            "status": "paid",
            "amount_paid": 50000,
            "payments": {"items": []},  # no payment items yet
        }
    )

    result = await PaymentLinkReconciliationStrategy().reconcile("plink_1", gw, 5.0)

    assert result == VerificationOutcome.PARTIAL


@pytest.mark.unit
@pytest.mark.asyncio
async def test_payment_link_paid_but_payment_not_captured_returns_partial() -> None:
    """Payment link paid, payment exists but not captured → PARTIAL.

    Requirements: FR-VERIFY-002
    """
    gw = _make_gateway(
        link_data={
            "status": "paid",
            "amount_paid": 50000,
            "payments": {"items": [{"id": "pay_456"}]},
        },
        payment_data={"status": "authorized"},  # not captured
    )

    result = await PaymentLinkReconciliationStrategy().reconcile("plink_1", gw, 5.0)

    assert result == VerificationOutcome.PARTIAL


# ---------------------------------------------------------------------------
# SubscriptionReconciliationStrategy
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_subscription_halted_returns_still_failed() -> None:
    """Halted subscription → STILL_FAILED.

    Requirements: FR-VERIFY-003
    """
    gw = _make_gateway(sub_data={"status": "halted"})

    result = await SubscriptionReconciliationStrategy().reconcile("sub_1", gw, 5.0)

    assert result == VerificationOutcome.STILL_FAILED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_subscription_cancelled_returns_still_failed() -> None:
    """Cancelled subscription → STILL_FAILED.

    Requirements: FR-VERIFY-003
    """
    gw = _make_gateway(sub_data={"status": "cancelled"})

    result = await SubscriptionReconciliationStrategy().reconcile("sub_1", gw, 5.0)

    assert result == VerificationOutcome.STILL_FAILED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_subscription_active_returns_partial_not_recovered() -> None:
    """Active subscription alone is NOT sufficient for RECOVERED → PARTIAL.

    Active subscription ≠ overdue payment collected. Must return PARTIAL to
    trigger re-verification to confirm the specific payment was captured.

    Requirements: FR-VERIFY-003
    """
    gw = _make_gateway(sub_data={"status": "active"})

    result = await SubscriptionReconciliationStrategy().reconcile("sub_1", gw, 5.0)

    assert result == VerificationOutcome.PARTIAL
    assert result != VerificationOutcome.RECOVERED, (
        "Active subscription alone must NOT result in RECOVERED "
        "(FR-VERIFY-003: overdue payment must be independently confirmed)"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_subscription_timeout_returns_timeout() -> None:
    """Gateway timeout on subscription fetch → TIMEOUT.

    Requirements: FR-VERIFY-003
    """
    gw = MagicMock()
    gw.get_subscription = AsyncMock(side_effect=asyncio.TimeoutError())

    result = await SubscriptionReconciliationStrategy().reconcile("sub_1", gw, 5.0)

    assert result == VerificationOutcome.TIMEOUT


@pytest.mark.unit
@pytest.mark.asyncio
async def test_subscription_pending_returns_timeout() -> None:
    """Pending subscription → TIMEOUT (reschedule re-verification).

    Requirements: FR-VERIFY-003
    """
    gw = _make_gateway(sub_data={"status": "pending"})

    result = await SubscriptionReconciliationStrategy().reconcile("sub_1", gw, 5.0)

    assert result == VerificationOutcome.TIMEOUT


# ---------------------------------------------------------------------------
# InternalActionReconciliationStrategy
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_internal_action_returns_recovered() -> None:
    """Internal action strategy always returns RECOVERED (no gateway call needed).

    Requirements: FR-VERIFY-004, FR-VERIFY-005
    """
    gw = _make_gateway()

    result = await InternalActionReconciliationStrategy().reconcile("op_1", gw, 5.0)

    assert result == VerificationOutcome.RECOVERED


# ---------------------------------------------------------------------------
# RecommendationOnlyReconciliationStrategy
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recommendation_only_returns_partial() -> None:
    """Recommendation-only strategy returns PARTIAL (unverifiable externally).

    Requirements: FR-VERIFY-005
    """
    gw = _make_gateway()

    result = await RecommendationOnlyReconciliationStrategy().reconcile("op_1", gw, 5.0)

    assert result == VerificationOutcome.PARTIAL


# ---------------------------------------------------------------------------
# VerificationEngine dispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_dispatches_payment_link_strategy() -> None:
    """VerificationEngine routes CREATE_PAYMENT_LINK to PaymentLinkReconciliationStrategy.

    Requirements: FR-VERIFY-001
    """
    gw = _make_gateway(link_data={"status": "created", "amount_paid": 0})

    engine = VerificationEngine(gateway=gw, timeout_seconds=5.0)
    result = await engine.verify("CREATE_PAYMENT_LINK", "plink_1")

    assert result == VerificationOutcome.STILL_FAILED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_dispatches_subscription_strategy() -> None:
    """VerificationEngine routes RETRY_CHARGE to SubscriptionReconciliationStrategy.

    Requirements: FR-VERIFY-003
    """
    gw = _make_gateway(sub_data={"status": "halted"})

    engine = VerificationEngine(gateway=gw, timeout_seconds=5.0)
    result = await engine.verify("RETRY_CHARGE", "sub_1")

    assert result == VerificationOutcome.STILL_FAILED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_dispatches_internal_action_strategy() -> None:
    """VerificationEngine routes PAUSE_SUBSCRIPTION to InternalActionReconciliationStrategy.

    Requirements: FR-VERIFY-004
    """
    gw = _make_gateway()

    engine = VerificationEngine(gateway=gw, timeout_seconds=5.0)
    result = await engine.verify("PAUSE_SUBSCRIPTION", "op_1")

    assert result == VerificationOutcome.RECOVERED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_unknown_action_falls_back_to_recommendation_only() -> None:
    """Unknown action_type falls back to RecommendationOnlyReconciliationStrategy → PARTIAL.

    Requirements: FR-VERIFY-005
    """
    gw = _make_gateway()

    engine = VerificationEngine(gateway=gw, timeout_seconds=5.0)
    result = await engine.verify("SOME_UNKNOWN_ACTION", "op_xyz")

    assert result == VerificationOutcome.PARTIAL


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_payment_link_recovered_end_to_end() -> None:
    """Full CREATE_PAYMENT_LINK → RECOVERED path through the VerificationEngine.

    Requirements: FR-VERIFY-001, FR-VERIFY-002
    """
    gw = _make_gateway(
        link_data={
            "status": "paid",
            "amount_paid": 75000,
            "payments": {"items": [{"id": "pay_789"}]},
        },
        payment_data={"status": "captured"},
    )

    engine = VerificationEngine(gateway=gw, timeout_seconds=5.0)
    result = await engine.verify("CREATE_PAYMENT_LINK", "plink_2")

    assert result == VerificationOutcome.RECOVERED
