"""Outbox reconciliation beat task.

Runs every 30 seconds (configured in backend/worker.py beat_schedule).

Delivery model: at-least-once + idempotent (NOT exactly-once).

Case A: DB commit succeeds, beat hasn't run yet
  → Row stays enqueued=FALSE; picked up within 30s.

Case B: Beat publishes task, crashes before marking enqueued=TRUE
  → Duplicate Celery delivery; the async processor's idempotency guard
    (processing_status=PROCESSED check) absorbs the duplicate.

Case C: Redis/Celery unavailable
  → Row stays enqueued=FALSE; beat retries until Redis is reachable.

Locking: SELECT FOR UPDATE SKIP LOCKED
  SKIP LOCKED is appropriate here (queue consumption semantics).
  Multiple beat instances must not double-publish the same outbox record.
  A worker that finds a row locked simply skips it — the locking instance
  will publish it. This is different from StateMachine.transition() which
  uses FOR UPDATE without SKIP LOCKED.

Requirements: FR-WEBHOOK-007
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog
from sqlalchemy import select

from backend.database import async_session_factory
from backend.domain.models import OutboxEvent
from backend.worker import celery_app

logger = structlog.get_logger(__name__)

# Maximum outbox records processed per beat cycle (FR-WEBHOOK-007).
# Keeps each run bounded and prevents a large backlog from blocking the beat process.
BATCH_SIZE = 100


@celery_app.task(
    name="backend.webhook_processor.outbox.reconcile_outbox",
    bind=True,
    max_retries=0,  # Beat tasks don't retry; next beat cycle handles failures
)
def reconcile_outbox(self) -> dict:  # type: ignore[no-untyped-def]
    """Poll outbox_events for un-enqueued records and publish to Celery.

    This task is scheduled by Celery beat every 30 seconds (see
    beat_schedule in backend/worker.py). It scans the outbox_events table
    for records with enqueued=FALSE and publishes a Celery task for each,
    then marks the row enqueued=TRUE.

    On publish failure: leaves the row with enqueued=FALSE so the next
    beat cycle retries. This handles Case C (queue unavailable).

    Returns a summary dict with published/failed counts for observability.
    """
    return asyncio.run(_reconcile_outbox_async())


async def _reconcile_outbox_async() -> dict:
    """Async implementation of the outbox reconciliation.

    Uses SELECT FOR UPDATE SKIP LOCKED to safely handle concurrent beat
    instances (e.g., two beat processes racing during a rolling deploy).
    SKIP LOCKED is the correct semantic here: queue consumption, not case
    state serialization. Compare with StateMachine.transition() which uses
    FOR UPDATE without SKIP LOCKED.
    """
    published = 0
    failed = 0

    async with async_session_factory() as session:
        async with session.begin():
            # SELECT FOR UPDATE SKIP LOCKED — queue consumption semantics.
            # Order by created_at ASC ensures older events are processed first
            # (FIFO delivery within a batch). LIMIT 100 keeps each cycle bounded.
            stmt = (
                select(OutboxEvent)
                .where(OutboxEvent.enqueued == False)  # noqa: E712 — SQLAlchemy requires ==
                .order_by(OutboxEvent.created_at.asc())
                .limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            outbox_rows = list(result.scalars().all())

            for row in outbox_rows:
                try:
                    # Publish the async webhook processing task.
                    # The processor performs its own idempotency check on entry
                    # (processing_status=PROCESSED guard), so duplicate delivery
                    # from a crash between publish and mark-enqueued is safe.
                    task_result = celery_app.send_task(
                        "backend.webhook_processor.processor.process_webhook_event",
                        args=[str(row.webhook_event_id), str(row.merchant_id)],
                        queue="webhook",
                    )

                    # Mark enqueued only after successful publish (Case B: if we
                    # crash here, beat re-enqueues next cycle → idempotent duplicate).
                    row.enqueued = True
                    row.enqueued_at = datetime.now(timezone.utc)
                    row.celery_task_id = task_result.id
                    published += 1

                    logger.debug(
                        "outbox.published",
                        outbox_event_id=str(row.id),
                        webhook_event_id=str(row.webhook_event_id),
                        merchant_id=str(row.merchant_id),
                        celery_task_id=task_result.id,
                    )

                except Exception as exc:
                    # On publish failure: leave enqueued=FALSE for next beat cycle.
                    # Do NOT raise — we want to continue processing remaining rows
                    # and commit any successfully published rows in this batch.
                    logger.warning(
                        "outbox.publish_failed",
                        outbox_event_id=str(row.id),
                        webhook_event_id=str(row.webhook_event_id),
                        merchant_id=str(row.merchant_id),
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    failed += 1
                    # Re-raise would abort the whole transaction; we want to commit
                    # the rows that *did* publish. Isolation: row stays enqueued=FALSE
                    # because we never set row.enqueued = True above.

    logger.info(
        "outbox.reconciliation_complete",
        published=published,
        failed=failed,
        batch_size=BATCH_SIZE,
    )
    return {"published": published, "failed": failed}
