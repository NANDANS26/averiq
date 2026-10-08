"""Unit tests for outbox reconciliation beat task (task 6.2, FR-WEBHOOK-007).

Tests verify:
  1. SELECT FOR UPDATE SKIP LOCKED semantics are used (not plain FOR UPDATE).
  2. On publish failure, `enqueued` stays False for next beat cycle.
  3. Batch limit of 100 is respected.
  4. Successfully published rows are marked enqueued=True with enqueued_at set.
  5. Partial batch failures do not abort successful publishes in the same batch.

All tests are pure unit tests — no real database or Redis.
Requirements: FR-WEBHOOK-007
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from backend.webhook_processor.outbox import BATCH_SIZE, _reconcile_outbox_async


# ---------------------------------------------------------------------------
# Helpers — build lightweight OutboxEvent stand-ins
# ---------------------------------------------------------------------------


def _make_outbox_row(
    enqueued: bool = False,
    row_id: uuid.UUID | None = None,
    webhook_event_id: uuid.UUID | None = None,
    merchant_id: uuid.UUID | None = None,
) -> MagicMock:
    """Return a MagicMock that quacks like an OutboxEvent ORM row."""
    row = MagicMock()
    row.id = row_id or uuid.uuid4()
    row.webhook_event_id = webhook_event_id or uuid.uuid4()
    row.merchant_id = merchant_id or uuid.uuid4()
    row.enqueued = enqueued
    row.enqueued_at = None
    row.celery_task_id = None
    return row


# ---------------------------------------------------------------------------
# Async context-manager helpers for mocking the session factory
# ---------------------------------------------------------------------------


def _make_async_ctx_manager(return_value: Any = None) -> MagicMock:
    """Return a MagicMock that behaves as an async context manager."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=return_value)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_session_factory_mock(outbox_rows: list[MagicMock]) -> tuple[MagicMock, MagicMock]:
    """Build a mock async_session_factory that yields a session returning `outbox_rows`."""
    # Scalars result
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = outbox_rows

    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_mock

    session = MagicMock()
    session.execute = AsyncMock(return_value=execute_result)

    # session.begin() must return an async context manager (not a coroutine)
    session.begin = MagicMock(return_value=_make_async_ctx_manager(return_value=None))

    # async_session_factory() → async context manager wrapping the session
    factory_ctx = _make_async_ctx_manager(return_value=session)
    factory_mock = MagicMock(return_value=factory_ctx)
    return factory_mock, session


# ---------------------------------------------------------------------------
# Test 1: SKIP LOCKED semantics are used
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skip_locked_is_used_in_query() -> None:
    """FR-WEBHOOK-007: query must use FOR UPDATE SKIP LOCKED, not plain FOR UPDATE.

    Validates that `with_for_update(skip_locked=True)` is passed to the
    SQLAlchemy statement. SKIP LOCKED is mandatory for queue consumption
    semantics so concurrent beat instances skip rows locked by another instance
    rather than blocking.
    """
    factory_mock, session = _make_session_factory_mock([])

    with (
        patch(
            "backend.webhook_processor.outbox.async_session_factory",
            factory_mock,
        ),
        patch("backend.webhook_processor.outbox.celery_app") as mock_celery,
    ):
        await _reconcile_outbox_async()

    # Verify session.execute was called
    session.execute.assert_called_once()

    # Extract the compiled statement passed to execute
    stmt_arg = session.execute.call_args[0][0]

    # Inspect the statement's _for_update_arg to verify skip_locked=True
    # SQLAlchemy stores locking info in _for_update_arg on the Select object
    for_update_arg = getattr(stmt_arg, "_for_update_arg", None)
    assert for_update_arg is not None, (
        "Statement must use with_for_update(); _for_update_arg is None"
    )
    # ForUpdateArg.skip_locked must be True
    assert getattr(for_update_arg, "skip_locked", False) is True, (
        "with_for_update(skip_locked=True) must be used for queue consumption semantics, "
        "not plain FOR UPDATE which would block concurrent beat instances"
    )


# ---------------------------------------------------------------------------
# Test 2: On publish failure, enqueued stays False
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publish_failure_leaves_enqueued_false() -> None:
    """FR-WEBHOOK-007: on Celery publish failure, row.enqueued must remain False.

    This covers Case C (Redis/Celery unavailable): the row stays with
    enqueued=FALSE so the next beat cycle retries automatically.
    The task must NOT raise; it logs a structured warning and continues.
    """
    row = _make_outbox_row(enqueued=False)
    factory_mock, session = _make_session_factory_mock([row])

    with (
        patch(
            "backend.webhook_processor.outbox.async_session_factory",
            factory_mock,
        ),
        patch("backend.webhook_processor.outbox.celery_app") as mock_celery,
    ):
        # Simulate Celery broker being unavailable
        mock_celery.send_task.side_effect = ConnectionError("Redis unavailable")

        result = await _reconcile_outbox_async()

    # Row must NOT have been marked enqueued
    assert row.enqueued is False, (
        "enqueued must remain False when Celery publish fails"
    )
    assert row.enqueued_at is None, (
        "enqueued_at must not be set when publish fails"
    )
    assert row.celery_task_id is None, (
        "celery_task_id must not be set when publish fails"
    )

    # Result must report the failure
    assert result["published"] == 0
    assert result["failed"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publish_failure_does_not_raise() -> None:
    """FR-WEBHOOK-007: publish failure must be handled gracefully (no exception propagated).

    The beat task must complete without raising so the beat scheduler continues
    running subsequent cycles.
    """
    row = _make_outbox_row(enqueued=False)
    factory_mock, _ = _make_session_factory_mock([row])

    with (
        patch(
            "backend.webhook_processor.outbox.async_session_factory",
            factory_mock,
        ),
        patch("backend.webhook_processor.outbox.celery_app") as mock_celery,
    ):
        mock_celery.send_task.side_effect = RuntimeError("broker timeout")

        # Must NOT raise
        result = await _reconcile_outbox_async()

    assert result["failed"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_failure_does_not_abort_successful_rows() -> None:
    """FR-WEBHOOK-007: a failure on one row must not prevent other rows from being published.

    In a batch of 3 rows where the middle one fails, rows 1 and 3 should still
    be published and marked enqueued=True.
    """
    row_ok_1 = _make_outbox_row(enqueued=False)
    row_fail = _make_outbox_row(enqueued=False)
    row_ok_2 = _make_outbox_row(enqueued=False)

    factory_mock, _ = _make_session_factory_mock([row_ok_1, row_fail, row_ok_2])

    task_result_mock = MagicMock()
    task_result_mock.id = "celery-task-id-ok"

    def _side_effect(task_name: str, args: list, queue: str) -> MagicMock:
        # Fail only when processing row_fail
        if args[0] == str(row_fail.webhook_event_id):
            raise ConnectionError("Broker down")
        return task_result_mock

    with (
        patch(
            "backend.webhook_processor.outbox.async_session_factory",
            factory_mock,
        ),
        patch("backend.webhook_processor.outbox.celery_app") as mock_celery,
    ):
        mock_celery.send_task.side_effect = _side_effect

        result = await _reconcile_outbox_async()

    # Successful rows must be marked enqueued
    assert row_ok_1.enqueued is True
    assert row_ok_1.celery_task_id == "celery-task-id-ok"

    assert row_ok_2.enqueued is True
    assert row_ok_2.celery_task_id == "celery-task-id-ok"

    # Failed row must stay un-enqueued
    assert row_fail.enqueued is False
    assert row_fail.celery_task_id is None

    assert result["published"] == 2
    assert result["failed"] == 1


# ---------------------------------------------------------------------------
# Test 3: Batch limit of 100 is respected
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_batch_size_constant_is_100() -> None:
    """FR-WEBHOOK-007: BATCH_SIZE constant must be exactly 100.

    The design specifies LIMIT 100 per run to keep each beat cycle bounded
    and prevent a large backlog from blocking the process.
    """
    assert BATCH_SIZE == 100, (
        f"BATCH_SIZE must be 100 per spec (FR-WEBHOOK-007); got {BATCH_SIZE}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_applies_limit_of_batch_size() -> None:
    """FR-WEBHOOK-007: the SQLAlchemy query must include .limit(BATCH_SIZE).

    Validates that the query passed to session.execute includes the LIMIT
    clause so the database never returns more than BATCH_SIZE rows per cycle.
    """
    factory_mock, session = _make_session_factory_mock([])

    with (
        patch(
            "backend.webhook_processor.outbox.async_session_factory",
            factory_mock,
        ),
        patch("backend.webhook_processor.outbox.celery_app"),
    ):
        await _reconcile_outbox_async()

    stmt_arg = session.execute.call_args[0][0]

    # SQLAlchemy Select stores the LIMIT in _limit_clause (a BindParameter).
    # Retrieve the effective value via the clause's .value or .effective_value.
    limit_clause = getattr(stmt_arg, "_limit_clause", None)
    assert limit_clause is not None, "Query must include .limit()"

    # BindParameter exposes the value via .value or .effective_value
    limit_value = getattr(
        limit_clause,
        "effective_value",
        getattr(limit_clause, "value", None),
    )
    assert limit_value == BATCH_SIZE, (
        f"Query LIMIT must be {BATCH_SIZE}; got {limit_value!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: Successfully published rows are marked enqueued=True
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_successful_publish_marks_enqueued_true() -> None:
    """FR-WEBHOOK-007: after a successful Celery publish, row.enqueued must be True."""
    row = _make_outbox_row(enqueued=False)
    factory_mock, _ = _make_session_factory_mock([row])

    task_result_mock = MagicMock()
    task_result_mock.id = "test-task-id-abc"

    with (
        patch(
            "backend.webhook_processor.outbox.async_session_factory",
            factory_mock,
        ),
        patch("backend.webhook_processor.outbox.celery_app") as mock_celery,
    ):
        mock_celery.send_task.return_value = task_result_mock

        result = await _reconcile_outbox_async()

    assert row.enqueued is True
    assert row.celery_task_id == "test-task-id-abc"
    assert row.enqueued_at is not None
    assert isinstance(row.enqueued_at, datetime)

    assert result["published"] == 1
    assert result["failed"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_celery_task_published_with_correct_args() -> None:
    """FR-WEBHOOK-007: Celery task must be published with webhook_event_id and merchant_id."""
    wh_id = uuid.uuid4()
    merchant_id = uuid.uuid4()
    row = _make_outbox_row(
        enqueued=False,
        webhook_event_id=wh_id,
        merchant_id=merchant_id,
    )
    factory_mock, _ = _make_session_factory_mock([row])

    task_result_mock = MagicMock()
    task_result_mock.id = "task-id-xyz"

    with (
        patch(
            "backend.webhook_processor.outbox.async_session_factory",
            factory_mock,
        ),
        patch("backend.webhook_processor.outbox.celery_app") as mock_celery,
    ):
        mock_celery.send_task.return_value = task_result_mock

        await _reconcile_outbox_async()

    mock_celery.send_task.assert_called_once_with(
        "backend.webhook_processor.processor.process_webhook_event",
        args=[str(wh_id), str(merchant_id)],
        queue="webhook",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_outbox_returns_zero_counts() -> None:
    """FR-WEBHOOK-007: when no rows are pending, result must be published=0, failed=0."""
    factory_mock, _ = _make_session_factory_mock([])

    with (
        patch(
            "backend.webhook_processor.outbox.async_session_factory",
            factory_mock,
        ),
        patch("backend.webhook_processor.outbox.celery_app") as mock_celery,
    ):
        result = await _reconcile_outbox_async()

    mock_celery.send_task.assert_not_called()
    assert result["published"] == 0
    assert result["failed"] == 0


# ---------------------------------------------------------------------------
# Test 5: Query filters on enqueued=False
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_filters_on_enqueued_false() -> None:
    """FR-WEBHOOK-007: query WHERE clause must filter on enqueued=FALSE.

    Rows with enqueued=TRUE must never be re-published; they are already
    in the Celery queue (or have been processed).
    """
    factory_mock, session = _make_session_factory_mock([])

    with (
        patch(
            "backend.webhook_processor.outbox.async_session_factory",
            factory_mock,
        ),
        patch("backend.webhook_processor.outbox.celery_app"),
    ):
        await _reconcile_outbox_async()

    stmt_arg = session.execute.call_args[0][0]

    # Walk the WHERE criteria to confirm OutboxEvent.enqueued == False is present
    where_criteria = stmt_arg.whereclause
    assert where_criteria is not None, "Query must include a WHERE clause"

    # The criterion's left side should reference the 'enqueued' column
    criterion_str = str(where_criteria.compile())
    assert "enqueued" in criterion_str.lower(), (
        "WHERE clause must filter on the 'enqueued' column"
    )
