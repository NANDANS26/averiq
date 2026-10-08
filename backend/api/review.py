"""Human review queue API endpoints.

Exposes:
  GET  /api/v1/review                              — pending review queue (OPERATOR+)
  POST /api/v1/interventions/{id}/approve          — approve a human-review intervention
  POST /api/v1/interventions/{id}/reject           — reject a human-review intervention

OPERATOR+ roles can approve or reject interventions that require human approval.
Reviewer actions do NOT mutate the terminal state of the associated case — the
execution engine handles state transitions based on review outcomes.

Requirements: FR-POLICY-007
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import CurrentUser, require_operator
from backend.database import get_session
from backend.domain.models import HumanReview, RevenueRiskCase
from backend.policy_engine.human_review import HumanReviewManager
from backend.state_machine.machine import StateMachine
from backend.worker import celery_app

router = APIRouter()
manager = HumanReviewManager()


class ReviewActionRequest(BaseModel):
    notes: str | None = None


@router.get("/review", summary="Pending human review queue")
async def get_pending_reviews(
    current_user: Annotated[CurrentUser, Depends(require_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    """Return OPEN and ASSIGNED reviews for the authenticated merchant.

    Requires OPERATOR or higher role (FR-POLICY-007).
    Results are ordered by creation time ascending (oldest first).
    """
    reviews = await manager.get_pending_reviews(session, current_user.merchant_id)
    return [
        {
            "id": str(r.id),
            "case_id": str(r.case_id),
            "intervention_id": str(r.intervention_id),
            "status": r.status,
            "created_at": r.created_at.isoformat(),
            "review_notes": r.review_notes,
        }
        for r in reviews
    ]


@router.post(
    "/interventions/{intervention_id}/approve",
    status_code=status.HTTP_200_OK,
    summary="Approve a human-review intervention",
)
async def approve_intervention(
    intervention_id: uuid.UUID,
    body: ReviewActionRequest,
    current_user: Annotated[CurrentUser, Depends(require_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Approve an intervention pending human review.

    Transitions: OPEN/ASSIGNED → APPROVED → RESOLVED.
    Reviewer actions do NOT directly mutate the associated case terminal state.
    Requires OPERATOR or higher role (FR-POLICY-007).
    """
    review = await _get_review_for_intervention(
        session, intervention_id, current_user.merchant_id
    )
    corr_id = str(uuid.uuid4())
    updated = await manager.approve(
        session,
        review,
        current_user.user_id,
        body.notes,
        corr_id,
    )

    # Transition case to ACTION_APPROVED
    case_res = await session.execute(
        select(RevenueRiskCase).where(RevenueRiskCase.id == review.case_id).with_for_update()
    )
    case = case_res.scalar_one_or_none()
    if case and case.state == "ACTION_PENDING":
        sm = StateMachine()
        await sm.transition(
            session,
            case_id=case.id,
            to_state="ACTION_APPROVED",
            actor=str(current_user.user_id),
            reason=f"Human review approved: {body.notes or 'No notes'}",
            correlation_id=corr_id,
        )

        celery_app.send_task(
            "backend.execution_engine.tasks.execute_intervention",
            args=[str(case.id), str(intervention_id), str(current_user.merchant_id)],
            queue="default",
            headers={"correlation_id": corr_id},
        )

    return {"status": "approved", "review_id": str(updated.id)}


@router.post(
    "/interventions/{intervention_id}/reject",
    status_code=status.HTTP_200_OK,
    summary="Reject a human-review intervention",
)
async def reject_intervention(
    intervention_id: uuid.UUID,
    body: ReviewActionRequest,
    current_user: Annotated[CurrentUser, Depends(require_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Reject an intervention pending human review.

    Transitions: OPEN/ASSIGNED → REJECTED → RESOLVED.
    Case transitions to STOPPED.
    Requires OPERATOR or higher role (FR-POLICY-007).
    """
    review = await _get_review_for_intervention(
        session, intervention_id, current_user.merchant_id
    )
    corr_id = str(uuid.uuid4())
    updated = await manager.reject(
        session,
        review,
        current_user.user_id,
        body.notes,
        corr_id,
    )

    # Transition case to STOPPED
    case_res = await session.execute(
        select(RevenueRiskCase).where(RevenueRiskCase.id == review.case_id).with_for_update()
    )
    case = case_res.scalar_one_or_none()
    if case and case.state in ("ACTION_PENDING", "ACTION_APPROVED"):
        sm = StateMachine()
        await sm.transition(
            session,
            case_id=case.id,
            to_state="STOPPED",
            actor=str(current_user.user_id),
            reason=f"Human review rejected: {body.notes or 'No notes'}",
            correlation_id=corr_id,
        )

    return {"status": "rejected", "review_id": str(updated.id)}


async def _get_review_for_intervention(
    session: AsyncSession,
    intervention_id: uuid.UUID,
    merchant_id: uuid.UUID,
) -> HumanReview:
    """Load the HumanReview for the given intervention, scoped to the merchant.

    Raises HTTP 404 if no review exists for the intervention within the
    authenticated merchant's scope (prevents cross-merchant information leakage
    — returns 404 rather than 403 to avoid enumeration).
    """
    stmt = (
        select(HumanReview)
        .where(
            HumanReview.intervention_id == intervention_id,
            HumanReview.merchant_id == merchant_id,
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    review = result.scalar_one_or_none()
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="HumanReview not found",
        )
    return review
