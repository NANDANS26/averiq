"""Re-verification scheduler with escalation on exhaustion.

Schedules re-verification Celery tasks with configurable delays.
Maximum 3 re-verification attempts before escalating to ESCALATED state.

Per-attempt records are written to ``verification_attempts``.
Raw gateway response payloads are NEVER stored in AuditEvent or
``verification_attempts`` (FR-VERIFY-008, AUD-006).

Requirements: FR-VERIFY-006, FR-VERIFY-007, FR-VERIFY-008
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.models import VerificationAttempt

logger = structlog.get_logger(__name__)

MAX_VERIFICATION_ATTEMPTS: int = 3
DEFAULT_REVERIFICATION_DELAY_SECONDS: int = 300  # 5 minutes


class VerificationScheduler:
    """Manages re-verification attempt recording and escalation logic.

    Responsibilities:
    - ``record_attempt`` — write a normalised ``VerificationAttempt`` row.
      Raw gateway payloads are NEVER stored (FR-VERIFY-008, AUD-006).
    - ``should_escalate`` — returns True when the attempt count for an
      ``ExternalOperation`` has reached or exceeded ``MAX_VERIFICATION_ATTEMPTS``.
    - ``schedule_reverification`` — enqueue a Celery re-verification task
      with a configurable countdown delay (FR-VERIFY-006).
    """

    async def record_attempt(
        self,
        session: AsyncSession,
        external_operation_id: uuid.UUID,
        attempt_number: int,
        outcome: str | None,
        gateway_status: str | None,
        verification_strategy: str,
        scheduled_at: datetime,
    ) -> VerificationAttempt:
        """Record a single verification attempt in ``verification_attempts``.

        Raw gateway response payloads are NOT stored here (FR-VERIFY-008).
        Only normalised status fields are persisted.

        ``verification_timestamp`` is set to the current UTC instant.
        ``scheduled_at`` is accepted for caller context but is not a separate
        DB column — the ORM model uses ``verification_timestamp`` for the
        execution instant (DATA-004).

        Args:
            session: The active async database session.
            external_operation_id: FK to ``external_operations.id``.
            attempt_number: 1-based attempt counter for this operation.
            outcome: Normalised result string (e.g. "VERIFIED", "UNKNOWN",
                "FAILED") — NOT a raw gateway payload.
            gateway_status: Normalised gateway-reported status string
                (e.g. "paid", "active") — NOT a raw response body.
            verification_strategy: Human-readable strategy name for
                observability (e.g. "PaymentLinkReconciliationStrategy").
            scheduled_at: When the attempt was originally scheduled
                (used for structured logging only).

        Returns:
            The newly-created and flushed ``VerificationAttempt`` ORM instance.
        """
        now = datetime.now(timezone.utc)
        attempt = VerificationAttempt(
            id=uuid.uuid4(),
            external_operation_id=external_operation_id,
            attempt_number=attempt_number,
            outcome=outcome,
            # Normalised status only — raw gateway response body is NOT stored
            # (FR-VERIFY-008, AUD-006).
            gateway_reported_status=gateway_status,
            # ``provider`` stores the verification strategy for observability.
            # Raw payloads are never included.
            provider=verification_strategy,
            # DATA-004 — always UTC with timezone info
            verification_timestamp=now,
        )
        session.add(attempt)
        await session.flush()

        logger.info(
            "verification_attempt_recorded",
            external_operation_id=str(external_operation_id),
            attempt_number=attempt_number,
            outcome=outcome,
            scheduled_at=scheduled_at.isoformat(),
            executed_at=now.isoformat(),
        )
        return attempt

    async def should_escalate(
        self,
        session: AsyncSession,
        external_operation_id: uuid.UUID,
    ) -> bool:
        """Return True when the attempt count has reached MAX_VERIFICATION_ATTEMPTS.

        Called after each attempt to decide whether to escalate the case to
        ESCALATED state (FR-VERIFY-007) or schedule another re-verification.

        Args:
            session: The active async database session.
            external_operation_id: FK to ``external_operations.id`` to count
                attempts for.

        Returns:
            True if ``count >= MAX_VERIFICATION_ATTEMPTS``, False otherwise.
        """
        stmt = select(func.count(VerificationAttempt.id)).where(
            VerificationAttempt.external_operation_id == external_operation_id,
        )
        result = await session.execute(stmt)
        count: int = result.scalar() or 0

        logger.debug(
            "should_escalate_check",
            external_operation_id=str(external_operation_id),
            attempt_count=count,
            max_attempts=MAX_VERIFICATION_ATTEMPTS,
            will_escalate=count >= MAX_VERIFICATION_ATTEMPTS,
        )
        return count >= MAX_VERIFICATION_ATTEMPTS

    def schedule_reverification(
        self,
        external_operation_id: str,
        case_id: str,
        attempt_number: int,
        delay_seconds: int = DEFAULT_REVERIFICATION_DELAY_SECONDS,
    ) -> str:
        """Enqueue a re-verification Celery task with the given countdown.

        Called when the current attempt outcome is UNKNOWN and escalation
        threshold has NOT been reached (FR-VERIFY-006).

        The task is routed to the ``verification`` queue with ``countdown``
        (not eta) so that Celery applies the delay at enqueue time.

        Args:
            external_operation_id: String UUID of the ExternalOperation.
            case_id: String UUID of the RevenueRiskCase.
            attempt_number: The next attempt number (current + 1).
            delay_seconds: Seconds to wait before the next attempt.
                Defaults to ``DEFAULT_REVERIFICATION_DELAY_SECONDS`` (5 min).

        Returns:
            The Celery task ID string on success, or empty string on failure.
            An empty string indicates the task was not enqueued; callers
            should log a warning and handle gracefully (the beat loop will
            eventually re-attempt reconciliation via the outbox mechanism).
        """
        try:
            from backend.worker import celery_app  # late import to avoid circular deps

            result = celery_app.send_task(
                "backend.verification_engine.tasks.run_verification",
                args=[external_operation_id, case_id, attempt_number],
                countdown=delay_seconds,
                queue="verification",
            )
            task_id: str = result.id

            logger.info(
                "reverification_scheduled",
                external_operation_id=external_operation_id,
                case_id=case_id,
                attempt_number=attempt_number,
                delay_seconds=delay_seconds,
                celery_task_id=task_id,
            )
            return task_id

        except Exception as exc:
            logger.warning(
                "reverification_scheduling_failed",
                external_operation_id=external_operation_id,
                case_id=case_id,
                attempt_number=attempt_number,
                error=str(exc),
            )
            return ""
