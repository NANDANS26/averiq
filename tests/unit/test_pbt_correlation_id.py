"""Property-Based Tests: Structured Log Correlation ID Presence (Property 13).

Property 13: Structured Log Correlation ID Presence
  For any operation processed by the system, every structured log entry
  emitted contains a non-null, non-empty correlation_id identical across
  all entries in the same chain.

Feature: averiq, Property 13: Structured Log Correlation ID Presence
Requirements: NFR-OBS-002

**Validates: Requirements NFR-OBS-002**
"""
from __future__ import annotations

import uuid
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# P13a — correlation_id is bound before any log entry in the request chain
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(correlation_id=st.uuids().map(str))
@settings(max_examples=100, deadline=None)
def test_p13_bind_correlation_id_makes_it_available(correlation_id: str) -> None:
    """After bind_correlation_id(), the correlation_id is present in structlog context.

    Feature: averiq, Property 13: Structured Log Correlation ID Presence
    **Validates: Requirements NFR-OBS-002**
    """
    import structlog
    from backend.observability.logging import bind_correlation_id

    structlog.contextvars.clear_contextvars()
    bind_correlation_id(correlation_id)

    ctx = structlog.contextvars.get_contextvars()
    assert "correlation_id" in ctx
    assert ctx["correlation_id"] == correlation_id
    assert ctx["correlation_id"]  # non-empty

    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# P13b — bind_case_context adds case_id and merchant_id
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(case_id=st.uuids().map(str), merchant_id=st.uuids().map(str))
@settings(max_examples=100)
def test_p13_bind_case_context_adds_ids(case_id: str, merchant_id: str) -> None:
    """bind_case_context() must add case_id and merchant_id to the structlog context.

    Feature: averiq, Property 13: Structured Log Correlation ID Presence
    **Validates: Requirements NFR-OBS-002**
    """
    import structlog
    from backend.observability.logging import bind_case_context

    structlog.contextvars.clear_contextvars()
    bind_case_context(case_id, merchant_id)

    ctx = structlog.contextvars.get_contextvars()
    assert ctx.get("case_id") == case_id
    assert ctx.get("merchant_id") == merchant_id

    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# P13c — same correlation_id across multiple bind+log calls in same chain
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(correlation_id=st.uuids().map(str), n_logs=st.integers(min_value=2, max_value=10))
@settings(max_examples=50)
def test_p13_same_correlation_id_across_chain(correlation_id: str, n_logs: int) -> None:
    """All log entries in the same chain must share the same correlation_id.

    Feature: averiq, Property 13: Structured Log Correlation ID Presence
    **Validates: Requirements NFR-OBS-002**
    """
    import structlog
    from backend.observability.logging import bind_correlation_id

    structlog.contextvars.clear_contextvars()
    bind_correlation_id(correlation_id)

    # All context reads within this request chain should show the same correlation_id
    for _ in range(n_logs):
        ctx = structlog.contextvars.get_contextvars()
        assert ctx.get("correlation_id") == correlation_id, (
            "All log entries in the same request chain must have the same correlation_id"
        )

    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# P13d — timed_operation context manager tracks latency
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(operation_name=st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz_"))
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_p13_timed_operation_does_not_raise(operation_name: str) -> None:
    """timed_operation context manager must not raise for any operation name.

    Feature: averiq, Property 13: Structured Log Correlation ID Presence
    **Validates: Requirements NFR-OBS-002**
    """
    from backend.observability.logging import timed_operation

    # Should complete without raising
    with timed_operation(operation_name, threshold_ms=10_000):
        pass  # No-op — just verify it doesn't raise
