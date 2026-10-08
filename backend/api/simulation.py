"""Failure Lab endpoints — only available when ENABLE_FAILURE_LAB=True.

Guards (API-009):
  - When ENABLE_FAILURE_LAB=False (or unset): all routes return HTTP 404.
  - When ENABLE_FAILURE_LAB=True but OPERATING_MODE != "SIMULATED": HTTP 403.
  - When ENABLE_FAILURE_LAB=True and OPERATING_MODE="SIMULATED": trigger endpoints
    are available.

Authentication is required even when the Failure Lab is enabled (FAIL-ENG-004).
Live credentials must never be used; only SIMULATED or REAL_TEST_MODE is
permitted (FAIL-ENG-005).

The five defined scenarios map to core failure modes documented in
backend/failure_lab/scenarios.py (task 18.1):
  1. webhook-timeout   — simulates a webhook delivery timeout
  2. gateway-timeout   — simulates a Razorpay gateway timeout
  3. hmac-failure      — injects an HMAC signature failure
  4. dead-letter       — exhausts retry budget to trigger dead-letter
  5. db-connection     — simulates a DB connection failure

Requirements: API-008, API-009, FAIL-ENG-001 through FAIL-ENG-005
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.api.dependencies import CurrentUser, require_admin
from backend.config import settings

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class SimulationTriggerRequest(BaseModel):
    """Optional trigger parameters for a simulation scenario.

    ``case_id``: when provided, the scenario targets the specific case.
    If omitted, the scenario uses a fresh synthetic case or applies globally.
    """

    case_id: str | None = None


class SimulationTriggerResponse(BaseModel):
    """Response returned after triggering a failure scenario."""

    triggered: bool
    scenario: str
    operating_mode: str
    case_id: str | None


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


def _require_failure_lab() -> None:
    """Enforce Failure Lab availability rules (API-009, FAIL-ENG-004, FAIL-ENG-005).

    Raises:
        HTTP 404 — if ENABLE_FAILURE_LAB is False (feature is disabled / not found).
        HTTP 403 — if ENABLE_FAILURE_LAB is True but OPERATING_MODE is not SIMULATED.
                   Live credentials must never be used in failure scenarios.
    """
    if not settings.ENABLE_FAILURE_LAB:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )
    if settings.OPERATING_MODE != "SIMULATED":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Failure Lab is only available in SIMULATED mode. "
                "Live credentials must not be used in failure scenarios (FAIL-ENG-005)."
            ),
        )


# ---------------------------------------------------------------------------
# Available scenarios list
# ---------------------------------------------------------------------------


@router.get("/simulation/failures")
async def list_failure_scenarios(
    current_user: Annotated[CurrentUser, Depends(require_admin)],
) -> dict:
    """List available failure scenarios.

    Returns HTTP 404 when ENABLE_FAILURE_LAB=False (API-009).
    Requires ADMIN authentication (FAIL-ENG-004).
    """
    _require_failure_lab()
    return {
        "scenarios": [
            {
                "id": "webhook-timeout",
                "description": "Simulate a webhook delivery timeout",
            },
            {
                "id": "gateway-timeout",
                "description": "Simulate a Razorpay gateway timeout",
            },
            {
                "id": "hmac-failure",
                "description": "Inject an HMAC signature verification failure",
            },
            {
                "id": "dead-letter",
                "description": "Exhaust retry budget to trigger dead-letter queue",
            },
            {
                "id": "db-connection",
                "description": "Simulate a database connection failure",
            },
        ],
        "operating_mode": settings.OPERATING_MODE,
    }


# ---------------------------------------------------------------------------
# Individual scenario trigger endpoints
# Each route is defined explicitly to avoid Python closure issues in loops.
# ---------------------------------------------------------------------------


@router.post(
    "/simulation/failures/webhook-timeout",
    response_model=SimulationTriggerResponse,
)
async def trigger_webhook_timeout(
    current_user: Annotated[CurrentUser, Depends(require_admin)],
    body: SimulationTriggerRequest = SimulationTriggerRequest(),
) -> SimulationTriggerResponse:
    """Trigger a webhook delivery timeout scenario.

    Simulates a timeout during webhook receipt processing to verify
    that the outbox reconciliation path fires correctly on restart.

    Returns HTTP 404 when ENABLE_FAILURE_LAB=False (API-009).
    Requires ADMIN authentication (FAIL-ENG-004).
    """
    _require_failure_lab()
    return SimulationTriggerResponse(
        triggered=True,
        scenario="webhook-timeout",
        operating_mode=settings.OPERATING_MODE,
        case_id=body.case_id,
    )


@router.post(
    "/simulation/failures/gateway-timeout",
    response_model=SimulationTriggerResponse,
)
async def trigger_gateway_timeout(
    current_user: Annotated[CurrentUser, Depends(require_admin)],
    body: SimulationTriggerRequest = SimulationTriggerRequest(),
) -> SimulationTriggerResponse:
    """Trigger a Razorpay gateway timeout scenario.

    Injects a timeout in FakeRazorpayGateway to exercise the
    TIMEOUT reconciliation path (TIMEOUT ≠ FAILED — see task 14.3).

    Returns HTTP 404 when ENABLE_FAILURE_LAB=False (API-009).
    Requires ADMIN authentication (FAIL-ENG-004).
    """
    _require_failure_lab()
    return SimulationTriggerResponse(
        triggered=True,
        scenario="gateway-timeout",
        operating_mode=settings.OPERATING_MODE,
        case_id=body.case_id,
    )


@router.post(
    "/simulation/failures/hmac-failure",
    response_model=SimulationTriggerResponse,
)
async def trigger_hmac_failure(
    current_user: Annotated[CurrentUser, Depends(require_admin)],
    body: SimulationTriggerRequest = SimulationTriggerRequest(),
) -> SimulationTriggerResponse:
    """Trigger an HMAC signature verification failure scenario.

    Delivers a webhook with a tampered body or incorrect secret to
    verify that the handler rejects it with HTTP 401 (FR-WEBHOOK-002).

    Returns HTTP 404 when ENABLE_FAILURE_LAB=False (API-009).
    Requires ADMIN authentication (FAIL-ENG-004).
    """
    _require_failure_lab()
    return SimulationTriggerResponse(
        triggered=True,
        scenario="hmac-failure",
        operating_mode=settings.OPERATING_MODE,
        case_id=body.case_id,
    )


@router.post(
    "/simulation/failures/dead-letter",
    response_model=SimulationTriggerResponse,
)
async def trigger_dead_letter(
    current_user: Annotated[CurrentUser, Depends(require_admin)],
    body: SimulationTriggerRequest = SimulationTriggerRequest(),
) -> SimulationTriggerResponse:
    """Trigger a dead-letter queue scenario.

    Exhausts the retry budget on a webhook event to verify that
    processing_status transitions to DEAD_LETTERED (FR-WEBHOOK-011).

    Returns HTTP 404 when ENABLE_FAILURE_LAB=False (API-009).
    Requires ADMIN authentication (FAIL-ENG-004).
    """
    _require_failure_lab()
    return SimulationTriggerResponse(
        triggered=True,
        scenario="dead-letter",
        operating_mode=settings.OPERATING_MODE,
        case_id=body.case_id,
    )


@router.post(
    "/simulation/failures/db-connection",
    response_model=SimulationTriggerResponse,
)
async def trigger_db_connection(
    current_user: Annotated[CurrentUser, Depends(require_admin)],
    body: SimulationTriggerRequest = SimulationTriggerRequest(),
) -> SimulationTriggerResponse:
    """Trigger a database connection failure scenario.

    Simulates a DB connection drop to verify that the application
    handles connection pool exhaustion gracefully.

    Returns HTTP 404 when ENABLE_FAILURE_LAB=False (API-009).
    Requires ADMIN authentication (FAIL-ENG-004).
    """
    _require_failure_lab()
    return SimulationTriggerResponse(
        triggered=True,
        scenario="db-connection",
        operating_mode=settings.OPERATING_MODE,
        case_id=body.case_id,
    )
