"""Verification engine Celery tasks.

Full implementation connecting VerificationEngine, VerificationScheduler,
and AttributionEngine to confirm payment outcomes and compute attribution.

Task name: backend.verification_engine.tasks.run_verification
Queue: verification
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import func, select

from backend.attribution_engine.engine import AttributionEngine
from backend.config import settings
from backend.database import async_session_factory
from backend.domain.models import ExternalOperation, RevenueRiskCase, VerificationAttempt
from backend.gateway.fake_razorpay import FakeRazorpayGateway
from backend.gateway.razorpay_test import RazorpayTestGateway
from backend.state_machine.machine import StateMachine
from backend.verification_engine.engine import VerificationEngine
from backend.verification_engine.scheduler import VerificationScheduler
from backend.worker import celery_app
from shared.contracts.enums import VerificationOutcome

logger = structlog.get_logger(__name__)


def _get_gateway():
    if settings.OPERATING_MODE == "REAL_TEST_MODE":
        return RazorpayTestGateway()
    return FakeRazorpayGateway()


@celery_app.task(
    name="backend.verification_engine.tasks.run_verification",
    bind=True,
    max_retries=0,
    queue="verification",
    acks_late=True,
)
def run_verification(
    self: object,
    external_operation_id: str,
    case_id: str,
    attempt_number: int,
) -> dict[str, Any]:
    """Run a single re-verification attempt asynchronously."""
    return asyncio.run(_run_async(external_operation_id, case_id, attempt_number))


async def _run_async(
    ext_op_id_str: str,
    case_id_str: str,
    attempt: int,
) -> dict[str, Any]:
    ext_op_id = uuid.UUID(ext_op_id_str)
    case_id = uuid.UUID(case_id_str)
    correlation_id = str(uuid.uuid4())

    log = logger.bind(
        external_operation_id=str(ext_op_id),
        case_id=str(case_id),
        attempt=attempt,
        correlation_id=correlation_id,
    )
    log.info("verification_task_started")

    gateway = _get_gateway()
    engine = VerificationEngine(gateway=gateway)
    scheduler = VerificationScheduler()
    state_machine = StateMachine()
    attribution_engine = AttributionEngine()

    async with async_session_factory() as session:
        async with session.begin():
            # 1. Load ExternalOperation and Case with lock
            op_stmt = (
                select(ExternalOperation)
                .where(ExternalOperation.id == ext_op_id)
                .with_for_update()
            )
            op_res = await session.execute(op_stmt)
            ext_op = op_res.scalar_one_or_none()
            if not ext_op:
                log.error("verification_op_not_found")
                return {"status": "op_not_found"}

            case_stmt = (
                select(RevenueRiskCase)
                .where(RevenueRiskCase.id == case_id)
                .with_for_update()
            )
            case_res = await session.execute(case_stmt)
            case = case_res.scalar_one_or_none()
            if not case:
                log.error("verification_case_not_found")
                return {"status": "case_not_found"}

            # 2. Query gateway via VerificationEngine
            gateway_ref_id = ext_op.external_operation_id or str(ext_op.id)
            verification_outcome: VerificationOutcome = await engine.verify(
                action_type=ext_op.action_type,
                external_operation_id=gateway_ref_id,
            )

            log.info("verification_outcome_received", outcome=verification_outcome.value)

            # 3. Record VerificationAttempt
            count_stmt = select(func.count(VerificationAttempt.id)).where(
                VerificationAttempt.external_operation_id == ext_op.id
            )
            count_res = await session.execute(count_stmt)
            prior_attempts = count_res.scalar() or 0
            current_attempt_num = prior_attempts + 1

            await scheduler.record_attempt(
                session=session,
                external_operation_id=ext_op.id,
                attempt_number=current_attempt_num,
                outcome=verification_outcome.value,
                gateway_status="paid" if verification_outcome == VerificationOutcome.RECOVERED else "created",
                verification_strategy="PaymentLinkReconciliationStrategy",
                scheduled_at=datetime.now(timezone.utc),
            )

            # 4. Handle outcome
            now = datetime.now(timezone.utc)
            if verification_outcome == VerificationOutcome.RECOVERED:
                ext_op.status = "VERIFIED"
                ext_op.reconciled_at = now

                # Transition case VERIFYING -> RECOVERED
                await state_machine.transition(
                    session,
                    case_id=case.id,
                    to_state="RECOVERED",
                    actor="VERIFICATION_ENGINE",
                    reason="Gateway confirmed payment captured",
                    correlation_id=correlation_id,
                )

                # Run attribution
                attr_res = await attribution_engine.classify(
                    session=session,
                    case_id=case.id,
                    merchant_id=case.merchant_id,
                    recovered_at=now,
                )
                case.attribution_class = attr_res.attribution_class.value
                case.recovered_amount = case.payment_amount_at_risk
                case.recovered_currency = case.currency

                return {
                    "status": "recovered",
                    "attempt": attempt,
                    "attribution_class": case.attribution_class,
                }

            # Still unresolved
            should_esc = await scheduler.should_escalate(session, ext_op.id) if hasattr(scheduler, "should_escalate") else (attempt >= 3)
            if should_esc:
                log.warning("verification_attempts_exhausted_escalating")
                await state_machine.transition(
                    session,
                    case_id=case.id,
                    to_state="ESCALATED",
                    actor="VERIFICATION_ENGINE",
                    reason=f"Exhausted verification attempts ({attempt}) without confirmation",
                    correlation_id=correlation_id,
                )
                return {"status": "escalated", "attempt": attempt}

            # Schedule re-verification
            scheduler.schedule_reverification(
                external_operation_id=str(ext_op.id),
                case_id=str(case.id),
                attempt_number=attempt + 1,
                delay_seconds=5,  # Short delay for fast completion
            )
            return {"status": "pending_reverification", "attempt": attempt}