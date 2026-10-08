"""Core case and intervention REST API endpoints.

All endpoints require VIEWER+ role and are scoped to authenticated merchant_id.
AuditEvent responses NEVER include raw gateway payloads, card numbers, or secrets (AUD-006).

Endpoints:
  GET /api/v1/cases                          — paginated cursor list
  GET /api/v1/cases/{case_id}                — full case detail
  GET /api/v1/cases/{case_id}/trace          — decision trace
  GET /api/v1/cases/{case_id}/audit          — audit history (safe subset only)
  GET /api/v1/cases/{case_id}/interventions  — interventions for case
  GET /api/v1/interventions/{intervention_id} — single intervention detail

Requirements: FR-STATE-001, AUD-002, AUD-006, API-001, API-002, API-004, DEC-TRACE-001–005
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import CurrentUser, require_viewer
from backend.database import get_session
from backend.domain.models import (
    AuditEvent,
    ExternalOperation,
    Intervention,
    RevenueRiskCase,
    VerificationAttempt,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class CaseListItem(BaseModel):
    """Compact case representation for the paginated list."""

    id: str
    payment_id: str | None = None
    state: str
    payment_amount_at_risk: int
    currency: str
    exposure_tier: str | None
    recovery_risk_tier: str | None = None
    failure_category: str | None
    recovery_probability: float | None = None
    created_at: str
    updated_at: str


class CaseDetail(BaseModel):
    """Full case detail returned by GET /cases/{case_id}."""

    id: str
    merchant_id: str
    payment_id: str
    state: str
    payment_amount_at_risk: int
    currency: str
    projected_subscription_revenue_at_risk: int | None
    risk_calculation_path: str
    exposure_tier: str | None
    recovery_risk_tier: str | None
    failure_category: str | None
    confidence_score: float | None  # maps to diagnosis_confidence on the ORM
    ai_source: str | None
    operating_mode: str
    is_demo_fixture: bool
    created_at: str
    updated_at: str


class AuditEventItem(BaseModel):
    """Safe audit response — the ``payload`` JSONB column is intentionally omitted.

    Per AUD-006, raw gateway response payloads, card numbers, bank account
    numbers, and any other PII must never be exposed via the audit API.
    Only normalised, non-sensitive fields are returned here.
    """

    id: str
    actor: str
    action_type: str
    from_state: str | None
    to_state: str | None
    policy_version: str | None
    model_version: str | None
    correlation_id: str
    created_at: str


class CursorPage(BaseModel):
    """Cursor-based pagination envelope."""

    items: list[Any]
    total: int | None
    next_cursor: str | None


# ---------------------------------------------------------------------------
# Helper — merchant-scoped case fetch
# ---------------------------------------------------------------------------


async def _get_case_scoped(
    session: AsyncSession,
    case_id: uuid.UUID,
    merchant_id: uuid.UUID,
) -> RevenueRiskCase:
    """Fetch a RevenueRiskCase and enforce merchant scope.

    Raises HTTP 404 when the case does not exist or belongs to a different
    merchant (SEC-ISOL-002 — same response shape prevents merchant enumeration).
    """
    result = await session.execute(
        select(RevenueRiskCase).where(
            RevenueRiskCase.id == case_id,
            RevenueRiskCase.merchant_id == merchant_id,
        )
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


# ---------------------------------------------------------------------------
# GET /cases — paginated cursor-based list
# ---------------------------------------------------------------------------


@router.get(
    "/cases",
    response_model=CursorPage,
    summary="List revenue risk cases (cursor-paginated)",
    tags=["cases"],
)
async def list_cases(
    current_user: Annotated[CurrentUser, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    state: str | None = Query(None, description="Filter by case state"),
    exposure_tier: str | None = Query(None, description="Filter by exposure tier"),
    recovery_risk_tier: str | None = Query(None, description="Filter by recovery risk tier"),
    failure_category: str | None = Query(None, description="Filter by failure category"),
    search: str | None = Query(None, description="Search by case ID or payment ID"),
    sort_by: str = Query("created_at_desc", description="Sort by created_at_desc, created_at_asc, amount_desc, amount_asc"),
    limit: int = Query(50, ge=1, le=200, description="Page size (1–200)"),
    cursor: str | None = Query(None, description="Opaque cursor from previous page"),
) -> CursorPage:
    """Return a merchant-scoped, cursor-paginated list of RevenueRiskCase records.

    Filters:
      - ``state``              — exact match (e.g. "OBSERVED", "RECOVERED")
      - ``exposure_tier``       — exact match (e.g. "HIGH", "MEDIUM", "LOW")
      - ``recovery_risk_tier``  — exact match (e.g. "HIGH", "MEDIUM", "LOW")
      - ``failure_category``    — exact match
      - ``search``              — partial match on case ID or payment ID

    Pagination & Sorting:
      - Supports sort_by: created_at_desc, created_at_asc, amount_desc, amount_asc.
      - Next cursor points to next page.

    Requirements: FR-STATE-001, SEC-ISOL-001
    """
    stmt = select(RevenueRiskCase).where(
        RevenueRiskCase.merchant_id == current_user.merchant_id
    )

    if state:
        stmt = stmt.where(RevenueRiskCase.state == state)
    if exposure_tier:
        stmt = stmt.where(RevenueRiskCase.exposure_tier == exposure_tier)
    if recovery_risk_tier and not hasattr(recovery_risk_tier, "default"):
        stmt = stmt.where(RevenueRiskCase.recovery_risk_tier == recovery_risk_tier)
    if failure_category and not hasattr(failure_category, "default"):
        stmt = stmt.where(RevenueRiskCase.failure_category == failure_category)
    if search and isinstance(search, str):
        search_term = search.strip()
        try:
            search_uuid = uuid.UUID(search_term)
            stmt = stmt.where(
                (RevenueRiskCase.id == search_uuid) | (RevenueRiskCase.payment_id == search_uuid)
            )
        except ValueError:
            pass

    # Cursor-based pagination: advance past the last seen id
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
            stmt = stmt.where(RevenueRiskCase.id > cursor_uuid)
        except ValueError:
            # Ignore malformed cursor — start from the beginning
            pass

    if sort_by == "amount_desc":
        stmt = stmt.order_by(desc(RevenueRiskCase.payment_amount_at_risk), desc(RevenueRiskCase.created_at))
    elif sort_by == "amount_asc":
        stmt = stmt.order_by(RevenueRiskCase.payment_amount_at_risk.asc(), desc(RevenueRiskCase.created_at))
    elif sort_by == "created_at_asc":
        stmt = stmt.order_by(RevenueRiskCase.created_at.asc())
    else:
        stmt = stmt.order_by(desc(RevenueRiskCase.created_at))

    stmt = stmt.limit(limit + 1)

    result = await session.execute(stmt)
    cases = list(result.scalars().all())

    has_next = len(cases) > limit
    items = cases[:limit]

    return CursorPage(
        items=[
            CaseListItem(
                id=str(c.id),
                payment_id=str(c.payment_id) if c.payment_id else None,
                state=c.state,
                payment_amount_at_risk=int(c.payment_amount_at_risk),
                currency=c.currency,
                exposure_tier=c.exposure_tier,
                recovery_risk_tier=c.recovery_risk_tier,
                failure_category=c.failure_category,
                recovery_probability=c.recovery_probability,
                created_at=c.created_at.isoformat(),
                updated_at=c.updated_at.isoformat(),
            )
            for c in items
        ],
        total=None,
        next_cursor=str(items[-1].id) if has_next and items else None,
    )


# ---------------------------------------------------------------------------
# GET /cases/{case_id} — full case detail
# ---------------------------------------------------------------------------


@router.get(
    "/cases/{case_id}",
    response_model=CaseDetail,
    summary="Get full case detail",
    tags=["cases"],
)
async def get_case(
    case_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseDetail:
    """Return full detail for a single RevenueRiskCase scoped to the authenticated merchant.

    Returns HTTP 404 when the case does not exist or belongs to another merchant.

    Requirements: API-002, SEC-ISOL-002
    """
    c = await _get_case_scoped(session, case_id, current_user.merchant_id)
    return CaseDetail(
        id=str(c.id),
        merchant_id=str(c.merchant_id),
        payment_id=str(c.payment_id),
        state=c.state,
        payment_amount_at_risk=int(c.payment_amount_at_risk),
        currency=c.currency,
        projected_subscription_revenue_at_risk=(
            int(c.projected_subscription_revenue_at_risk)
            if c.projected_subscription_revenue_at_risk is not None
            else None
        ),
        risk_calculation_path=c.risk_calculation_path,
        exposure_tier=c.exposure_tier,
        recovery_risk_tier=c.recovery_risk_tier,
        failure_category=c.failure_category,
        # diagnosis_confidence is the ORM field; exposed as confidence_score in the API
        confidence_score=c.diagnosis_confidence,
        ai_source=c.ai_source,
        operating_mode=c.operating_mode,
        is_demo_fixture=c.is_demo_fixture,
        created_at=c.created_at.isoformat(),
        updated_at=c.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /cases/{case_id}/trace — decision trace (DEC-TRACE-001–005)
# ---------------------------------------------------------------------------


@router.get(
    "/cases/{case_id}/trace",
    summary="Get decision trace for a case",
    tags=["cases"],
)
async def get_case_trace(
    case_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Return the full DecisionTrace for a case.

    The trace includes:
      - Failure summary (current state, failure_category)
      - AI source (StubAIProvider / ExternalLLMProvider / DeterministicFallback)
      - Ordered audit events showing every state transition and decision

    No raw gateway payloads are included (AUD-006).

    Requirements: API-004, DEC-TRACE-001, DEC-TRACE-002, DEC-TRACE-003,
                  DEC-TRACE-004, DEC-TRACE-005
    """
    c = await _get_case_scoped(session, case_id, current_user.merchant_id)

    result = await session.execute(
        select(AuditEvent)
        .where(
            AuditEvent.case_id == case_id,
            AuditEvent.merchant_id == current_user.merchant_id,
        )
        .order_by(AuditEvent.created_at.asc())
        .limit(200)
    )
    events = result.scalars().all()

    # Fetch interventions for candidates & policy evaluations
    intv_res = await session.execute(
        select(Intervention)
        .where(
            Intervention.case_id == case_id,
            Intervention.merchant_id == current_user.merchant_id,
        )
        .order_by(Intervention.expected_recovery_value.desc().nullslast(), Intervention.created_at.desc())
    )
    interventions = intv_res.scalars().all()

    candidates_data = []
    for intv in interventions:
        blocking_rules = []
        if intv.policy_blocking_rules and isinstance(intv.policy_blocking_rules, dict):
            blocking_rules = intv.policy_blocking_rules.get("rules", [])
        candidates_data.append({
            "intervention_type": intv.intervention_type,
            "expected_recovery_value": intv.expected_recovery_value,
            "execution_capability": intv.execution_capability,
            "policy_decision": intv.policy_decision,
            "blocking_rules": blocking_rules,
            "evaluated_values": intv.policy_evaluated_values,
        })

    # Fetch latest external operation
    ext_op_res = await session.execute(
        select(ExternalOperation)
        .where(
            ExternalOperation.case_id == case_id,
            ExternalOperation.merchant_id == current_user.merchant_id,
        )
        .order_by(ExternalOperation.created_at.desc())
        .limit(1)
    )
    latest_op = ext_op_res.scalar_one_or_none()

    execution_result = None
    verification_outcome = None
    if latest_op:
        execution_result = {
            "external_operation_id": latest_op.external_operation_id,
            "status": latest_op.status,
            "action_id": str(latest_op.action_id),
        }
        # Fetch latest verification attempt outcome
        ver_res = await session.execute(
            select(VerificationAttempt)
            .where(VerificationAttempt.external_operation_id == latest_op.id)
            .order_by(VerificationAttempt.attempt_number.desc())
            .limit(1)
        )
        latest_ver = ver_res.scalar_one_or_none()
        if latest_ver and latest_ver.outcome:
            verification_outcome = latest_ver.outcome

    # Extract metadata for fallback / timeout and decision trace
    meta = c.case_metadata or {}
    dt = meta.get("decision_trace", {})

    # Build mathematical explainability details
    p0 = dt.get("baseline_recovery_probability", 0.35)
    pref_action = dt.get("model_preferred_action", "NONE")
    reason_codes = dt.get("reason_codes", [])

    candidate_math = []
    for cand in dt.get("candidate_evaluations", []):
        act = cand.get("action")
        prob = cand.get("probability", 0.0)
        uplift = cand.get("incremental_uplift", 0.0)
        cost_p = cand.get("cost_paise", 0)
        net_ev_p = cand.get("expected_net_value_paise", 0)
        candidate_math.append({
            "action": act,
            "P_action": prob,
            "P0_baseline": p0,
            "incremental_uplift": uplift,
            "cost_paise": cost_p,
            "expected_net_recovery_value_paise": net_ev_p,
            "formula": f"floor(max(0, {prob:.4f} - {p0:.4f}) × {c.payment_amount_at_risk}) - {cost_p}",
        })

    explanation_text = (
        f"Averiq evaluated recovery for {c.payment_amount_at_risk} paise. "
        f"Baseline organic recoverability P0 was {p0:.2f}. "
        f"Preferred intervention is {pref_action} based on incremental expected net value."
    )

    return {
        "case_id": str(case_id),
        "current_state": c.state,
        "failure_category": c.failure_category,
        "ai_source": c.ai_source,
        "confidence_score": c.diagnosis_confidence,
        "fallback_reason": meta.get("fallback_reason"),
        "ai_timeout": meta.get("ai_timeout", False),
        "recovery_probability": c.recovery_probability,
        "explainability": {
            "baseline_recovery_probability_p0": p0,
            "model_preferred_action": pref_action,
            "reason_codes": reason_codes,
            "explanation_text": explanation_text,
            "mathematical_evaluations": candidate_math,
        },
        "candidates": candidates_data,
        "execution_result": execution_result,
        "verification_outcome": verification_outcome or (c.state if c.state == "RECOVERED" else None),
        "events": [
            {
                "actor": e.actor,
                "action_type": e.action_type,
                "from_state": e.from_state,
                "to_state": e.to_state,
                # DEC-TRACE-003: policy blocking rules visible via action_type
                "policy_version": e.policy_version,
                "model_version": e.model_version,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
    }


# ---------------------------------------------------------------------------
# GET /cases/{case_id}/audit — audit history (AUD-006 safe)
# ---------------------------------------------------------------------------


@router.get(
    "/cases/{case_id}/audit",
    response_model=list[AuditEventItem],
    summary="Get audit event history for a case",
    tags=["cases"],
)
async def get_case_audit(
    case_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(100, ge=1, le=500, description="Maximum events to return"),
) -> list[AuditEventItem]:
    """Return the ordered audit event history for a case.

    The ``payload`` JSONB column is intentionally omitted from responses to
    prevent exposure of raw gateway payloads, PII, or card data (AUD-006).

    Results are ordered chronologically (oldest first) to allow replaying
    the full decision history in order.

    Requirements: AUD-002, AUD-004, AUD-006, SEC-ISOL-001
    """
    # Confirm case belongs to this merchant before reading its audit events
    await _get_case_scoped(session, case_id, current_user.merchant_id)

    result = await session.execute(
        select(AuditEvent)
        .where(
            AuditEvent.case_id == case_id,
            AuditEvent.merchant_id == current_user.merchant_id,
        )
        .order_by(AuditEvent.created_at.asc())
        .limit(limit)
    )

    return [
        AuditEventItem(
            id=str(e.id),
            actor=e.actor,
            action_type=e.action_type,
            from_state=e.from_state,
            to_state=e.to_state,
            policy_version=e.policy_version,
            model_version=e.model_version,
            correlation_id=str(e.correlation_id),
            created_at=e.created_at.isoformat(),
        )
        for e in result.scalars().all()
    ]


# ---------------------------------------------------------------------------
# GET /cases/{case_id}/interventions — interventions for a case
# ---------------------------------------------------------------------------


@router.get(
    "/cases/{case_id}/interventions",
    summary="List interventions for a case",
    tags=["cases"],
)
async def get_case_interventions(
    case_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, Any]]:
    """Return all interventions associated with a RevenueRiskCase.

    Results are ordered by ``created_at DESC`` (newest first).

    Requirements: API-002, SEC-ISOL-001
    """
    # Confirm case belongs to this merchant
    await _get_case_scoped(session, case_id, current_user.merchant_id)

    result = await session.execute(
        select(Intervention)
        .where(
            Intervention.case_id == case_id,
            Intervention.merchant_id == current_user.merchant_id,
        )
        .order_by(Intervention.created_at.desc())
    )

    return [
        {
            "id": str(i.id),
            "case_id": str(i.case_id),
            "intervention_type": i.intervention_type,
            "execution_capability": i.execution_capability,
            "policy_decision": i.policy_decision,
            "execution_status": i.execution_status,
            "created_at": i.created_at.isoformat(),
        }
        for i in result.scalars().all()
    ]


# ---------------------------------------------------------------------------
# GET /interventions/{intervention_id} — single intervention detail
# ---------------------------------------------------------------------------


@router.get(
    "/interventions/{intervention_id}",
    summary="Get single intervention detail",
    tags=["cases"],
)
async def get_intervention(
    intervention_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Return full detail for a single Intervention scoped to the authenticated merchant.

    Returns HTTP 404 when the intervention does not exist or belongs to another
    merchant — consistent response shape prevents resource enumeration
    (SEC-ISOL-002).

    Requirements: API-002, SEC-ISOL-002
    """
    result = await session.execute(
        select(Intervention).where(
            Intervention.id == intervention_id,
            Intervention.merchant_id == current_user.merchant_id,
        )
    )
    intervention = result.scalar_one_or_none()
    if intervention is None:
        raise HTTPException(status_code=404, detail="Intervention not found")

    return {
        "id": str(intervention.id),
        "case_id": str(intervention.case_id),
        "intervention_type": intervention.intervention_type,
        "execution_capability": intervention.execution_capability,
        "provider_support_status": intervention.provider_support_status,
        "requires_human_approval": intervention.requires_human_approval,
        "expected_recovery_value": intervention.expected_recovery_value,
        "verification_strategy": intervention.verification_strategy,
        "policy_decision": intervention.policy_decision,
        "execution_status": intervention.execution_status,
        "recommendation_text": intervention.recommendation_text,
        "created_at": intervention.created_at.isoformat(),
        "updated_at": intervention.updated_at.isoformat(),
    }
