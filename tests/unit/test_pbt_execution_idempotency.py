"""Property-Based Tests: Execution Idempotency (Property 6) — MANDATORY.

Property 6: Execution Idempotency
  For any action_id that was previously completed with SUCCEEDED status,
  resubmitting the same action returns the blocked/cached result with NO
  new external API call being made.

  The count of external API calls per action_id is at most 1 per logical
  recovery action.

Feature: averiq, Property 6: Execution Idempotency
Requirements: FR-EXEC-005, REL-IDEMP-002

**Validates: Requirements FR-EXEC-005, REL-IDEMP-002**
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.execution_engine.guards import BlockingCondition, CanExecuteAction, GuardResult


# ---------------------------------------------------------------------------
# P6a — Previously SUCCEEDED action_id is blocked by idempotency guard
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@pytest.mark.asyncio
@given(action_id=st.uuids())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow], deadline=None)
async def test_p6_previously_succeeded_action_id_is_blocked(action_id: uuid.UUID) -> None:
    """Feature: averiq, Property 6: Execution Idempotency

    For any action_id that was previously SUCCEEDED, CanExecuteAction.check()
    must return can_execute=False with 'action_id_already_succeeded' in blocking
    conditions. This prevents any new external API call for the same action_id.

    **Validates: Requirements FR-EXEC-005, REL-IDEMP-002**
    """
    # Build a mock case in ACTION_APPROVED state
    case = MagicMock()
    case.id = uuid.uuid4()
    case.merchant_id = uuid.uuid4()
    case.state = "ACTION_APPROVED"

    # Condition 3 check: no executing operation
    executing_result = MagicMock()
    executing_result.scalar_one_or_none.return_value = None

    # Condition 4 check: a previously SUCCEEDED operation exists for this action_id
    succeeded_op = MagicMock()
    succeeded_op.id = uuid.uuid4()
    succeeded_op.action_id = action_id
    succeeded_op.status = "SUCCEEDED"

    succeeded_result = MagicMock()
    succeeded_result.scalar_one_or_none.return_value = succeeded_op

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[executing_result, succeeded_result])

    guard = CanExecuteAction()
    result = await guard.check(
        session=session,
        case=case,
        action_id=action_id,
        intervention_type="CREATE_PAYMENT_LINK",
        actor="SYSTEM",
        correlation_id=str(uuid.uuid4()),
    )

    assert result.can_execute is False, (
        f"Expected can_execute=False for previously SUCCEEDED action_id={action_id}, "
        f"but got can_execute=True"
    )
    condition_names = [bc.condition_name for bc in result.blocking_conditions]
    assert "action_id_already_succeeded" in condition_names, (
        f"Expected 'action_id_already_succeeded' in blocking conditions, got: {condition_names}"
    )


# ---------------------------------------------------------------------------
# P6b — Fresh action_id (not previously SUCCEEDED) is not blocked by idempotency guard
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@pytest.mark.asyncio
@given(action_id=st.uuids())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow], deadline=None)
async def test_p6_fresh_action_id_is_not_blocked_by_idempotency_guard(
    action_id: uuid.UUID,
) -> None:
    """Feature: averiq, Property 6: Execution Idempotency

    A fresh action_id not previously seen should not trigger the idempotency
    guard. The 'action_id_already_succeeded' condition must NOT appear in
    blocking_conditions when no prior SUCCEEDED operation exists.

    **Validates: Requirements FR-EXEC-005, REL-IDEMP-002**
    """
    case = MagicMock()
    case.id = uuid.uuid4()
    case.merchant_id = uuid.uuid4()
    case.state = "ACTION_APPROVED"

    # No existing executing or succeeded operations for this action_id
    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[no_result, no_result])

    guard = CanExecuteAction()
    result = await guard.check(
        session=session,
        case=case,
        action_id=action_id,
        intervention_type="CREATE_PAYMENT_LINK",
        actor="SYSTEM",
        correlation_id=str(uuid.uuid4()),
    )

    # The idempotency guard must NOT fire for a fresh action_id
    condition_names = [bc.condition_name for bc in result.blocking_conditions]
    assert "action_id_already_succeeded" not in condition_names, (
        f"Idempotency guard should NOT fire for fresh action_id={action_id}, "
        f"but found 'action_id_already_succeeded' in: {condition_names}"
    )


# ---------------------------------------------------------------------------
# P6c — evaluated_values always records the idempotency check result
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@pytest.mark.asyncio
@given(action_id=st.uuids(), was_succeeded=st.booleans())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow], deadline=None)
async def test_p6_evaluated_values_records_idempotency_check(
    action_id: uuid.UUID,
    was_succeeded: bool,
) -> None:
    """Feature: averiq, Property 6: Execution Idempotency

    evaluated_values must always record 'previously_succeeded' for the audit
    trail, regardless of whether the action_id was previously SUCCEEDED or not.
    This is required by FR-EXEC-003 — the AuditEvent must contain all evaluated
    pre-condition values.

    **Validates: Requirements FR-EXEC-005, REL-IDEMP-002**
    """
    case = MagicMock()
    case.id = uuid.uuid4()
    case.merchant_id = uuid.uuid4()
    case.state = "ACTION_APPROVED"

    # Condition 3: no executing operation
    executing_result = MagicMock()
    executing_result.scalar_one_or_none.return_value = None

    # Condition 4: conditionally return a SUCCEEDED op or None
    succeeded_op: MagicMock | None = None
    if was_succeeded:
        succeeded_op = MagicMock()
        succeeded_op.id = uuid.uuid4()
        succeeded_op.action_id = action_id
        succeeded_op.status = "SUCCEEDED"

    succeeded_result = MagicMock()
    succeeded_result.scalar_one_or_none.return_value = succeeded_op

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[executing_result, succeeded_result])

    guard = CanExecuteAction()
    result = await guard.check(
        session=session,
        case=case,
        action_id=action_id,
        intervention_type="CREATE_PAYMENT_LINK",
        actor="SYSTEM",
        correlation_id=str(uuid.uuid4()),
    )

    # evaluated_values must always contain 'previously_succeeded' for the audit trail
    assert "previously_succeeded" in result.evaluated_values, (
        "evaluated_values must record 'previously_succeeded' for the audit trail "
        f"(action_id={action_id}, was_succeeded={was_succeeded})"
    )
    assert result.evaluated_values["previously_succeeded"] == was_succeeded, (
        f"evaluated_values['previously_succeeded'] should be {was_succeeded}, "
        f"got {result.evaluated_values['previously_succeeded']!r}"
    )


# ---------------------------------------------------------------------------
# P6d — TIMEOUT status does NOT block via idempotency guard (only SUCCEEDED does)
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@pytest.mark.asyncio
@given(action_id=st.uuids())
@settings(max_examples=100, deadline=None)
async def test_p6_timeout_status_does_not_trigger_idempotency_block(
    action_id: uuid.UUID,
) -> None:
    """Feature: averiq, Property 6: Execution Idempotency

    A TIMEOUT operation must NOT block via the 'action_id_already_succeeded'
    guard. TIMEOUT != SUCCEEDED. The idempotency guard only blocks SUCCEEDED
    actions. TIMEOUT requires reconciliation — not a repeat submission block.

    The guard queries WHERE status='SUCCEEDED'; a TIMEOUT operation will not
    match that query, so no SUCCEEDED record will be found.

    **Validates: Requirements FR-EXEC-005, REL-IDEMP-002**
    """
    case = MagicMock()
    case.id = uuid.uuid4()
    case.merchant_id = uuid.uuid4()
    case.state = "ACTION_APPROVED"

    # Condition 3: no executing operation
    executing_result = MagicMock()
    executing_result.scalar_one_or_none.return_value = None

    # Condition 4: the guard queries WHERE status='SUCCEEDED'; a TIMEOUT operation
    # does NOT match — so the DB returns None for the SUCCEEDED query.
    no_succeeded = MagicMock()
    no_succeeded.scalar_one_or_none.return_value = None  # TIMEOUT != SUCCEEDED

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[executing_result, no_succeeded])

    guard = CanExecuteAction()
    result = await guard.check(
        session=session,
        case=case,
        action_id=action_id,
        intervention_type="CREATE_PAYMENT_LINK",
        actor="SYSTEM",
        correlation_id=str(uuid.uuid4()),
    )

    # TIMEOUT must NOT trigger the idempotency block
    condition_names = [bc.condition_name for bc in result.blocking_conditions]
    assert "action_id_already_succeeded" not in condition_names, (
        f"TIMEOUT status must NOT trigger 'action_id_already_succeeded' guard "
        f"for action_id={action_id}, but found it in: {condition_names}"
    )

    # evaluated_values must confirm previously_succeeded=False
    assert result.evaluated_values.get("previously_succeeded") is False, (
        f"evaluated_values['previously_succeeded'] should be False for TIMEOUT, "
        f"got: {result.evaluated_values.get('previously_succeeded')!r}"
    )
