"""CanExecuteAction — pre-condition guard for execution engine.

Checks all 4 pre-conditions before any external gateway call is made:
  1. Case in ACTION_APPROVED state
  2. Payment not already recovered (case in RECOVERED terminal state)
  3. No identical (case_id, intervention_type) in ACTION_EXECUTING status
  4. action_id not previously SUCCEEDED

On any blocking condition:
  - Record GuardResult with all evaluated conditions
  - Insert AuditEvent (same DB transaction — atomicity invariant)
  - Transition case back to ACTION_PENDING via StateMachine

The guard is a hard gate before execution. No external gateway call may
proceed if the guard returns can_execute=False.

Requirements: FR-EXEC-002, FR-EXEC-003
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.models import AuditEvent, ExternalOperation, RevenueRiskCase

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class BlockingCondition:
    """One pre-condition that blocked execution."""

    condition_name: str
    detail: str
    evaluated_value: Any = None


@dataclass(frozen=True)
class GuardResult:
    """The outcome of running all CanExecuteAction pre-condition checks.

    ``can_execute=True`` means all 4 checks passed; the caller may proceed
    to submit the external gateway request.

    ``can_execute=False`` means one or more checks failed.  All blocking
    conditions are listed in ``blocking_conditions`` and the full set of
    evaluated values is in ``evaluated_values`` — both are required by
    FR-EXEC-003 for recording in the AuditEvent.
    """

    can_execute: bool
    blocking_conditions: list[BlockingCondition] = field(default_factory=list)
    evaluated_values: dict[str, Any] = field(default_factory=dict)


class CanExecuteAction:
    """Pre-condition guard: evaluates all 4 checks before any gateway call.

    The guard is intentionally stateless — every call is self-contained and
    deterministic given the same database state.  This makes it safe to
    instantiate once and reuse across concurrent Celery workers.

    Pre-conditions (FR-EXEC-002):
      1. case.state == "ACTION_APPROVED"
      2. case.state != "RECOVERED" (payment not already recovered)
      3. No ExternalOperation with (case_id, intervention_type, status=IN_PROGRESS)
         — prevents duplicate concurrent executions (maps to ACTION_EXECUTING guard)
      4. action_id not previously SUCCEEDED

    All four checks are always evaluated and their outcomes recorded in
    ``GuardResult.evaluated_values`` regardless of whether any earlier check
    failed.  This ensures the AuditEvent always contains the full picture.

    Caller responsibilities (FR-EXEC-003):
      When ``can_execute=False``:
        - Record the GuardResult + all evaluated values in an AuditEvent
          (in the same DB transaction as the state change)
        - Transition the case to ACTION_PENDING via StateMachine
    """

    async def check(
        self,
        session: AsyncSession,
        case: RevenueRiskCase,
        action_id: uuid.UUID,
        intervention_type: str,
        actor: str,
        correlation_id: str,
    ) -> GuardResult:
        """Run all 4 pre-condition checks.

        Conditions checked (all must pass for can_execute=True):
          1. case.state == "ACTION_APPROVED"
          2. case.state != "RECOVERED"
          3. No existing ExternalOperation with same (case_id, intervention_type)
             in "IN_PROGRESS" status (maps to the ACTION_EXECUTING guard)
          4. action_id not previously SUCCEEDED

        Returns GuardResult. Does NOT write AuditEvent here — caller must
        record the audit in the same transaction as the state change, per the
        audit atomicity invariant.

        Parameters
        ----------
        session:
            An open ``AsyncSession``.  The method is read-only (no writes);
            it can be called inside or outside an active transaction.
        case:
            The ``RevenueRiskCase`` ORM object to evaluate.
        action_id:
            The UUID assigned to the action that is about to be executed.
            Used for Condition 4 (not previously SUCCEEDED).
        intervention_type:
            The ``intervention_type`` string (e.g. "PAYMENT_LINK") used for
            Condition 3 (duplicate ACTION_EXECUTING check).
        actor:
            The actor requesting execution ("SYSTEM" or a user ID).  Recorded
            in the evaluated_values for audit trail purposes.
        correlation_id:
            The end-to-end correlation identifier.  Recorded in evaluated_values.

        Returns
        -------
        GuardResult
            ``can_execute=True`` if all 4 conditions pass.
            ``can_execute=False`` with blocking_conditions populated if any fail.
        """
        blocking: list[BlockingCondition] = []
        evaluated: dict[str, Any] = {
            "case_id": str(case.id),
            "case_state": case.state,
            "action_id": str(action_id),
            "intervention_type": intervention_type,
            "actor": actor,
            "correlation_id": correlation_id,
        }

        # ------------------------------------------------------------------
        # Condition 1: case must be in ACTION_APPROVED state
        # FR-EXEC-002: "the case is in ACTION_APPROVED state"
        # ------------------------------------------------------------------
        if case.state != "ACTION_APPROVED":
            blocking.append(
                BlockingCondition(
                    condition_name="case_not_action_approved",
                    detail=(
                        f"Case is in state '{case.state}', expected 'ACTION_APPROVED'"
                    ),
                    evaluated_value=case.state,
                )
            )

        # ------------------------------------------------------------------
        # Condition 2: payment not already recovered
        # FR-EXEC-002: "the payment has not already been recovered"
        # Note: RECOVERED is a terminal state; once reached no execution is safe.
        # ------------------------------------------------------------------
        if case.state == "RECOVERED":
            blocking.append(
                BlockingCondition(
                    condition_name="payment_already_recovered",
                    detail="Case is already in RECOVERED state",
                    evaluated_value=case.state,
                )
            )

        # ------------------------------------------------------------------
        # Condition 3: no identical (case_id, intervention_type) in ACTION_EXECUTING
        # FR-EXEC-002: "no identical action (same case_id, same intervention_type)
        # is already in ACTION_EXECUTING state"
        #
        # The ExternalOperation.status column uses "IN_PROGRESS" at the DB layer
        # to represent the logical ACTION_EXECUTING guard described in the spec.
        # An ExternalOperation with status IN_PROGRESS means the gateway call has
        # been submitted and we are awaiting a response — i.e. the case is
        # logically ACTION_EXECUTING.
        # ------------------------------------------------------------------
        executing_stmt = (
            select(ExternalOperation)
            .where(
                ExternalOperation.case_id == case.id,
                ExternalOperation.action_type == intervention_type,
                ExternalOperation.status == "IN_PROGRESS",
            )
            .limit(1)
        )
        executing_result = await session.execute(executing_stmt)
        executing_op = executing_result.scalar_one_or_none()
        evaluated["existing_executing_operation_id"] = (
            str(executing_op.id) if executing_op is not None else None
        )

        if executing_op is not None:
            blocking.append(
                BlockingCondition(
                    condition_name="duplicate_action_executing",
                    detail=(
                        "Identical (case_id, intervention_type) is already "
                        "ACTION_EXECUTING"
                    ),
                    evaluated_value=str(executing_op.id),
                )
            )

        # ------------------------------------------------------------------
        # Condition 4: action_id not previously SUCCEEDED
        # FR-EXEC-002: "the action_id has not previously completed with SUCCEEDED"
        # This is the idempotency guard: if the action already SUCCEEDED once,
        # re-executing would be a duplicate financial action.
        # ------------------------------------------------------------------
        succeeded_stmt = (
            select(ExternalOperation)
            .where(
                ExternalOperation.action_id == action_id,
                ExternalOperation.status == "SUCCEEDED",
            )
            .limit(1)
        )
        succeeded_result = await session.execute(succeeded_stmt)
        succeeded_op = succeeded_result.scalar_one_or_none()
        evaluated["previously_succeeded"] = succeeded_op is not None

        if succeeded_op is not None:
            blocking.append(
                BlockingCondition(
                    condition_name="action_id_already_succeeded",
                    detail=f"action_id {action_id} was already SUCCEEDED",
                    evaluated_value=str(action_id),
                )
            )

        can_execute = len(blocking) == 0

        if not can_execute:
            logger.info(
                "execution_guard.blocked",
                case_id=str(case.id),
                action_id=str(action_id),
                intervention_type=intervention_type,
                blocking_conditions=[bc.condition_name for bc in blocking],
                actor=actor,
            )
        else:
            logger.debug(
                "execution_guard.passed",
                case_id=str(case.id),
                action_id=str(action_id),
                intervention_type=intervention_type,
                actor=actor,
            )

        return GuardResult(
            can_execute=can_execute,
            blocking_conditions=blocking,
            evaluated_values=evaluated,
        )


def _utcnow() -> datetime:
    """Return the current UTC datetime as a timezone-aware value."""
    return datetime.now(tz=timezone.utc)
