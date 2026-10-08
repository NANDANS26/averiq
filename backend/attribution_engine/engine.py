"""AttributionEngine — classifies recovered payments by attribution class.

IMPORTANT CAUTION (required in spec):
  Temporal proximity (recovery within window after intervention) is NOT causal proof.
  DIRECT_INTERVENTION means: "Averiq executed an intervention, and recovery occurred
  within the attribution window". NOT "Averiq caused the recovery".
  This is incremental measurement relative to a defined baseline, not causal inference.

Attribution classes:
  DIRECT_INTERVENTION:  Recovery within attribution_window_seconds after Averiq
                        executable intervention (CREATE_PAYMENT_LINK, etc.).
  ORGANIC:              Recovery without any Averiq intervention within window.
  UNKNOWN_ATTRIBUTION:  Ambiguous signals.

Requirements: FR-ATTR-001, FR-ATTR-002, FR-ATTR-005, FR-ATTR-006
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.models import ExternalOperation
from shared.contracts.enums import AttributionClass

logger = structlog.get_logger(__name__)

# Actions that count as "Averiq interventions" for attribution.
# RECOMMENDATION_ONLY (NO_OP) does NOT count — no external gateway call.
EXECUTABLE_ACTION_TYPES: frozenset[str] = frozenset({
    "CREATE_PAYMENT_LINK",
    "RETRY_CHARGE",
    "PAUSE_SUBSCRIPTION",
})

DEFAULT_ATTRIBUTION_WINDOW_SECONDS: int = 86_400  # 24 hours


@dataclass(frozen=True)
class AttributionResult:
    attribution_class: AttributionClass
    intervention_id: uuid.UUID | None
    action_type: str | None
    recovered_at: datetime
    intervention_executed_at: datetime | None
    attribution_window_seconds: int
    # CAUTION: temporal proximity != causation (see module docstring)


class AttributionEngine:
    """Classifies payment recovery by attribution. Temporal proximity ≠ causation."""

    async def classify(
        self,
        session: AsyncSession,
        case_id: uuid.UUID,
        merchant_id: uuid.UUID,
        recovered_at: datetime,
        attribution_window_seconds: int = DEFAULT_ATTRIBUTION_WINDOW_SECONDS,
    ) -> AttributionResult:
        window_start = recovered_at - timedelta(seconds=attribution_window_seconds)

        stmt = (
            select(ExternalOperation)
            .where(
                ExternalOperation.case_id == case_id,
                ExternalOperation.status == "SUCCEEDED",
                ExternalOperation.action_type.in_(list(EXECUTABLE_ACTION_TYPES)),
                ExternalOperation.completed_at >= window_start,
                ExternalOperation.completed_at <= recovered_at,
            )
            .order_by(ExternalOperation.completed_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        op = result.scalar_one_or_none()

        if op is not None:
            # CAUTION: temporal proximity, not causation
            logger.info("attribution.direct_intervention",
                       case_id=str(case_id), action_type=op.action_type,
                       caution="temporal_proximity_not_causation")
            return AttributionResult(
                attribution_class=AttributionClass.DIRECT_INTERVENTION,
                intervention_id=op.intervention_id,
                action_type=op.action_type,
                recovered_at=recovered_at,
                intervention_executed_at=op.completed_at,
                attribution_window_seconds=attribution_window_seconds,
            )

        return AttributionResult(
            attribution_class=AttributionClass.ORGANIC,
            intervention_id=None,
            action_type=None,
            recovered_at=recovered_at,
            intervention_executed_at=None,
            attribution_window_seconds=attribution_window_seconds,
        )
