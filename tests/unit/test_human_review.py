"""Unit tests for the HumanReview workflow.

Tests cover the HumanReviewManager lifecycle transitions and audit atomicity.

Requirements: FR-POLICY-007
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from backend.domain.models import AuditEvent, HumanReview
from backend.policy_engine.human_review import (
    ALLOWED_REVIEW_TRANSITIONS,
    HumanReviewManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_review(status: str = "OPEN") -> HumanReview:
    """Create a minimal HumanReview ORM-like object for testing."""
    review = MagicMock(spec=HumanReview)
    review.id = uuid.uuid4()
    review.merchant_id = uuid.uuid4()
    review.case_id = uuid.uuid4()
    review.intervention_id = uuid.uuid4()
    review.status = status
    review.assigned_to = None
    review.reviewed_by = None
    review.review_notes = None
    review.resolved_at = None
    review.created_at = datetime.now(timezone.utc)
    review.updated_at = datetime.now(timezone.utc)
    return review


def _make_session() -> AsyncMock:
    """Return a minimal async session mock."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Test 1: OPEN → APPROVED → RESOLVED succeeds
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_approve_open_review_transitions_to_resolved() -> None:
    """OPEN → APPROVED → RESOLVED lifecycle completes without error."""
    manager = HumanReviewManager()
    session = _make_session()
    review = _make_review(status="OPEN")
    reviewer_id = uuid.uuid4()

    result = await manager.approve(
        session, review, reviewer_id, notes="Looks good", correlation_id=str(uuid.uuid4())
    )

    assert result.status == "RESOLVED"
    # resolved_at must be set
    assert result.resolved_at is not None
    # reviewer_by set on APPROVED step
    assert result.reviewed_by == reviewer_id
    # review_notes set during APPROVED step
    assert result.review_notes == "Looks good"
    # session.flush called 3 times (OPEN→ASSIGNED, ASSIGNED→APPROVED, APPROVED→RESOLVED)
    assert session.flush.call_count == 3
    # Three AuditEvent objects added
    assert session.add.call_count == 3


# ---------------------------------------------------------------------------
# Test 2: OPEN → REJECTED → RESOLVED succeeds
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reject_open_review_transitions_to_resolved() -> None:
    """OPEN → REJECTED → RESOLVED lifecycle completes without error."""
    manager = HumanReviewManager()
    session = _make_session()
    review = _make_review(status="OPEN")
    reviewer_id = uuid.uuid4()

    result = await manager.reject(
        session, review, reviewer_id, reason="Not justified", correlation_id=str(uuid.uuid4())
    )

    assert result.status == "RESOLVED"
    assert result.resolved_at is not None
    assert result.reviewed_by == reviewer_id
    assert result.review_notes == "Not justified"
    # 3 flushes: OPEN→ASSIGNED, ASSIGNED→REJECTED, REJECTED→RESOLVED
    assert session.flush.call_count == 3
    assert session.add.call_count == 3


# ---------------------------------------------------------------------------
# Test 3: RESOLVED → any transition raises ValueError
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_from_resolved_raises_value_error() -> None:
    """Any transition attempt from RESOLVED raises ValueError (terminal state)."""
    manager = HumanReviewManager()
    session = _make_session()
    reviewer_id = uuid.uuid4()

    for target in ("APPROVED", "REJECTED", "STOPPED", "ASSIGNED", "OPEN"):
        review = _make_review(status="RESOLVED")
        with pytest.raises(ValueError, match="Invalid review transition"):
            await manager._transition(
                session,
                review,
                target,
                reviewer_id,
                notes=None,
                correlation_id=str(uuid.uuid4()),
            )


# ---------------------------------------------------------------------------
# Test 4: Audit event added in same flush as review transition
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_audit_event_added_before_flush() -> None:
    """An AuditEvent is session.add()-ed before each session.flush() call."""
    manager = HumanReviewManager()
    session = _make_session()
    # Start from ASSIGNED so we exercise a single ASSIGNED→APPROVED transition
    review = _make_review(status="ASSIGNED")
    reviewer_id = uuid.uuid4()

    call_order: list[str] = []

    def track_add(obj: object) -> None:
        call_order.append("add")

    async def track_flush() -> None:
        call_order.append("flush")

    session.add.side_effect = track_add
    session.flush.side_effect = track_flush

    await manager._transition(
        session,
        review,
        "APPROVED",
        reviewer_id,
        notes=None,
        correlation_id=str(uuid.uuid4()),
    )

    # add must come before flush in each transition
    assert call_order == ["add", "flush"], (
        f"Expected ['add', 'flush'] but got {call_order!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: reviewed_by set on APPROVED transition
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reviewed_by_set_on_approved_transition() -> None:
    """reviewed_by is populated with actor_id when transitioning to APPROVED."""
    manager = HumanReviewManager()
    session = _make_session()
    # Start from ASSIGNED — direct valid path to APPROVED
    review = _make_review(status="ASSIGNED")
    reviewer_id = uuid.uuid4()

    await manager._transition(
        session,
        review,
        "APPROVED",
        reviewer_id,
        notes="approved",
        correlation_id=str(uuid.uuid4()),
    )

    assert review.reviewed_by == reviewer_id
    assert review.review_notes == "approved"
    assert review.status == "APPROVED"


# ---------------------------------------------------------------------------
# Test 6: resolved_at set on RESOLVED transition
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolved_at_set_on_resolved_transition() -> None:
    """resolved_at timestamp is set when transitioning to RESOLVED."""
    manager = HumanReviewManager()
    session = _make_session()
    # APPROVED can transition to RESOLVED
    review = _make_review(status="APPROVED")
    reviewer_id = uuid.uuid4()

    before = datetime.now(timezone.utc)
    await manager._transition(
        session,
        review,
        "RESOLVED",
        reviewer_id,
        notes=None,
        correlation_id=str(uuid.uuid4()),
    )
    after = datetime.now(timezone.utc)

    assert review.resolved_at is not None
    # Verify the timestamp is within the expected window
    assert before <= review.resolved_at <= after
    assert review.status == "RESOLVED"
