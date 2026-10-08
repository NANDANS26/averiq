"""Structured logging helpers for Averiq.

Provides:
- bind_correlation_id(): bind a correlation_id to the structlog context
- bind_case_context(): bind case_id and merchant_id to the structlog context
- timed_operation(): context manager that measures operation latency and emits
  a structured warning when the threshold is exceeded

All helpers operate on structlog's contextvars — they are safe to call from
sync and async code alike. Every bound variable is automatically included in
every subsequent log entry in the same request/task chain.

Requirements: NFR-OBS-001, NFR-OBS-002, NFR-OBS-006
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Generator

import structlog

logger = structlog.get_logger(__name__)


def bind_correlation_id(correlation_id: str) -> None:
    """Bind a correlation_id to the current structlog context.

    After this call every log entry emitted in the same context chain will
    include ``correlation_id``.  Call this once per logical operation boundary
    (e.g. webhook receipt, Celery task start).

    Requirements: NFR-OBS-002
    """
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)


def bind_case_context(case_id: str, merchant_id: str) -> None:
    """Bind case_id and merchant_id to the current structlog context.

    After this call every log entry emitted in the same context chain will
    include both ``case_id`` and ``merchant_id``.

    Requirements: NFR-OBS-001, NFR-OBS-002
    """
    structlog.contextvars.bind_contextvars(case_id=case_id, merchant_id=merchant_id)


@contextmanager
def timed_operation(
    operation_name: str,
    threshold_ms: float = 1_000.0,
) -> Generator[None, None, None]:
    """Context manager that measures wall-clock latency for a named operation.

    Emits a structured warning log entry when the measured latency exceeds
    *threshold_ms* milliseconds.

    Usage::

        with timed_operation("fetch_payment_status", threshold_ms=500):
            result = await gateway.fetch_payment(payment_id)

    Requirements: NFR-OBS-006
    """
    start = time.monotonic()
    try:
        yield
    finally:
        duration_ms = (time.monotonic() - start) * 1_000
        if duration_ms > threshold_ms:
            logger.warning(
                "operation.latency_exceeded",
                operation=operation_name,
                duration_ms=round(duration_ms, 2),
                threshold_ms=threshold_ms,
            )
