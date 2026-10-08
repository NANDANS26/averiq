"""Async webhook event processor (Celery worker task).

processing_status semantics
---------------------------
PROCESSING:
    The worker is actively applying this event to the domain layer. If a worker
    crashes while in PROCESSING, the task is re-queued (acks_late +
    reject_on_worker_lost) and will be re-processed from scratch. The
    idempotency guard at entry absorbs the re-delivery safely once the
    original execution completes.

PROCESSED:
    The webhook event was successfully applied to the domain layer — either a
    new RevenueRiskCase was created in OBSERVED state or an existing active
    case was updated. This does NOT mean the Decision Engine has run, an action
    was executed, or the payment was recovered. Those outcomes are tracked in
    revenue_risk_cases.state and external_operations.status respectively.

FAILED_RETRYABLE:
    Processing failed transiently (DB connection error, timeout, etc.).
    The Celery retry mechanism will re-attempt with exponential backoff up to
    MAX_RETRIES attempts.

DEAD_LETTERED:
    All MAX_RETRIES attempts have been exhausted. The event requires manual
    intervention. The processing_status is set atomically in the on_failure
    handler before the task is discarded to the dead-letter queue.

Idempotency guarantee
---------------------
Duplicate Celery task delivery (at-least-once outbox semantics) is absorbed
by the entry-point idempotency guard: if processing_status == "PROCESSED",
the worker returns immediately with no domain side-effects.

Concurrent re-failure guard
---------------------------
Two workers may receive the same new payment.failed event for the same
(merchant_id, payment_id) pair simultaneously (e.g. duplicate outbox
entries from a beat race or a broker retry). Only ONE RevenueRiskCase must
be created.

Mechanism:
    1. Query active cases (state NOT IN terminal states) with SELECT FOR UPDATE
       — NOT SKIP LOCKED. The second worker blocks until the first commits.
    2. After the first worker commits, the second worker re-reads and finds
       the case created by the first worker. It updates rather than inserts.
    3. The unique partial index idx_cases_active_payment
       (revenue_risk_cases WHERE state NOT IN terminal_states) provides the
       DB-level guard: a concurrent INSERT that somehow bypasses the lock
       will fail with IntegrityError, which is caught and retried as a lookup.

Out-of-order events
-------------------
Razorpay does not guarantee ordered delivery (Known Limitation #8). When an
event arrives for a case that has already advanced past the implied state, the
processor skips the transition silently (FR-WEBHOOK-010). It does not fail —
the event is already in the past relative to the current case state.

Only valid forward transitions per ALLOWED_TRANSITIONS are applied.

Requirements: FR-WEBHOOK-009, FR-WEBHOOK-010, FR-WEBHOOK-011
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from celery import Task
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session_factory
from backend.domain.models import Payment, RevenueRiskCase, WebhookEvent
from backend.state_machine.machine import ALLOWED_TRANSITIONS, TERMINAL_STATES, StateMachine
from backend.worker import celery_app

logger = structlog.get_logger(__name__)

MAX_RETRIES: int = 5

# Shared StateMachine instance — stateless; safe to reuse across tasks.
state_machine = StateMachine()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# States that represent an active (non-terminal) recovery case.
# Used for the concurrent re-failure guard query.
_ACTIVE_STATES: frozenset[str] = frozenset(
    s for s in ALLOWED_TRANSITIONS if s not in TERMINAL_STATES
)


def _utcnow() -> datetime:
    """Return the current timezone-aware UTC datetime (DATA-004)."""
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Custom Task class — carries the on_failure hook (FR-WEBHOOK-011)
# ---------------------------------------------------------------------------


class _WebhookProcessorTask(Task):
    """Celery Task subclass that marks DEAD_LETTERED on final failure.

    Celery calls ``on_failure`` after the task has exhausted all retries.
    This hook sets processing_status = 'DEAD_LETTERED' so operators and
    monitoring systems can identify events that require manual intervention.

    Why a subclass rather than a standalone function?
    ---------------------------------------------------
    The ``on_failure`` hook must be a method on the Task class (or a subclass).
    Attaching it as a plain function to a task decorated with ``@celery_app.task``
    is not supported by the Celery public API. The ``base=`` kwarg on
    ``@celery_app.task`` is the canonical way to supply custom lifecycle hooks.
    """

    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: list[Any],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        """Mark the WebhookEvent as DEAD_LETTERED when all retries are exhausted.

        Called by Celery after the final retry attempt fails. At this point
        the task will not be retried again. The event must be manually
        investigated and potentially replayed.

        Args:
            exc:     The exception that caused the final failure.
            task_id: Celery task ID string.
            args:    Positional arguments passed to the task (args[0] is
                     webhook_event_id, args[1] is merchant_id).
            kwargs:  Keyword arguments passed to the task.
            einfo:   Celery ExceptionInfo with traceback details.
        """
        # args[0] is webhook_event_id (str), args[1] is merchant_id (str)
        if not args:
            logger.error(
                "webhook_processor.on_failure.no_args",
                task_id=task_id,
                error_type=type(exc).__name__,
            )
            return

        webhook_event_id_str: str = args[0]
        logger.error(
            "webhook_processor.dead_lettered",
            webhook_event_id=webhook_event_id_str,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=str(exc),
            note="All retries exhausted; marking DEAD_LETTERED — requires manual intervention",
        )

        asyncio.run(
            _mark_dead_lettered(
                webhook_event_id=uuid.UUID(webhook_event_id_str),
                error=str(exc),
            )
        )


# ---------------------------------------------------------------------------
# Celery task definition
# ---------------------------------------------------------------------------


@celery_app.task(
    name="backend.webhook_processor.processor.process_webhook_event",
    base=_WebhookProcessorTask,
    bind=True,
    max_retries=MAX_RETRIES,
    # Base retry delay in seconds; exponential backoff is applied in the
    # except block: countdown = 2 ** self.request.retries * 30
    default_retry_delay=30,
    queue="webhook",
    # Delivery guarantees (at-least-once + idempotent):
    acks_late=True,              # ACK only after successful processing
    reject_on_worker_lost=True,  # Re-queue on worker crash (FR-WEBHOOK-011)
)
def process_webhook_event(
    self: _WebhookProcessorTask,
    webhook_event_id: str,
    merchant_id: str,
) -> dict[str, Any]:
    """Process a webhook event: create or update a RevenueRiskCase.

    This is the primary Celery entry point. It delegates all async DB work
    to ``_process_async`` via ``asyncio.run``.

    Args:
        self:              The bound Celery Task instance.
        webhook_event_id:  UUID string of the WebhookEvent row to process.
        merchant_id:       UUID string of the owning Merchant.

    Returns:
        dict with ``case_id`` and ``status`` keys on success.

    Raises:
        celery.exceptions.Retry: on transient failures (auto-handled by Celery).
    """
    return asyncio.run(
        _process_async(
            task=self,
            webhook_event_id=uuid.UUID(webhook_event_id),
            merchant_id=uuid.UUID(merchant_id),
        )
    )


# ---------------------------------------------------------------------------
# Core async processing logic
# ---------------------------------------------------------------------------


async def _process_async(
    task: Task,
    webhook_event_id: uuid.UUID,
    merchant_id: uuid.UUID,
) -> dict[str, Any]:
    """Async implementation of the webhook event processing pipeline.

    Executes the following steps in sequence:

    1. Load WebhookEvent — if not found, return (already deleted).
    2. Idempotency guard — if PROCESSED, return immediately.
    3. Mark PROCESSING.
    4. Extract entity type and entity ID from the event.
    5. Load Payment (SELECT FOR UPDATE) when entity_type is "payment".
    6. Concurrent re-failure guard — SELECT FOR UPDATE on active case.
    7. Create new RevenueRiskCase (OBSERVED) if none found, else update.
    8. Apply OBSERVED → AT_RISK transition via StateMachine.
    9. Enqueue Decision Engine task.
    10. Mark PROCESSED.

    Error handling:
        Transient DB errors → exponential-backoff retry (2 ** retries * 30s).
        Max retries exhausted → on_failure handler marks DEAD_LETTERED.

    Args:
        task:              The bound Celery Task (for retry / request context).
        webhook_event_id:  UUID of the WebhookEvent to process.
        merchant_id:       UUID of the owning Merchant.

    Returns:
        {"case_id": str, "status": "processed"} on success.
        {"status": "already_processed"} when idempotency guard fires.
        {"status": "skipped_no_payment"} when entity is not a payment.
    """
    log = logger.bind(
        webhook_event_id=str(webhook_event_id),
        merchant_id=str(merchant_id),
        task_id=task.request.id,
        attempt=task.request.retries,
    )

    try:
        async with async_session_factory() as session:
            # ------------------------------------------------------------------
            # Step 1: Load WebhookEvent
            # ------------------------------------------------------------------
            webhook_event = await session.get(WebhookEvent, webhook_event_id)

            if webhook_event is None:
                # Event was deleted (e.g. manual cleanup or pruning). Nothing to do.
                log.warning(
                    "webhook_processor.event_not_found",
                    note="WebhookEvent row not found; skipping — may have been deleted",
                )
                return {"status": "skipped_not_found"}

            # ------------------------------------------------------------------
            # Step 2: Idempotency guard (FR-WEBHOOK-009)
            # ------------------------------------------------------------------
            if webhook_event.processing_status == "PROCESSED":
                log.info(
                    "webhook_processor.already_processed",
                    note="Idempotency guard fired — event already in PROCESSED state",
                )
                return {"status": "already_processed"}

            # Rollback autobegun read transaction if active before starting write block
            if session.in_transaction():
                await session.rollback()

            async with session.begin():
                # Re-fetch event inside write transaction
                webhook_event = await session.get(WebhookEvent, webhook_event_id)
                if webhook_event is None:
                    return {"status": "skipped_not_found"}
                if webhook_event.processing_status == "PROCESSED":
                    return {"status": "already_processed"}

                # ------------------------------------------------------------------
                # Step 3: Mark PROCESSING
                # ------------------------------------------------------------------
                webhook_event.processing_status = "PROCESSING"
                webhook_event.updated_at = _utcnow()
                await session.flush()

                log.info("webhook_processor.started")

                # Step 4: Extract entity type and entity ID
                entity_type: str | None = webhook_event.entity_type
                entity_id_str: str | None = webhook_event.entity_id

                if entity_type != "payment" or not entity_id_str:
                    log.info(
                        "webhook_processor.non_payment_entity",
                        entity_type=entity_type,
                        note="Skipping case creation for non-payment entity in MVP",
                    )
                    webhook_event.processing_status = "PROCESSED"
                    webhook_event.updated_at = _utcnow()
                    return {"status": "skipped_no_payment"}

                # Step 5: Load Payment with SELECT FOR UPDATE
                stmt = (
                    select(Payment)
                    .where(
                        Payment.merchant_id == merchant_id,
                        Payment.gateway_payment_id == entity_id_str,
                    )
                    .with_for_update()
                )
                res = await session.execute(stmt)
                payment = res.scalar_one_or_none()

                if payment is None:
                    log.warning(
                        "webhook_processor.payment_not_found",
                        gateway_payment_id=entity_id_str,
                        note="Payment row not found; marking PROCESSED to stop retries",
                    )
                    webhook_event.processing_status = "PROCESSED"
                    webhook_event.updated_at = _utcnow()
                    return {"status": "skipped_payment_not_found"}

                # Step 6 + 7: Concurrent re-failure guard + case creation/update
                active_case_stmt = (
                    select(RevenueRiskCase)
                    .where(
                        RevenueRiskCase.merchant_id == merchant_id,
                        RevenueRiskCase.payment_id == payment.id,
                        RevenueRiskCase.state.notin_(list(TERMINAL_STATES)),
                    )
                    .with_for_update()
                    .limit(1)
                )
                res_case = await session.execute(active_case_stmt)
                case = res_case.scalar_one_or_none()

                now = _utcnow()
                if case is not None:
                    case.updated_at = now
                    await session.flush()
                else:
                    case = RevenueRiskCase(
                        id=uuid.uuid4(),
                        merchant_id=merchant_id,
                        payment_id=payment.id,
                        customer_id=payment.customer_id,
                        subscription_id=payment.subscription_id,
                        previous_case_id=None,
                        state="OBSERVED",
                        payment_amount_at_risk=payment.amount,
                        currency=payment.currency,
                        projected_subscription_revenue_at_risk=None,
                        risk_calculation_path="SINGLE_PAYMENT",
                        exposure_tier=None,
                        recovery_risk_tier=None,
                        failure_category=None,
                        diagnosis_confidence=None,
                        ai_source=None,
                        recovery_probability=None,
                        decision_timestamp=None,
                        attribution_class=None,
                        recovered_amount=None,
                        recovered_currency=None,
                        is_demo_fixture=False,
                        operating_mode="SIMULATED",
                        case_metadata={},
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(case)
                    await session.flush()

                # Step 8: Apply OBSERVED -> AT_RISK state transition
                correlation_id = str(uuid.uuid4())
                if state_machine.can_transition(case.state, "AT_RISK"):
                    case = await state_machine.transition(
                        session=session,
                        case_id=case.id,
                        to_state="AT_RISK",
                        actor="SYSTEM",
                        reason=(
                            "Webhook event processed: payment failure confirmed. "
                            f"gateway_payment_id={payment.gateway_payment_id}"
                        ),
                        correlation_id=correlation_id,
                    )

                # Step 10: Mark PROCESSED
                webhook_event.processing_status = "PROCESSED"
                webhook_event.updated_at = _utcnow()

            # Step 9: Enqueue Decision Engine task (outside transaction commit)
            celery_app.send_task(
                "backend.decision_engine.tasks.run_decision_pipeline",
                args=[str(case.id), str(merchant_id)],
                queue="decisions",
                kwargs={},
                headers={"correlation_id": correlation_id},
            )

            log.info(
                "webhook_processor.completed",
                case_id=str(case.id),
                correlation_id=correlation_id,
            )
            return {"case_id": str(case.id), "status": "processed"}

    except Exception as exc:  # noqa: BLE001
        # ------------------------------------------------------------------
        # Transient error handling — exponential backoff retry
        #
        # Attempt to mark the event as FAILED_RETRYABLE to signal monitoring.
        # If the DB itself is unavailable, this secondary write may also fail —
        # that is acceptable; the retry mechanism remains active regardless.
        #
        # Backoff schedule (seconds):
        #   Attempt 0: 30s
        #   Attempt 1: 60s
        #   Attempt 2: 120s
        #   Attempt 3: 240s
        #   Attempt 4: 480s
        # ------------------------------------------------------------------
        countdown: int = (2 ** task.request.retries) * 30

        log.warning(
            "webhook_processor.transient_error",
            error_type=type(exc).__name__,
            error=str(exc),
            retry_attempt=task.request.retries,
            countdown_seconds=countdown,
        )

        # Best-effort status update — do not raise if this also fails
        try:
            await _mark_failed_retryable(
                webhook_event_id=webhook_event_id,
                error=str(exc),
            )
        except Exception as mark_exc:  # noqa: BLE001
            log.warning(
                "webhook_processor.failed_retryable_mark_failed",
                error_type=type(mark_exc).__name__,
                error=str(mark_exc),
            )

        # Re-raise as a Celery retry (FR-WEBHOOK-011).
        # After MAX_RETRIES, Celery calls on_failure which marks DEAD_LETTERED.
        raise task.retry(exc=exc, countdown=countdown)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _load_payment_for_update(
    *,
    session: AsyncSession,
    merchant_id: uuid.UUID,
    gateway_payment_id: str,
    log: Any,
) -> Payment | None:
    """Load a Payment row using SELECT FOR UPDATE.

    Acquires a row-level exclusive lock on the Payment row for the duration
    of the caller's transaction. This prevents concurrent processors from
    reading a stale payment status while the case is being created.

    Args:
        session:            The open AsyncSession.
        merchant_id:        UUID of the owning Merchant.
        gateway_payment_id: Razorpay payment ID string (e.g. "pay_XXXX").
        log:                Bound structlog logger for context-aware logging.

    Returns:
        The Payment ORM object, or None if not found.
    """
    stmt = (
        select(Payment)
        .where(
            Payment.merchant_id == merchant_id,
            Payment.gateway_payment_id == gateway_payment_id,
        )
        .with_for_update()  # NOT SKIP LOCKED — state serialisation semantics
    )
    async with session.begin():
        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()

    return payment


async def _find_or_create_case(
    *,
    session: AsyncSession,
    merchant_id: uuid.UUID,
    payment: Payment,
    webhook_event: WebhookEvent,
    log: Any,
) -> RevenueRiskCase:
    """Find an active RevenueRiskCase or create a new one.

    Concurrent re-failure guard (REL-CONC-001, REL-CONC-002):
    -----------------------------------------------------------
    Two workers may race on the same (merchant_id, payment_id). This function
    uses SELECT FOR UPDATE (NOT SKIP LOCKED) to serialise them:

    Worker A arrives first:
        → SELECT FOR UPDATE finds nothing → INSERT new case → COMMIT
    Worker B arrives while A holds the lock:
        → SELECT FOR UPDATE blocks until A commits
        → After A commits, B re-reads → finds A's case → updates, not inserts

    If, despite the lock, a concurrent INSERT somehow races to the DB before
    the lock is acquired (extremely rare given the FOR UPDATE), the partial
    unique index idx_cases_active_payment raises an IntegrityError. This is
    caught and resolved by re-fetching the existing case.

    Args:
        session:        The open AsyncSession.
        merchant_id:    UUID of the owning Merchant.
        payment:        The locked Payment ORM object.
        webhook_event:  The WebhookEvent being processed (for timestamp context).
        log:            Bound structlog logger.

    Returns:
        A RevenueRiskCase in OBSERVED or later state.
    """
    now = _utcnow()

    # Build the active-case query using SELECT FOR UPDATE to serialise
    # concurrent workers on the same (merchant_id, payment_id) pair.
    active_case_stmt = (
        select(RevenueRiskCase)
        .where(
            RevenueRiskCase.merchant_id == merchant_id,
            RevenueRiskCase.payment_id == payment.id,
            RevenueRiskCase.state.notin_(list(TERMINAL_STATES)),
        )
        .with_for_update()  # Serialise concurrent workers — NOT SKIP LOCKED
        .limit(1)
    )

    try:
        async with session.begin():
            result = await session.execute(active_case_stmt)
            existing_case: RevenueRiskCase | None = result.scalar_one_or_none()

            if existing_case is not None:
                # An active case already exists for this (merchant_id, payment_id).
                # This worker is either:
                #   (a) Processing a duplicate event for an ongoing recovery, or
                #   (b) A concurrent worker that lost the race on the initial INSERT.
                # Either way: update updated_at to reflect the latest processing
                # attempt and return the existing case.
                existing_case.updated_at = now
                await session.flush()

                log.info(
                    "webhook_processor.existing_case_found",
                    case_id=str(existing_case.id),
                    case_state=existing_case.state,
                    note="Active case found; updating rather than creating a duplicate",
                )
                return existing_case

            # No active case found — this is a new failure event.
            # Create a new RevenueRiskCase in OBSERVED state.
            new_case = RevenueRiskCase(
                id=uuid.uuid4(),
                merchant_id=merchant_id,
                payment_id=payment.id,
                customer_id=payment.customer_id,
                subscription_id=payment.subscription_id,
                previous_case_id=None,  # set below if prior terminal case exists
                state="OBSERVED",
                payment_amount_at_risk=payment.amount,
                currency=payment.currency,
                projected_subscription_revenue_at_risk=None,
                risk_calculation_path="SINGLE_PAYMENT",
                exposure_tier=None,
                recovery_risk_tier=None,
                failure_category=None,
                diagnosis_confidence=None,
                ai_source=None,
                recovery_probability=None,
                decision_timestamp=None,
                attribution_class=None,
                recovered_amount=None,
                recovered_currency=None,
                is_demo_fixture=False,
                operating_mode="SIMULATED",
                case_metadata={},
                created_at=now,
                updated_at=now,
            )

            session.add(new_case)
            await session.flush()

            log.info(
                "webhook_processor.case_created",
                case_id=str(new_case.id),
                payment_id=str(payment.id),
                amount=payment.amount,
                currency=payment.currency,
            )
            return new_case

    except IntegrityError:
        # The partial unique index idx_cases_active_payment rejected a duplicate
        # INSERT — another concurrent worker created the case between our
        # SELECT FOR UPDATE and our INSERT. Fetch that case now.
        log.warning(
            "webhook_processor.concurrent_insert_race",
            payment_id=str(payment.id),
            note=(
                "IntegrityError on INSERT — concurrent worker already created the case. "
                "Re-fetching existing active case."
            ),
        )
        # Re-fetch without FOR UPDATE (the race is resolved; we just need to read)
        async with session.begin():
            fallback_stmt = (
                select(RevenueRiskCase)
                .where(
                    RevenueRiskCase.merchant_id == merchant_id,
                    RevenueRiskCase.payment_id == payment.id,
                    RevenueRiskCase.state.notin_(list(TERMINAL_STATES)),
                )
                .limit(1)
            )
            result = await session.execute(fallback_stmt)
            recovered_case: RevenueRiskCase | None = result.scalar_one_or_none()

        if recovered_case is None:
            # Extremely unlikely: the concurrent case was created and immediately
            # reached a terminal state. Raise so the retry mechanism handles it.
            raise RuntimeError(
                f"Concurrent INSERT race resolved but no active case found "
                f"for payment_id={payment.id}. Letting Celery retry."
            ) from None

        log.info(
            "webhook_processor.concurrent_race_resolved",
            case_id=str(recovered_case.id),
            case_state=recovered_case.state,
        )
        return recovered_case


async def _mark_processed(
    session: AsyncSession,
    webhook_event: WebhookEvent,
) -> None:
    """Atomically mark the WebhookEvent as PROCESSED.

    Must be called AFTER all domain writes are committed so that the PROCESSED
    status accurately reflects that the domain layer has been fully updated.

    Args:
        session:       An open AsyncSession (outside any active transaction).
        webhook_event: The WebhookEvent to update.
    """
    async with session.begin():
        webhook_event.processing_status = "PROCESSED"
        webhook_event.updated_at = _utcnow()
        await session.flush()


async def _mark_failed_retryable(
    webhook_event_id: uuid.UUID,
    error: str,
) -> None:
    """Open a fresh session and mark the WebhookEvent as FAILED_RETRYABLE.

    Called on transient failure before scheduling a Celery retry. Uses its
    own session so that the caller's session rollback does not affect this
    status update.

    Args:
        webhook_event_id: UUID of the WebhookEvent to update.
        error:            Error message to record in last_error.
    """
    async with async_session_factory() as session:
        async with session.begin():
            event = await session.get(WebhookEvent, webhook_event_id)
            if event is not None:
                event.processing_status = "FAILED_RETRYABLE"
                event.retry_count = (event.retry_count or 0) + 1
                event.last_error = error[:1000]  # Truncate to fit Text column
                event.updated_at = _utcnow()
                await session.flush()


async def _mark_dead_lettered(
    webhook_event_id: uuid.UUID,
    error: str,
) -> None:
    """Open a fresh session and mark the WebhookEvent as DEAD_LETTERED.

    Called from the on_failure handler after all retries are exhausted.

    Args:
        webhook_event_id: UUID of the WebhookEvent to update.
        error:            Error message to record in last_error.
    """
    try:
        async with async_session_factory() as session:
            async with session.begin():
                event = await session.get(WebhookEvent, webhook_event_id)
                if event is not None:
                    event.processing_status = "DEAD_LETTERED"
                    event.last_error = error[:1000]  # Truncate to fit Text column
                    event.updated_at = _utcnow()
                    await session.flush()
    except Exception as exc:  # noqa: BLE001
        # This is a best-effort write in the on_failure handler.
        # Log and swallow — we must not raise here as it would obscure the
        # original failure and prevent Celery from completing the task lifecycle.
        logger.error(
            "webhook_processor.dead_letter_mark_failed",
            webhook_event_id=str(webhook_event_id),
            error_type=type(exc).__name__,
            error=str(exc),
            note=(
                "Failed to mark event as DEAD_LETTERED in on_failure handler. "
                "Event will remain in last known status."
            ),
        )
