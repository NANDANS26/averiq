"""Demo execution and scenario management API router.

Endpoints:
  GET /api/v1/demo/scenarios      - List all supported deterministic demo scenarios
  POST /api/v1/demo/run/{scenario_id} - Execute or re-seed a deterministic demo scenario
  GET /api/v1/demo/cases          - List all cases flagged with is_demo_fixture=True

All demo fixtures carry `is_demo_fixture = True` and use a dedicated demo merchant.
No production data is modified or exposed.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import CurrentUser, require_viewer
from backend.database import async_session_factory, get_session
from backend.domain.models import RevenueRiskCase
from backend.simulation.demo_fixtures import DemoFixtureManager

router = APIRouter()


class DemoScenarioMetadata(BaseModel):
    id: str
    code: str
    name: str
    description: str
    expected_action: str
    expected_final_state: str
    category: str


DEMO_SCENARIOS = [
    DemoScenarioMetadata(
        id="demo-001",
        code="DEMO-001",
        name="Golden Recovery Path",
        description="Normal payment failure -> automated PAYMENT_LINK -> verification -> successful recovery",
        expected_action="PAYMENT_LINK",
        expected_final_state="RECOVERED",
        category="Golden Path",
    ),
    DemoScenarioMetadata(
        id="demo-002",
        code="DEMO-002",
        name="High-Value Human Approval",
        description="Payment amount exceeds auto-threshold (₹10,000+) -> policy pauses in AWAITING_APPROVAL",
        expected_action="PAYMENT_LINK",
        expected_final_state="AWAITING_APPROVAL",
        category="Policy & Governance",
    ),
    DemoScenarioMetadata(
        id="demo-003",
        code="DEMO-003",
        name="Fraud Policy Hard Block",
        description="Fraud risk detected or stop condition met -> all interventions blocked -> safe STOPPED",
        expected_action="NO_INTERVENTION",
        expected_final_state="STOPPED",
        category="Policy & Safety",
    ),
    DemoScenarioMetadata(
        id="demo-004",
        code="DEMO-004",
        name="Gateway Execution Failure",
        description="Simulated 502 Bad Gateway during payment link generation -> safely moves to FAILED",
        expected_action="PAYMENT_LINK",
        expected_final_state="FAILED",
        category="Fault Tolerance",
    ),
    DemoScenarioMetadata(
        id="demo-005",
        code="DEMO-005",
        name="Verification Retry Exhaustion",
        description="Settlement verification fails 3 consecutive times -> automatically escalates to operator",
        expected_action="PAYMENT_LINK",
        expected_final_state="ESCALATED",
        category="Fault Tolerance",
    ),
    DemoScenarioMetadata(
        id="demo-006",
        code="DEMO-006",
        name="Idempotent Duplicate Webhook",
        description="Duplicate payment.failed webhook received -> idempotency key prevents duplicate execution",
        expected_action="PAYMENT_LINK",
        expected_final_state="RECOVERED",
        category="Idempotency",
    ),
    DemoScenarioMetadata(
        id="demo-007",
        code="DEMO-007",
        name="Execution Lock Race Prevention",
        description="Simultaneous concurrent execution requests -> Redis distributed lock blocks second runner",
        expected_action="PAYMENT_LINK",
        expected_final_state="RECOVERED",
        category="Concurrency Safety",
    ),
    DemoScenarioMetadata(
        id="demo-008",
        code="DEMO-008",
        name="Model Unavailable Fallback",
        description="Action-value model inference unavailable -> fallback to base rate P0 = 0.35 -> safe evaluation",
        expected_action="NO_INTERVENTION",
        expected_final_state="STOPPED",
        category="Resilience",
    ),
]


class DemoRunResponse(BaseModel):
    scenario_id: str
    case_id: str
    expected_final_state: str
    message: str


@router.get("/demo/scenarios", response_model=list[DemoScenarioMetadata], tags=["demo"])
async def list_demo_scenarios(
    current_user: CurrentUser = Depends(require_viewer),
) -> list[DemoScenarioMetadata]:
    """List all pre-configured deterministic demo scenarios."""
    return DEMO_SCENARIOS


@router.post("/demo/run/{scenario_id}", response_model=DemoRunResponse, tags=["demo"])
async def run_demo_scenario(
    scenario_id: str,
    current_user: CurrentUser = Depends(require_viewer),
) -> DemoRunResponse:
    """Execute a deterministic demo scenario and return the seeded case ID."""
    manager = DemoFixtureManager(async_session_factory)

    scenario_map = {
        "demo-001": (manager.seed_demo_001_successful_recovery, "RECOVERED"),
        "demo-002": (manager.seed_demo_002_human_approval, "AWAITING_APPROVAL"),
        "demo-003": (manager.seed_demo_003_policy_stopped, "STOPPED"),
        "demo-004": (manager.seed_demo_004_execution_failure, "FAILED"),
        "demo-005": (manager.seed_demo_005_retry_exhaustion_escalated, "ESCALATED"),
        "demo-006": (manager.seed_demo_006_duplicate_webhook, "RECOVERED"),
        "demo-007": (manager.seed_demo_007_duplicate_execution_blocked, "RECOVERED"),
        "demo-008": (manager.seed_demo_008_fallback_probability, "STOPPED"),
    }

    if scenario_id not in scenario_map:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown demo scenario: {scenario_id}",
        )

    fn, expected_state = scenario_map[scenario_id]
    case_id = await fn()

    return DemoRunResponse(
        scenario_id=scenario_id,
        case_id=case_id,
        expected_final_state=expected_state,
        message=f"Demo scenario {scenario_id} executed successfully. Case is ready for inspection.",
    )


@router.get("/demo/cases", tags=["demo"])
async def list_demo_cases(
    db: AsyncSession = Depends(get_session),
    current_user: CurrentUser = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """List recent demo fixture cases."""
    stmt = (
        select(RevenueRiskCase)
        .where(RevenueRiskCase.is_demo_fixture.is_(True))
        .order_by(desc(RevenueRiskCase.created_at))
        .limit(20)
    )
    result = await db.execute(stmt)
    cases = result.scalars().all()

    return [
        {
            "id": str(c.id),
            "state": c.state,
            "amount": c.payment_amount_at_risk,
            "currency": c.currency,
            "failure_category": c.failure_category,
            "recovery_risk_tier": c.recovery_risk_tier,
            "operating_mode": c.operating_mode,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in cases
    ]
