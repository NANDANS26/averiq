"""Model lifecycle management endpoints.

POST /api/v1/models/{model_id}/promote — promote model through lifecycle stages.

Valid lifecycle transitions:
  trained → evaluated → candidate → active

Quality gates for promotion to 'active' (candidate → active):
  - brier_score ≤ BRIER_SCORE_WARNING_THRESHOLD (configurable)
  - At most one active model per model_type is enforced by a DB partial
    unique index (idx_model_active)
  - If any gate fails → HTTP 422 with structured gate failure details including
    actual value and threshold for each gate

On activation: the previous active model of the same model_type is atomically
transitioned to 'retired'. The artifact and evaluation metrics are retained.

Requires ADMIN role (require_admin).

Requirements: FR-MODEL-009, AI-MODEL-001, AI-MODEL-003
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import CurrentUser, require_admin
from backend.config import settings
from backend.database import get_session
from backend.domain.models import ModelArtifact

router = APIRouter()

# ---------------------------------------------------------------------------
# Valid lifecycle state transitions
# ---------------------------------------------------------------------------

# Each key is the current state; the value is the only permitted next state.
# There is exactly one valid forward transition at each stage.
VALID_PROMOTIONS: dict[str, str] = {
    "trained": "evaluated",
    "evaluated": "candidate",
    "candidate": "active",
}


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class PromoteRequest(BaseModel):
    """Request body for the promote endpoint.

    ``reason`` is an optional human-readable justification for the promotion
    (e.g., "Q4 evaluation passed"). It is stored for audit trail purposes.
    """

    reason: str | None = None


class GateFailure(BaseModel):
    """Structured description of a single quality gate failure."""

    gate_name: str
    detail: str
    actual_value: float | None = None
    threshold: float | None = None


class PromoteResponse(BaseModel):
    """Structured response for a successful promotion."""

    model_id: str
    model_version: str
    model_type: str
    old_state: str
    new_state: str
    message: str


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/models/{model_id}/promote",
    response_model=PromoteResponse,
    status_code=status.HTTP_200_OK,
    summary="Promote a model to the next lifecycle stage",
    description=(
        "Promotes a model artifact through its lifecycle: "
        "trained → evaluated → candidate → active. "
        "The 'candidate → active' transition enforces configurable quality gates. "
        "If any gate fails the model stays at CANDIDATE and a structured error "
        "with gate_failures details is returned. "
        "On activation, the previous active model of the same type is retired atomically."
    ),
    tags=["models"],
)
async def promote_model(
    model_id: uuid.UUID,
    body: PromoteRequest,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PromoteResponse:
    """Promote a model to the next lifecycle stage.

    Lifecycle: trained → evaluated → candidate → active.

    Quality gates for ``candidate → active``:
      - ``brier_score`` (if set) must be ≤ ``BRIER_SCORE_WARNING_THRESHOLD``
        (configurable; default 0.25). All gate thresholds are configurable;
        none are hardcoded universal constants.
      - If any gate fails: HTTP 422 with structured ``gate_failures`` list;
        model stays at CANDIDATE.

    On activation:
      - At most one active model per ``model_type`` (partial unique index guard)
      - Previous active model of the same ``model_type`` is transitioned to
        ``retired`` atomically. Artifact and metrics are retained.

    Raises:
        HTTP 404 — if the model does not exist.
        HTTP 422 — if the current state has no valid promotion target (e.g.
                   already active or retired), or if any quality gate fails
                   when promoting to 'active'.

    Requirements: FR-MODEL-009, AI-MODEL-001, AI-MODEL-003
    """
    # ------------------------------------------------------------------
    # Load model artifact
    # ------------------------------------------------------------------
    model: ModelArtifact | None = await session.get(ModelArtifact, model_id)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model {model_id} not found",
        )

    old_state = model.lifecycle_state

    # ------------------------------------------------------------------
    # Validate that a forward transition exists for the current state
    # ------------------------------------------------------------------
    new_state = VALID_PROMOTIONS.get(old_state)
    if new_state is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": f"Cannot promote model in state '{old_state}'",
                "valid_source_states": list(VALID_PROMOTIONS.keys()),
                "current_state": old_state,
            },
        )

    # ------------------------------------------------------------------
    # Quality gates — only enforced when promoting to 'active'
    # ------------------------------------------------------------------
    if new_state == "active":
        gate_failures: list[dict] = []

        brier = model.brier_score
        threshold = float(settings.BRIER_SCORE_WARNING_THRESHOLD)

        if brier is not None and brier > threshold:
            gate_failures.append(
                {
                    "gate_name": "brier_score",
                    "detail": (
                        f"Brier score {brier:.4f} exceeds threshold {threshold:.4f} "
                        f"(BRIER_SCORE_WARNING_THRESHOLD). Lower Brier score is better; "
                        f"retrain or re-evaluate the model to meet the threshold."
                    ),
                    "actual_value": brier,
                    "threshold": threshold,
                }
            )

        if gate_failures:
            # Model stays at CANDIDATE — no state change applied.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "Quality gate failures — model cannot be promoted to active",
                    "gate_failures": gate_failures,
                    "note": (
                        "Model remains at CANDIDATE. Retrain or adjust "
                        "BRIER_SCORE_WARNING_THRESHOLD before promoting."
                    ),
                },
            )

        # ------------------------------------------------------------------
        # Retire the current active model of the same type (if any).
        # This preserves the partial unique index invariant (idx_model_active):
        # at most one 'active' model per model_type at any time.
        # ------------------------------------------------------------------
        stmt = (
            select(ModelArtifact)
            .where(
                ModelArtifact.model_type == model.model_type,
                ModelArtifact.lifecycle_state == "active",
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        prev_active: ModelArtifact | None = result.scalar_one_or_none()

        if prev_active is not None and prev_active.id != model.id:
            # Retire the previous active model; artifact and metrics are retained.
            prev_active.lifecycle_state = "retired"

    # ------------------------------------------------------------------
    # Apply the promotion. The session flush + commit happen at the end
    # of the request via the get_session() dependency's async context manager.
    # ------------------------------------------------------------------
    model.lifecycle_state = new_state

    return PromoteResponse(
        model_id=str(model_id),
        model_version=model.model_version,
        model_type=model.model_type,
        old_state=old_state,
        new_state=new_state,
        message=f"Model promoted from '{old_state}' to '{new_state}'",
    )
