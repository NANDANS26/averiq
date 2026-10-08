"""Execution Engine ?" executes recovery interventions via GatewayAbstraction.

Enforces execution guards, creates idempotent ExternalOperation records,
triggers external gateway actions (Payment Link, etc.), and transitions
case state from ACTION_APPROVED -> ACTION_EXECUTING -> VERIFYING.

Requirements: FR-EXEC-001, FR-EXEC-002, FR-EXEC-003, FR-EXEC-005, REL-IDEMP-002
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.models import AuditEvent, Customer, ExternalOperation, Intervention, RevenueRiskCase
from backend.execution_engine.guards import CanExecuteAction, GuardResult
from backend.gateway.abstract import GatewayAbstraction
from backend.state_machine.machine import StateMachine
from backend.worker import celery_app

logger = structlog.get_logger(__name__)


class ExecutionEngine:
    """Executes approved interventions against payment gateways with strict idempotency."""

    def __init__(
        self,
        gateway: GatewayAbstraction,
        state_machine: StateMachine | None = None,
        guard: CanExecuteAction | None = None,
    ) -> None:
        self.gateway = gateway
        self.state_machine = state_machine or StateMachine()
        self.guard = guard or CanExecuteAction()

    async def execute(
        self,
        session: AsyncSession,
        case_id: uuid.UUID,
        intervention_id: uuid.UUID,
        merchant_id: uuid.UUID,
        actor: str = "SYSTEM",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute an approved intervention atomically.

        Guards:
          1. CanExecuteAction pre-condition checks
          2. Generates action_id for primary idempotency
          3. Creates ExternalOperation with REQUESTED / IN_PROGRESS
          4. Transitions case to ACTION_EXECUTING
          5. Calls gateway to create payment link
          6. Updates ExternalOperation to SUCCEEDED / FAILED / TIMEOUT
          7. If SUCCEEDED: transitions case to VERIFYING and enqueues verification task
        """
        corr_id = correlation_id or str(uuid.uuid4())
        log = logger.bind(
            case_id=str(case_id),
            intervention_id=str(intervention_id),
            merchant_id=str(merchant_id),
            correlation_id=corr_id,
        )

        # 1. Load case and intervention
        case_stmt = (
            select(RevenueRiskCase)
            .where(
                RevenueRiskCase.id == case_id,
                RevenueRiskCase.merchant_id == merchant_id,
            )
            .with_for_update()
        )
        case_res = await session.execute(case_stmt)
        case = case_res.scalar_one_or_none()
        if not case:
            log.error("execution_case_not_found")
            return {"status": "case_not_found"}

        intv_stmt = (
            select(Intervention)
            .where(
                Intervention.id == intervention_id,
                Intervention.merchant_id == merchant_id,
            )
            .with_for_update()
        )
        intv_res = await session.execute(intv_stmt)
        intervention = intv_res.scalar_one_or_none()
        if not intervention:
            log.error("execution_intervention_not_found")
            return {"status": "intervention_not_found"}

        # 2. Assign action_id for idempotency
        action_id = uuid.uuid4()

        # 3. Pre-condition check via CanExecuteAction
        guard_result: GuardResult = await self.guard.check(
            session=session,
            case=case,
            action_id=action_id,
            intervention_type=intervention.intervention_type,
            actor=actor,
            correlation_id=corr_id,
        )

        if not guard_result.can_execute:
            log.warning("execution_guard_blocked", blocking=guard_result.blocking_conditions)
            # Record audit event for blocked execution
            audit = AuditEvent(
                merchant_id=merchant_id,
                case_id=case.id,
                actor=actor,
                action_type="EXECUTION_BLOCKED_BY_GUARD",
                affected_entity_type="RevenueRiskCase",
                affected_entity_id=case.id,
                from_state=case.state,
                to_state=case.state,
                correlation_id=uuid.UUID(corr_id),
                payload={
                    "blocking_conditions": [
                        {"name": c.condition_name, "detail": c.detail, "val": str(c.evaluated_value)}
                        for c in guard_result.blocking_conditions
                    ]
                },
                created_at=datetime.now(timezone.utc),
            )
            session.add(audit)
            return {"status": "blocked", "conditions": [c.condition_name for c in guard_result.blocking_conditions]}

        now = datetime.now(timezone.utc)

        # 4. Create ExternalOperation record
        action_type = "CREATE_PAYMENT_LINK" if intervention.intervention_type == "PAYMENT_LINK" else intervention.intervention_type
        ext_op = ExternalOperation(
            action_id=action_id,
            case_id=case.id,
            merchant_id=merchant_id,
            intervention_id=intervention.id,
            action_type=action_type,
            status="IN_PROGRESS",
            submitted_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(ext_op)
        intervention.execution_status = "EXECUTING"
        await session.flush()

        # 5. Transition case ACTION_APPROVED -> ACTION_EXECUTING
        await self.state_machine.transition(
            session,
            case_id=case.id,
            to_state="ACTION_EXECUTING",
            actor=actor,
            reason=f"Executing intervention {intervention.intervention_type}",
            correlation_id=corr_id,
        )

        # 6. Call gateway (handling timeout & exceptions)
        customer_email = None
        if case.customer_id:
            cust_res = await session.execute(select(Customer).where(Customer.id == case.customer_id))
            cust = cust_res.scalar_one_or_none()
            if cust:
                customer_email = cust.email

        plink_result = None
        try:
            plink_result = await self.gateway.create_payment_link(
                amount_paise=int(case.payment_amount_at_risk),
                currency=case.currency,
                description=f"Averiq recovery for payment {case.payment_id}",
                customer_email=customer_email,
                reference_id=str(case.id),
            )
        except asyncio.TimeoutError:
            log.warning("gateway_create_payment_link_timeout")
            ext_op.status = "TIMEOUT"
            ext_op.timeout_at = datetime.now(timezone.utc)
            intervention.execution_status = "TIMEOUT"
            return {"status": "timeout", "action_id": str(action_id)}
        except Exception as ex:
            log.error("gateway_create_payment_link_error", error=str(ex))
            ext_op.status = "FAILED"
            ext_op.error_message = str(ex)
            intervention.execution_status = "FAILED"
            return {"status": "failed", "error": str(ex)}

        # 7. Gateway succeeded
        ext_op.status = "SUCCEEDED"
        ext_op.external_operation_id = plink_result.get("id")
        ext_op.completed_at = datetime.now(timezone.utc)
        ext_op.response_payload = plink_result
        intervention.execution_status = "SUCCEEDED"

        # 8. Transition case ACTION_EXECUTING -> VERIFYING
        await self.state_machine.transition(
            session,
            case_id=case.id,
            to_state="VERIFYING",
            actor=actor,
            reason="External operation completed successfully; entering verification",
            correlation_id=corr_id,
        )

        # 9. Enqueue verification task
        celery_app.send_task(
            "backend.verification_engine.tasks.run_verification",
            args=[str(ext_op.id), str(case.id), 1],
            queue="verification",
            headers={"correlation_id": corr_id},
        )

        return {
            "status": "succeeded",
            "action_id": str(action_id),
            "external_operation_id": ext_op.external_operation_id,
        }