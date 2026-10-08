"""Unit tests for CanExecuteAction pre-condition guard.

Tests cover all 4 pre-condition checks defined by FR-EXEC-002:
  1. case.state == "ACTION_APPROVED"
  2. payment not already recovered (case.state != "RECOVERED")
  3. No identical (case_id, intervention_type) in ACTION_EXECUTING status
  4. action_id not previously SUCCEEDED

All tests use mocked AsyncSession — no database required.
All tests are marked @pytest.mark.unit and @pytest.mark.asyncio.

Requirements: FR-EXEC-002, FR-EXEC-003
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.execution_engine.guards import (
    BlockingCondition,
    CanExecuteAction,
    GuardResult,
)


# ---------------------------------------------------------------------------
# Helpers — build lightweight mock objects
# ---------------------------------------------------------------------------


def _make_case(state: str = "ACTION_APPROVED") -> MagicMock:
    """Return a mock ``RevenueRiskCase`` with the given state."""
    case = MagicMock()
    case.id = uuid.uuid4()
    case.merchant_id = uuid.uuid4()
    case.state = state
    return case


def _make_external_operation(
    case_id: uuid.UUID | None = None,
    action_id: uuid.UUID | None = None,
    action_type: str = "PAYMENT_LINK",
    status: str = "IN_PROGRESS",
) -> MagicMock:
    """Return a mock ``ExternalOperation``."""
    op = MagicMock()
    op.id = uuid.uuid4()
    op.case_id = case_id or uuid.uuid4()
    op.action_id = action_id or uuid.uuid4()
    op.action_type = action_type
    op.status = status
    return op


def _make_session(
    executing_op: Any = None,
    succeeded_op: Any = None,
) -> AsyncMock:
    """Return a mock ``AsyncSession`` that returns the given operations.

    The mock patches ``session.execute`` so that:
      - The first execute call (Condition 3 check) returns ``executing_op``
      - The second execute call (Condition 4 check) returns ``succeeded_op``

    Both calls go through ``scalar_one_or_none()``.
    """
    session = AsyncMock()

    # Build mock result objects whose scalar_one_or_none() returns the right op
    executing_result = MagicMock()
    executing_result.scalar_one_or_none.return_value = executing_op

    succeeded_result = MagicMock()
    succeeded_result.scalar_one_or_none.return_value = succeeded_op

    # session.execute is async; make it an AsyncMock that alternates results
    session.execute = AsyncMock(
        side_effect=[executing_result, succeeded_result]
    )
    return session


# ---------------------------------------------------------------------------
# Test 1: Returns can_execute=True when all 4 conditions pass
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_all_conditions_pass_returns_can_execute_true() -> None:
    """Guard returns can_execute=True when all 4 pre-conditions are satisfied.

    Conditions:
      1. case.state == "ACTION_APPROVED"  ✓
      2. case.state != "RECOVERED"         ✓
      3. No duplicate ACTION_EXECUTING op   ✓ (executing_op=None)
      4. action_id not previously SUCCEEDED ✓ (succeeded_op=None)

    Requirements: FR-EXEC-002
    """
    guard = CanExecuteAction()
    case = _make_case(state="ACTION_APPROVED")
    action_id = uuid.uuid4()
    session = _make_session(executing_op=None, succeeded_op=None)

    result = await guard.check(
        session=session,
        case=case,
        action_id=action_id,
        intervention_type="PAYMENT_LINK",
        actor="SYSTEM",
        correlation_id=str(uuid.uuid4()),
    )

    assert result.can_execute is True
    assert result.blocking_conditions == []


# ---------------------------------------------------------------------------
# Test 2: Blocks when case.state != "ACTION_APPROVED"
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocks_when_case_not_action_approved() -> None:
    """Guard blocks execution when case is not in ACTION_APPROVED state.

    Condition 1 fails: case.state is "ACTION_PENDING", not "ACTION_APPROVED".
    Blocking condition: "case_not_action_approved".

    Requirements: FR-EXEC-002, FR-EXEC-003
    """
    guard = CanExecuteAction()
    case = _make_case(state="ACTION_PENDING")
    action_id = uuid.uuid4()
    session = _make_session(executing_op=None, succeeded_op=None)

    result = await guard.check(
        session=session,
        case=case,
        action_id=action_id,
        intervention_type="PAYMENT_LINK",
        actor="SYSTEM",
        correlation_id=str(uuid.uuid4()),
    )

    assert result.can_execute is False
    condition_names = [bc.condition_name for bc in result.blocking_conditions]
    assert "case_not_action_approved" in condition_names


# ---------------------------------------------------------------------------
# Test 3: Blocks when case.state == "RECOVERED"
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocks_when_payment_already_recovered() -> None:
    """Guard blocks when case is already in RECOVERED terminal state.

    Condition 2 fails: case.state == "RECOVERED".
    Blocking condition: "payment_already_recovered".

    Note: Condition 1 also fires because RECOVERED != ACTION_APPROVED.
    Both blocking conditions are expected in the result.

    Requirements: FR-EXEC-002, FR-EXEC-003
    """
    guard = CanExecuteAction()
    case = _make_case(state="RECOVERED")
    action_id = uuid.uuid4()
    session = _make_session(executing_op=None, succeeded_op=None)

    result = await guard.check(
        session=session,
        case=case,
        action_id=action_id,
        intervention_type="PAYMENT_LINK",
        actor="SYSTEM",
        correlation_id=str(uuid.uuid4()),
    )

    assert result.can_execute is False
    condition_names = [bc.condition_name for bc in result.blocking_conditions]
    assert "payment_already_recovered" in condition_names


# ---------------------------------------------------------------------------
# Test 4: Blocks when duplicate ACTION_EXECUTING exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocks_when_duplicate_action_executing() -> None:
    """Guard blocks when an identical (case_id, intervention_type) is IN_PROGRESS.

    Condition 3 fails: an ExternalOperation with the same case_id and
    intervention_type is already in "IN_PROGRESS" status.
    Blocking condition: "duplicate_action_executing".

    Requirements: FR-EXEC-002, FR-EXEC-003
    """
    guard = CanExecuteAction()
    case = _make_case(state="ACTION_APPROVED")
    action_id = uuid.uuid4()

    # An existing IN_PROGRESS operation with the same case_id and type
    existing_op = _make_external_operation(
        case_id=case.id,
        action_type="PAYMENT_LINK",
        status="IN_PROGRESS",
    )
    session = _make_session(executing_op=existing_op, succeeded_op=None)

    result = await guard.check(
        session=session,
        case=case,
        action_id=action_id,
        intervention_type="PAYMENT_LINK",
        actor="SYSTEM",
        correlation_id=str(uuid.uuid4()),
    )

    assert result.can_execute is False
    condition_names = [bc.condition_name for bc in result.blocking_conditions]
    assert "duplicate_action_executing" in condition_names


# ---------------------------------------------------------------------------
# Test 5: Blocks when action_id previously SUCCEEDED
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocks_when_action_id_already_succeeded() -> None:
    """Guard blocks when the given action_id has already SUCCEEDED.

    Condition 4 fails: an ExternalOperation with the same action_id already
    has status "SUCCEEDED".
    Blocking condition: "action_id_already_succeeded".

    Requirements: FR-EXEC-002, FR-EXEC-003 (idempotency guard)
    """
    guard = CanExecuteAction()
    case = _make_case(state="ACTION_APPROVED")
    action_id = uuid.uuid4()

    # A previously SUCCEEDED operation with this action_id
    prev_op = _make_external_operation(
        action_id=action_id,
        status="SUCCEEDED",
    )
    session = _make_session(executing_op=None, succeeded_op=prev_op)

    result = await guard.check(
        session=session,
        case=case,
        action_id=action_id,
        intervention_type="PAYMENT_LINK",
        actor="SYSTEM",
        correlation_id=str(uuid.uuid4()),
    )

    assert result.can_execute is False
    condition_names = [bc.condition_name for bc in result.blocking_conditions]
    assert "action_id_already_succeeded" in condition_names


# ---------------------------------------------------------------------------
# Test 6: evaluated_values contains required keys
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_evaluated_values_contains_required_keys() -> None:
    """GuardResult.evaluated_values contains case_state, action_id, intervention_type.

    FR-EXEC-003 requires that all evaluated pre-condition values are recorded
    in the AuditEvent. The guard must always populate evaluated_values with
    the core context fields regardless of whether execution is blocked.

    Requirements: FR-EXEC-002, FR-EXEC-003
    """
    guard = CanExecuteAction()
    case = _make_case(state="ACTION_APPROVED")
    action_id = uuid.uuid4()
    session = _make_session(executing_op=None, succeeded_op=None)

    result = await guard.check(
        session=session,
        case=case,
        action_id=action_id,
        intervention_type="CUSTOMER_ACTION",
        actor="SYSTEM",
        correlation_id=str(uuid.uuid4()),
    )

    assert "case_state" in result.evaluated_values
    assert "action_id" in result.evaluated_values
    assert "intervention_type" in result.evaluated_values
    # Verify actual values are correctly captured
    assert result.evaluated_values["case_state"] == "ACTION_APPROVED"
    assert result.evaluated_values["action_id"] == str(action_id)
    assert result.evaluated_values["intervention_type"] == "CUSTOMER_ACTION"


# ---------------------------------------------------------------------------
# Test 7: Multiple blocking conditions returned simultaneously
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_multiple_blocking_conditions_returned_simultaneously() -> None:
    """All blocking conditions are collected and returned together.

    When multiple pre-conditions fail at once, all of them must appear in
    GuardResult.blocking_conditions. The guard evaluates all 4 checks
    regardless of earlier failures — it does not short-circuit.

    Scenario: case is RECOVERED (fails conditions 1 and 2), and action_id
    was already SUCCEEDED (fails condition 4). All three conditions should
    be present in the result.

    Requirements: FR-EXEC-002, FR-EXEC-003
    """
    guard = CanExecuteAction()
    case = _make_case(state="RECOVERED")  # fails conditions 1 AND 2
    action_id = uuid.uuid4()

    # action_id also previously SUCCEEDED (fails condition 4)
    prev_op = _make_external_operation(
        action_id=action_id,
        status="SUCCEEDED",
    )
    session = _make_session(executing_op=None, succeeded_op=prev_op)

    result = await guard.check(
        session=session,
        case=case,
        action_id=action_id,
        intervention_type="PAYMENT_LINK",
        actor="SYSTEM",
        correlation_id=str(uuid.uuid4()),
    )

    assert result.can_execute is False
    condition_names = [bc.condition_name for bc in result.blocking_conditions]

    # Condition 1: RECOVERED != ACTION_APPROVED
    assert "case_not_action_approved" in condition_names
    # Condition 2: case is RECOVERED
    assert "payment_already_recovered" in condition_names
    # Condition 4: action_id was already SUCCEEDED
    assert "action_id_already_succeeded" in condition_names
    # Total: at least 3 blocking conditions
    assert len(result.blocking_conditions) >= 3
