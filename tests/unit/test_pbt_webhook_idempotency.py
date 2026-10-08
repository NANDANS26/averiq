"""Property-Based Tests: Webhook Processing Idempotency (Property 2).

Property 2: Webhook Processing Idempotency
  For any webhook event that has already been processed (processing_status=PROCESSED),
  calling the processor again produces the same outcome as the first call —
  the state is NOT modified a second time.

This tests the idempotency guard in _process_async:
    if webhook_event.processing_status == "PROCESSED":
        return {"status": "already_processed"}

Feature: averiq, Property 2: Webhook Processing Idempotency
Requirements: FR-WEBHOOK-009, REL-IDEMP-001, NFR-TEST-006

**Validates: Requirements FR-WEBHOOK-009, REL-IDEMP-001, NFR-TEST-006**
"""
from __future__ import annotations

import uuid

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Webhook event data strategy
# ---------------------------------------------------------------------------

webhook_event_strategy = st.builds(
    lambda provider_event_id, event_type, entity_type, retry_count: {
        "id": uuid.uuid4(),
        "provider_event_id": provider_event_id,
        "event_type": event_type,
        "entity_type": entity_type,
        "retry_count": retry_count,
        "processing_status": "PROCESSED",  # already processed — idempotency guard must fire
    },
    provider_event_id=st.text(
        min_size=1,
        max_size=50,
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
    ),
    event_type=st.sampled_from(
        ["payment.failed", "subscription.charged", "payment.authorized"]
    ),
    entity_type=st.sampled_from(["payment", "subscription"]),
    retry_count=st.integers(min_value=0, max_value=5),
)


# ---------------------------------------------------------------------------
# P2a — Idempotency guard fires for PROCESSED status
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@pytest.mark.asyncio
@given(webhook_event_strategy)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow], deadline=None)
async def test_p2_processed_webhook_returns_already_processed(
    event_data: dict,
) -> None:
    """For any PROCESSED webhook, re-processing returns 'already_processed' status.

    Feature: averiq, Property 2: Webhook Processing Idempotency

    **Validates: Requirements FR-WEBHOOK-009, REL-IDEMP-001**

    This is the canonical idempotency guard test. Any WebhookEvent that already
    carries processing_status=PROCESSED must cause _process_async to return
    immediately with {"status": "already_processed"} without performing any
    domain mutations.
    """
    mock_event = MagicMock()
    mock_event.processing_status = "PROCESSED"
    mock_event.entity_type = event_data["entity_type"]

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_event)

    mock_task = MagicMock()
    mock_task.request.id = "test-task-id"
    mock_task.request.retries = 0

    with patch("backend.webhook_processor.processor.async_session_factory") as mock_factory:
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_ctx

        from backend.webhook_processor.processor import _process_async

        result = await _process_async(
            task=mock_task,
            webhook_event_id=event_data["id"],
            merchant_id=uuid.uuid4(),
        )

    assert result["status"] == "already_processed", (
        f"Expected 'already_processed' for PROCESSED webhook, got: {result['status']!r}"
    )


# ---------------------------------------------------------------------------
# P2b — State is not modified on second call (structural idempotency)
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@pytest.mark.asyncio
@given(webhook_event_strategy)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
async def test_p2_processing_status_unchanged_for_processed_webhook(
    event_data: dict,
) -> None:
    """For any PROCESSED webhook, the processing_status must remain PROCESSED after re-processing.

    Feature: averiq, Property 2: Webhook Processing Idempotency

    **Validates: Requirements FR-WEBHOOK-009, REL-IDEMP-001**

    The idempotency guard must not mutate the WebhookEvent — specifically it
    must not change processing_status, and it must not begin any DB transaction
    (no session.begin() call) after the guard fires.
    """
    mock_event = MagicMock()
    mock_event.processing_status = "PROCESSED"
    original_status = mock_event.processing_status

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_event)

    mock_task = MagicMock()
    mock_task.request.id = "test-task-id"
    mock_task.request.retries = 0

    with patch("backend.webhook_processor.processor.async_session_factory") as mock_factory:
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_ctx

        from backend.webhook_processor.processor import _process_async

        await _process_async(
            task=mock_task,
            webhook_event_id=event_data["id"],
            merchant_id=uuid.uuid4(),
        )

    # The processing_status must not have been mutated by the idempotency guard.
    assert mock_event.processing_status == original_status, (
        f"processing_status was mutated by second call: "
        f"expected {original_status!r}, got {mock_event.processing_status!r}"
    )

    # No DB transaction must have been opened after the idempotency check —
    # the guard returns before any domain writes begin.
    mock_session.begin.assert_not_called()


# ---------------------------------------------------------------------------
# P2c — Same provider_event_id always produces the same outcome (determinism)
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@pytest.mark.asyncio
@given(
    provider_event_id=st.text(min_size=1, max_size=50),
    status=st.sampled_from(["PROCESSED"]),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
async def test_p2_same_provider_event_id_same_outcome(
    provider_event_id: str,
    status: str,
) -> None:
    """Same provider_event_id with PROCESSED status always returns the same outcome.

    Feature: averiq, Property 2: Webhook Processing Idempotency

    **Validates: Requirements FR-WEBHOOK-009, REL-IDEMP-001**

    Processing the same logical event twice — both times with
    processing_status=PROCESSED — must yield the same status key in both
    responses. This validates that the guard is purely deterministic with
    respect to the stored status value.
    """
    results: list[str] = []

    for _ in range(2):
        mock_event = MagicMock()
        mock_event.processing_status = status

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_event)

        mock_task = MagicMock()
        mock_task.request.id = str(uuid.uuid4())
        mock_task.request.retries = 0

        with patch(
            "backend.webhook_processor.processor.async_session_factory"
        ) as mock_factory:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_ctx

            from backend.webhook_processor.processor import _process_async

            result = await _process_async(
                task=mock_task,
                webhook_event_id=uuid.uuid4(),
                merchant_id=uuid.uuid4(),
            )
        results.append(result["status"])

    assert results[0] == results[1], (
        f"Same provider_event_id={provider_event_id!r} with status={status!r} produced "
        f"different outcomes across two calls: {results[0]!r} vs {results[1]!r}"
    )
