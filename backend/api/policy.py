"""Policy lifecycle API endpoints.

GET  /api/v1/policies          — list policies (VIEWER+)
GET  /api/v1/policies/active   — get active policy (VIEWER+)
GET  /api/v1/policies/{id}     — single policy detail (VIEWER+)
POST /api/v1/policies          — create draft policy (ADMIN)
POST /api/v1/policies/{id}/activate — promote to active (ADMIN)

All amounts in policy configuration must be integers (paise), never float.
Requirements: FR-POLICY-012, FR-POLICY-013
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import CurrentUser, require_admin, require_viewer
from backend.database import get_session
from backend.domain.models import Policy
from backend.policy_engine.lifecycle import PolicyLifecycleError, PolicyLifecycleManager

router = APIRouter()
manager = PolicyLifecycleManager()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class PolicyOut(BaseModel):
    """Serialised policy record.

    All amounts in ``configuration`` are integer paise (minor currency units),
    never float.  The ``lifecycle_state`` field represents the current stage
    in the draft → validated → active → retired lifecycle.
    """

    id: str
    merchant_id: str
    version: int
    lifecycle_state: str
    configuration: dict
    created_at: str


class CreatePolicyRequest(BaseModel):
    """Request body for creating a new draft policy.

    The ``configuration`` dict must contain all threshold values as integers
    (paise) where monetary amounts are involved.  No float values are
    permitted for monetary fields.
    """

    configuration: dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _policy_to_out(p: Policy) -> PolicyOut:
    """Serialise a Policy ORM object to the API response model."""
    return PolicyOut(
        id=str(p.id),
        merchant_id=str(p.merchant_id),
        version=p.version,
        lifecycle_state=p.lifecycle_state,
        configuration=p.configuration or {},
        created_at=p.created_at.isoformat(),
    )


async def _get_policy_scoped(
    session: AsyncSession,
    policy_id: uuid.UUID,
    merchant_id: uuid.UUID,
) -> Policy:
    """Load a policy by ID, enforcing merchant isolation (SEC-ISOL-002).

    Raises:
        HTTP 404 — if the policy does not exist or belongs to another merchant.
    """
    result = await session.execute(
        select(Policy).where(
            Policy.id == policy_id,
            Policy.merchant_id == merchant_id,
        )
    )
    p = result.scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    return p


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/policies", response_model=list[PolicyOut])
async def list_policies(
    current_user: Annotated[CurrentUser, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[PolicyOut]:
    """Return the 50 most recent policies for the authenticated merchant.

    Results are ordered by creation time descending.
    Requirements: FR-POLICY-012
    """
    result = await session.execute(
        select(Policy)
        .where(Policy.merchant_id == current_user.merchant_id)
        .order_by(Policy.created_at.desc())
        .limit(50)
    )
    return [_policy_to_out(p) for p in result.scalars().all()]


@router.get("/policies/active", response_model=PolicyOut | None)
async def get_active_policy(
    current_user: Annotated[CurrentUser, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PolicyOut | None:
    """Return the currently active policy for the authenticated merchant.

    Returns ``null`` / HTTP 200 when no active policy exists for the merchant.
    Requirements: FR-POLICY-012
    """
    result = await session.execute(
        select(Policy)
        .where(
            Policy.merchant_id == current_user.merchant_id,
            Policy.lifecycle_state == "active",
        )
        .limit(1)
    )
    p = result.scalar_one_or_none()
    if p is None:
        return None
    return _policy_to_out(p)


@router.get("/policies/{policy_id}", response_model=PolicyOut)
async def get_policy(
    policy_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PolicyOut:
    """Return a single policy by ID, scoped to the authenticated merchant.

    Requirements: FR-POLICY-012, SEC-ISOL-002
    """
    p = await _get_policy_scoped(session, policy_id, current_user.merchant_id)
    return _policy_to_out(p)


@router.post("/policies", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
async def create_policy(
    body: CreatePolicyRequest,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PolicyOut:
    """Create a new draft policy for the authenticated merchant.

    Version numbers are assigned automatically, incrementing from the current
    highest version for the merchant.  All new policies start in ``draft``
    lifecycle state.

    All monetary threshold values in ``configuration`` must be integers (paise).
    Float values for monetary fields are a hard constraint violation.

    Requirements: FR-POLICY-012, FR-POLICY-013
    """
    # Determine next version number
    result = await session.execute(
        select(Policy)
        .where(Policy.merchant_id == current_user.merchant_id)
        .order_by(Policy.version.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    next_version = (latest.version + 1) if latest else 1

    now = datetime.now(timezone.utc)
    p = Policy(
        id=uuid.uuid4(),
        merchant_id=current_user.merchant_id,
        version=next_version,
        lifecycle_state="draft",
        configuration=body.configuration,
        created_at=now,
    )
    session.add(p)
    await session.flush()
    return _policy_to_out(p)


@router.post("/policies/{policy_id}/activate", response_model=PolicyOut)
async def activate_policy(
    policy_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PolicyOut:
    """Promote a draft or validated policy to active.

    Performs the full lifecycle path automatically:
      - draft → validated (runs all rule-set validation checks)
      - validated → active (retires any currently active policy atomically)

    If the policy is already validated, only the second transition is applied.

    Raises:
        HTTP 400 — if policy validation fails (e.g. negative thresholds).
        HTTP 409 — if the policy is already active or in an invalid state.
        HTTP 404 — if the policy does not exist in the merchant scope.

    Requirements: FR-POLICY-012, FR-POLICY-013
    """
    p = await _get_policy_scoped(session, policy_id, current_user.merchant_id)
    correlation_id = str(uuid.uuid4())
    actor = str(current_user.user_id)

    try:
        if p.lifecycle_state == "draft":
            p = await manager.transition(
                session, p, "validated", actor, "API activation request", correlation_id
            )
        if p.lifecycle_state == "validated":
            p = await manager.transition(
                session, p, "active", actor, "API activation request", correlation_id
            )
    except PolicyLifecycleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return _policy_to_out(p)
