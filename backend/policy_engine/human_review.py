"""HumanReview workflow.

Lifecycle: OPEN → ASSIGNED → APPROVED | REJECTED | STOPPED → RESOLVED

OPERATOR+ roles can approve/reject interventions in the HUMAN_REVIEW queue.
Reviewer actions record the outcome in human_reviews but do NOT directly
mutate terminal case states — the execution engine acts on the review outcome.

Requirements: FR-POLICY-007
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.models import AuditEvent, HumanReview, Intervention

logger = structlog.get_logger(__name__)

ALLOWED_REVIEW_TRANSITIONS: dict[str, frozenset[str]] = {
    "OPEN":     frozenset({"ASSIGNED", "STOPPED"}),
    "ASSIGNED": frozenset({"APPROVED", "REJECTED", "STOPPED"}),
    "APPROVED": frozenset({"RESOLVED"}),
    "REJECTED": frozenset({"RESOLVED"}),
    "STOPPED":  frozenset({"RESOLVED"}),
    "RESOLVED": frozenset(),
}


class HumanReviewManager:
    """Manages the human review lifecycle for intervention approvals."""

    async def get_pending_reviews(
        self, session: AsyncSession, merchant_id: uuid.UUID, limit: int = 50
    ) -> list[HumanReview]:
        """Return OPEN and ASSIGNED reviews for the merchant queue."""
        stmt = (
            select(HumanReview)
            .where(
                HumanReview.merchant_id == merchant_id,
                HumanReview.status.in_(["OPEN", "ASSIGNED"]),
            )
            .order_by(HumanReview.created_at.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def approve(
        self,
        session: AsyncSession,
        review: HumanReview,
        reviewer_id: uuid.UUID,
        notes: str | None,
        correlation_id: str,
    ) -> HumanReview:
        """Approve a OPEN/ASSIGNED review → (ASSIGNED →) APPROVED → RESOLVED.

        If the review is still OPEN, it is first transitioned to ASSIGNED
        before being APPROVED, following the defined lifecycle:
          OPEN → ASSIGNED → APPROVED → RESOLVED
        """
        if review.status == "OPEN":
            review = await self._transition(
                session, review, "ASSIGNED", reviewer_id, None, correlation_id
            )
        review = await self._transition(
            session, review, "APPROVED", reviewer_id, notes, correlation_id
        )
        return await self._transition(
            session, review, "RESOLVED", reviewer_id, None, correlation_id
        )

    async def reject(
        self,
        session: AsyncSession,
        review: HumanReview,
        reviewer_id: uuid.UUID,
        reason: str | None,
        correlation_id: str,
    ) -> HumanReview:
        """Reject a OPEN/ASSIGNED review → (ASSIGNED →) REJECTED → RESOLVED.

        If the review is still OPEN, it is first transitioned to ASSIGNED
        before being REJECTED, following the defined lifecycle:
          OPEN → ASSIGNED → REJECTED → RESOLVED
        """
        if review.status == "OPEN":
            review = await self._transition(
                session, review, "ASSIGNED", reviewer_id, None, correlation_id
            )
        review = await self._transition(
            session, review, "REJECTED", reviewer_id, reason, correlation_id
        )
        return await self._transition(
            session, review, "RESOLVED", reviewer_id, None, correlation_id
        )

    async def _transition(
        self,
        session: AsyncSession,
        review: HumanReview,
        to_status: str,
        actor_id: uuid.UUID,
        notes: str | None,
        correlation_id: str,
    ) -> HumanReview:
        from_status = review.status
        allowed = ALLOWED_REVIEW_TRANSITIONS.get(from_status, frozenset())
        if to_status not in allowed:
            raise ValueError(f"Invalid review transition: {from_status} → {to_status}")

        now = datetime.now(timezone.utc)
        review.status = to_status
        review.updated_at = now

        if to_status == "ASSIGNED":
            review.assigned_to = actor_id
        elif to_status in ("APPROVED", "REJECTED", "STOPPED"):
            review.reviewed_by = actor_id
            review.review_notes = notes
        elif to_status == "RESOLVED":
            review.resolved_at = now

        audit = AuditEvent(
            id=uuid.uuid4(),
            merchant_id=review.merchant_id,
            case_id=review.case_id,
            actor=str(actor_id),
            action_type="human_review_transition",
            affected_entity_type="HumanReview",
            affected_entity_id=review.id,
            from_state=from_status,
            to_state=to_status,
            correlation_id=_parse_uuid(correlation_id),
            payload={"review_id": str(review.id), "notes": notes or "", "result": to_status},
            created_at=now,
        )
        session.add(audit)
        await session.flush()

        logger.info(
            "human_review_transition",
            review_id=str(review.id),
            from_status=from_status,
            to_status=to_status,
            actor_id=str(actor_id),
        )

        return review


def _parse_uuid(s: str) -> uuid.UUID:
    try:
        return uuid.UUID(s)
    except (ValueError, AttributeError):
        return uuid.uuid4()
