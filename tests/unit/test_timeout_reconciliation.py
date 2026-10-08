"""Unit tests for TimeoutReconciliationFlow.

Tests cover all three TIMEOUT reconciliation outcome cases defined by task 14.3:
  1. RECOVERED outcome → sets status="SUCCEEDED" → returns CASE_A_SUCCEEDED
  2. STILL_FAILED outcome → sets status="FAILED" → returns CASE_B_FAILED
  3. TIMEOUT outcome → sets status="UNKNOWN" → returns CASE_C_UNRESOLVED
  4. PARTIAL outcome → sets status="UNKNOWN" → returns CASE_C_UNRESOLVED (not FAILED)
  5. Non-TIMEOUT/UNKNOWN status → reconciliation skipped → CASE_C_UNRESOLVED
  6. AuditEvent is added to session in same flush as status update
  7. UNKNOWN status never transitions to FAILED (TIMEOUT ≠ FAILED invariant)

All tests: @pytest.mark.unit, @pytest.mark.asyncio, mocked session + gateway.

Requirements: FR-EXEC-004, FR-EXEC-008, REL-IDEMP-003, REL-FAIL-002, REL-FAIL-003
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from backend.execution_engine.reconciliation import (
    ReconciliationOutcome,
    TimeoutReconciliationFlow,
)
from shared.contracts.enums import VerificationOutcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_external_op(
    status: str = "TIMEOUT",
    action_type: str = "CREATE_PAYMENT_LINK",
    external_operation_id: str | None = "plink_test_001",
) -> MagicMock:
    """Build a lightweight mock ExternalOperation."""
    op = MagicMock()
    op.id = uuid.uuid4()
    op.merchant_id = uuid.uuid4()
    op.case_id = uuid.uuid4()
    op.action_type = action_type
    op.status = status
    op.external_operation_id = external_operation_id
    op.updated_at = None
    op.completed_at = None
    return op


def _make_gateway() -> MagicMock:
    """Build a mock GatewayAbstraction."""
    gw = MagicMock()
    gw.get_payment_link = AsyncMock()
    gw.get_payment = AsyncMock()
    gw.get_subscription = AsyncMock()
    return gw


def _make_session() -> MagicMock:
    """Build a mock AsyncSession."""
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _make_flow(gateway: MagicMock | None = None) -> TimeoutReconciliationFlow:
    """Build a TimeoutReconciliationFlow with a mocked gateway."""
    if gateway is None:
        gateway = _make_gateway()
    return TimeoutReconciliationFlow(gateway=gateway, gateway_timeout_seconds=5.0)


# ---------------------------------------------------------------------------
# Test 1: RECOVERED outcome → status="SUCCEEDED" → CASE_A_SUCCEEDED
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recovered_outcome_sets_succeeded_and_returns_case_a() -> None:
    """Case A: gateway confirms success → external_op.status = SUCCEEDED.

    When VerificationEngine returns RECOVERED, the reconciliation flow must:
    - Set external_op.status to "SUCCEEDED"
    - Set external_op.completed_at to a non-None datetime
    - Return ReconciliationOutcome.CASE_A_SUCCEEDED

    Requirements: FR-EXEC-004, REL-IDEMP-003
    """
    external_op = _make_external_op(status="TIMEOUT")
    session = _make_session()
    flow = _make_flow()

    with patch.object(
        flow._verification_engine,
        "verify",
        new=AsyncMock(return_value=VerificationOutcome.RECOVERED),
    ):
        result = await flow.reconcile(
            session=session,
            external_op=external_op,
            actor="SYSTEM",
            correlation_id=str(uuid.uuid4()),
        )

    assert result.outcome == ReconciliationOutcome.CASE_A_SUCCEEDED
    assert external_op.status == "SUCCEEDED"
    assert external_op.completed_at is not None
    assert external_op.updated_at is not None


# ---------------------------------------------------------------------------
# Test 2: STILL_FAILED outcome → status="FAILED" → CASE_B_FAILED
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_still_failed_outcome_sets_failed_and_returns_case_b() -> None:
    """Case B: gateway confirms failure → external_op.status = FAILED.

    When VerificationEngine returns STILL_FAILED, the reconciliation flow must:
    - Set external_op.status to "FAILED"
    - Return ReconciliationOutcome.CASE_B_FAILED
    - NOT set completed_at (only Case A should)

    Requirements: FR-EXEC-004, REL-FAIL-002
    """
    external_op = _make_external_op(status="TIMEOUT")
    session = _make_session()
    flow = _make_flow()

    with patch.object(
        flow._verification_engine,
        "verify",
        new=AsyncMock(return_value=VerificationOutcome.STILL_FAILED),
    ):
        result = await flow.reconcile(
            session=session,
            external_op=external_op,
            actor="SYSTEM",
            correlation_id=str(uuid.uuid4()),
        )

    assert result.outcome == ReconciliationOutcome.CASE_B_FAILED
    assert external_op.status == "FAILED"
    assert external_op.updated_at is not None


# ---------------------------------------------------------------------------
# Test 3: TIMEOUT outcome → status="UNKNOWN" → CASE_C_UNRESOLVED
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_outcome_sets_unknown_and_returns_case_c() -> None:
    """Case C: gateway still unresolved (TIMEOUT) → status stays UNKNOWN.

    When VerificationEngine returns TIMEOUT, the reconciliation flow must:
    - Set external_op.status to "UNKNOWN" (never to "FAILED")
    - Return ReconciliationOutcome.CASE_C_UNRESOLVED

    This is the core TIMEOUT ≠ FAILED invariant.

    Requirements: REL-IDEMP-003, REL-FAIL-003
    """
    external_op = _make_external_op(status="TIMEOUT")
    session = _make_session()
    flow = _make_flow()

    with patch.object(
        flow._verification_engine,
        "verify",
        new=AsyncMock(return_value=VerificationOutcome.TIMEOUT),
    ):
        result = await flow.reconcile(
            session=session,
            external_op=external_op,
            actor="SYSTEM",
            correlation_id=str(uuid.uuid4()),
        )

    assert result.outcome == ReconciliationOutcome.CASE_C_UNRESOLVED
    # CRITICAL: must never set FAILED for a timeout — always UNKNOWN
    assert external_op.status == "UNKNOWN"
    assert external_op.updated_at is not None


# ---------------------------------------------------------------------------
# Test 4: PARTIAL outcome → status="UNKNOWN" → CASE_C_UNRESOLVED (not FAILED)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_outcome_sets_unknown_and_returns_case_c_not_failed() -> None:
    """Case C: gateway returns PARTIAL (intermediate state) → status stays UNKNOWN.

    PARTIAL indicates an intermediate state (e.g. payment link paid but
    payment not yet captured). The correct response is UNKNOWN, never FAILED.

    Requirements: REL-IDEMP-003, REL-FAIL-002, REL-FAIL-003
    """
    external_op = _make_external_op(status="UNKNOWN")
    session = _make_session()
    flow = _make_flow()

    with patch.object(
        flow._verification_engine,
        "verify",
        new=AsyncMock(return_value=VerificationOutcome.PARTIAL),
    ):
        result = await flow.reconcile(
            session=session,
            external_op=external_op,
            actor="SYSTEM",
            correlation_id=str(uuid.uuid4()),
        )

    assert result.outcome == ReconciliationOutcome.CASE_C_UNRESOLVED
    # PARTIAL must NOT result in FAILED — always UNKNOWN for re-reconciliation
    assert external_op.status == "UNKNOWN"
    assert external_op.status != "FAILED"


# ---------------------------------------------------------------------------
# Test 5: Non-TIMEOUT/UNKNOWN status → reconciliation skipped → CASE_C_UNRESOLVED
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_reconcilable_status_skips_reconciliation() -> None:
    """If status is SUCCEEDED or FAILED, reconciliation is skipped.

    Reconciliation should only run on TIMEOUT, UNKNOWN, or IN_PROGRESS.
    For any other status, the method returns CASE_C_UNRESOLVED immediately
    without calling the gateway.

    Requirements: FR-EXEC-004, FR-EXEC-008
    """
    external_op = _make_external_op(status="SUCCEEDED")
    session = _make_session()
    gateway = _make_gateway()
    flow = _make_flow(gateway)

    with patch.object(
        flow._verification_engine,
        "verify",
        new=AsyncMock(),
    ) as mock_verify:
        result = await flow.reconcile(
            session=session,
            external_op=external_op,
            actor="SYSTEM",
            correlation_id=str(uuid.uuid4()),
        )

    # Should not have queried the gateway at all
    mock_verify.assert_not_called()
    # Status must NOT have been mutated
    assert external_op.status == "SUCCEEDED"
    # Returns CASE_C_UNRESOLVED with a descriptive message
    assert result.outcome == ReconciliationOutcome.CASE_C_UNRESOLVED
    assert "does not require reconciliation" in result.message


# ---------------------------------------------------------------------------
# Test 6: AuditEvent is added to session in same flush as status update
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_audit_event_added_to_session_in_same_flush() -> None:
    """AuditEvent must be session.add()'d before flush — same-transaction atomicity.

    The audit atomicity invariant: AuditEvent is written in the same DB
    transaction as the status update. session.add() must be called with an
    AuditEvent, and session.flush() must be awaited.

    Requirements: REL-FAIL-002, FR-EXEC-004
    """
    from backend.domain.models import AuditEvent

    external_op = _make_external_op(status="TIMEOUT")
    session = _make_session()
    flow = _make_flow()

    with patch.object(
        flow._verification_engine,
        "verify",
        new=AsyncMock(return_value=VerificationOutcome.RECOVERED),
    ):
        await flow.reconcile(
            session=session,
            external_op=external_op,
            actor="SYSTEM",
            correlation_id=str(uuid.uuid4()),
        )

    # session.add() must have been called with an AuditEvent instance
    session.add.assert_called_once()
    added_object = session.add.call_args[0][0]
    assert isinstance(added_object, AuditEvent)
    # session.flush() must have been awaited to materialise the insert
    session.flush.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 7: UNKNOWN status never transitions to FAILED (TIMEOUT ≠ FAILED invariant)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_status_never_transitions_to_failed() -> None:
    """TIMEOUT ≠ FAILED: no unresolved verification outcome may produce FAILED.

    This test explicitly verifies the hard invariant stated throughout the spec:
    a TIMEOUT or UNKNOWN status MUST NEVER result in the external_op.status
    being set to "FAILED". That would be a data-corrupting assumption.

    For every VerificationOutcome that is NOT STILL_FAILED, the resulting
    external_op.status must never be "FAILED".

    Requirements: REL-IDEMP-003, REL-FAIL-003, FR-EXEC-008
    """
    unresolved_outcomes = [VerificationOutcome.TIMEOUT, VerificationOutcome.PARTIAL]

    for verification_outcome in unresolved_outcomes:
        external_op = _make_external_op(status="UNKNOWN")
        session = _make_session()
        flow = _make_flow()

        with patch.object(
            flow._verification_engine,
            "verify",
            new=AsyncMock(return_value=verification_outcome),
        ):
            result = await flow.reconcile(
                session=session,
                external_op=external_op,
                actor="SYSTEM",
                correlation_id=str(uuid.uuid4()),
            )

        # The critical invariant: TIMEOUT never means FAILED
        assert external_op.status != "FAILED", (
            f"Status must never be FAILED for unresolved outcome {verification_outcome}; "
            f"got {external_op.status!r}"
        )
        assert external_op.status == "UNKNOWN", (
            f"Expected UNKNOWN for unresolved outcome {verification_outcome}; "
            f"got {external_op.status!r}"
        )
        assert result.outcome == ReconciliationOutcome.CASE_C_UNRESOLVED
