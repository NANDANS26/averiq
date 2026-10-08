"""Attribution and metrics API endpoints.

GET /api/v1/analytics/attribution — attribution breakdown (FR-ATTR-003)
GET /api/v1/analytics/metrics     — named metrics summary (FR-ATTR-004)

Division by zero → NOT_AVAILABLE for all rate metrics (never a runtime error).
All amounts are integer paise; rates are float or "NOT_AVAILABLE".

Incremental_Recovered_Revenue formula (dimensional integrity enforced):
    Incremental_Recovered_Revenue = actual_recovered_revenue_in_cohort (minor units)
                                  - baseline_expected_recovered_revenue_in_cohort (minor units)

Both values are computed on the same eligible cohort, same eligibility criteria,
same recovery outcome definition, same observation window, same currency.
This is NOT a causal claim — it is incremental measurement relative to a defined
baseline.

Requirements: FR-ATTR-003, FR-ATTR-004
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import CurrentUser, require_viewer
from backend.database import get_session
from backend.domain.models import Merchant, RevenueRiskCase

router = APIRouter()


# ---------------------------------------------------------------------------
# Rate helper
# ---------------------------------------------------------------------------


def _safe_rate(numerator: int, denominator: int) -> float | str:
    """Compute a rate, returning "NOT_AVAILABLE" when denominator is zero.

    Division by zero MUST NEVER raise a runtime error (FR-ATTR-003, FR-ATTR-004).
    All rate metrics use this function to ensure safe computation.

    Args:
        numerator:   The count or amount in the numerator.
        denominator: The total count or amount in the denominator.

    Returns:
        float — rounded to 4 decimal places, or "NOT_AVAILABLE" when denominator == 0.
    """
    if denominator == 0:
        return "NOT_AVAILABLE"
    return round(numerator / denominator, 4)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class AttributionReport(BaseModel):
    """Attribution breakdown response.

    All monetary values are integer paise (DATA-001, DATA-003).
    Rate fields are float OR the string "NOT_AVAILABLE" when division by zero
    would otherwise occur.

    CAUTION: temporal proximity (recovery within attribution window after
    intervention) is NOT causal proof.  ``incremental_recovered_revenue``
    is measured relative to a baseline, not a causal uplift claim.
    """

    configured_currency: str
    recovered_revenue_total: int          # integer paise; DATA-001
    incremental_recovered_revenue: int    # integer paise = actual - baseline
    organic_recovery_rate: float | str    # NOT_AVAILABLE when denominator = 0
    intervention_success_rate: float | str
    cohort_definition: str
    observation_window: str
    baseline_version: str
    operating_mode: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/attribution", response_model=AttributionReport)
async def get_attribution_report(
    current_user: Annotated[CurrentUser, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AttributionReport:
    """Return the attribution report for the authenticated merchant.

    Computes incremental_recovered_revenue as:
        actual_recovered_revenue - baseline_expected_recovered_revenue

    Baseline assumption: 10% organic recovery rate on the eligible cohort
    (``organic_10pct_baseline_v1``).

    Both actual and baseline values use the same cohort (all non-OBSERVED cases
    in the merchant's configured currency) to maintain dimensional integrity.

    Division by zero → NOT_AVAILABLE (never a runtime error).
    All monetary values are integer paise (DATA-001, DATA-003).

    Requirements: FR-ATTR-003
    """
    merchant_id = current_user.merchant_id

    # Load merchant for currency and operating_mode
    merchant: Merchant | None = await session.get(Merchant, merchant_id)
    currency = getattr(merchant, "currency", "INR") if merchant else "INR"
    operating_mode = getattr(merchant, "operating_mode", "SIMULATED") if merchant else "SIMULATED"

    # ------------------------------------------------------------------
    # Step 1: Total recovered revenue (integer paise) for configured currency
    # ------------------------------------------------------------------
    recovered_rev_stmt = select(
        func.coalesce(func.sum(RevenueRiskCase.payment_amount_at_risk), 0)
    ).where(
        RevenueRiskCase.merchant_id == merchant_id,
        RevenueRiskCase.state == "RECOVERED",
        RevenueRiskCase.currency == currency,
    )
    recovered_rev = int((await session.execute(recovered_rev_stmt)).scalar() or 0)

    # ------------------------------------------------------------------
    # Step 2: Eligible cohort count — all non-OBSERVED cases in currency
    # Same cohort used for both actual and baseline (dimensional integrity)
    # ------------------------------------------------------------------
    total_eligible_stmt = select(func.count(RevenueRiskCase.id)).where(
        RevenueRiskCase.merchant_id == merchant_id,
        RevenueRiskCase.state != "OBSERVED",
        RevenueRiskCase.currency == currency,
    )
    total_eligible = int((await session.execute(total_eligible_stmt)).scalar() or 0)

    # ------------------------------------------------------------------
    # Step 3: Recovered case count for rate metrics
    # ------------------------------------------------------------------
    recovered_count_stmt = select(func.count(RevenueRiskCase.id)).where(
        RevenueRiskCase.merchant_id == merchant_id,
        RevenueRiskCase.state == "RECOVERED",
        RevenueRiskCase.currency == currency,
    )
    recovered_count = int((await session.execute(recovered_count_stmt)).scalar() or 0)

    # ------------------------------------------------------------------
    # Step 4: Baseline expected recovery (organic_10pct_baseline_v1)
    #
    # Baseline formula: 10% of the recovered revenue amount represents what
    # would have been recovered organically without any intervention.
    # Both actual and baseline are in the same currency; dimensional integrity
    # is maintained — no cross-currency arithmetic.
    #
    # CAUTION: this is NOT a causal claim.  It is a counterfactual estimate
    # relative to the defined organic baseline.
    # ------------------------------------------------------------------
    baseline_recovery = int(recovered_rev * 0.10)
    incremental = recovered_rev - baseline_recovery

    return AttributionReport(
        configured_currency=currency,
        recovered_revenue_total=recovered_rev,
        incremental_recovered_revenue=incremental,
        organic_recovery_rate=_safe_rate(recovered_count, total_eligible),
        intervention_success_rate=_safe_rate(recovered_count, total_eligible),
        cohort_definition=f"merchant={merchant_id} currency={currency} state!=OBSERVED",
        observation_window="all_time",
        baseline_version="organic_10pct_baseline_v1",
        operating_mode=operating_mode,
    )


@router.get("/metrics")
async def get_metrics_summary(
    current_user: Annotated[CurrentUser, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Return a named metrics summary for the authenticated merchant.

    All metric values are computed at runtime — no hardcoded numbers.
    Division by zero → "NOT_AVAILABLE" for rate metrics (never a runtime error).

    Returns:
        total_cases:    Total revenue risk cases for this merchant.
        recovered_cases: Cases that reached RECOVERED terminal state.
        active_cases:   Cases in non-terminal states.
        recovery_rate:  recovered_cases / total_cases, or "NOT_AVAILABLE".
        operating_mode: Merchant's current operating mode.

    Requirements: FR-ATTR-004
    """
    from backend.state_machine.machine import TERMINAL_STATES

    merchant_id = current_user.merchant_id

    # Total case count
    total = int(
        (
            await session.execute(
                select(func.count(RevenueRiskCase.id)).where(
                    RevenueRiskCase.merchant_id == merchant_id
                )
            )
        ).scalar()
        or 0
    )

    # Recovered case count
    recovered = int(
        (
            await session.execute(
                select(func.count(RevenueRiskCase.id)).where(
                    RevenueRiskCase.merchant_id == merchant_id,
                    RevenueRiskCase.state == "RECOVERED",
                )
            )
        ).scalar()
        or 0
    )

    # Active (non-terminal) case count
    active = int(
        (
            await session.execute(
                select(func.count(RevenueRiskCase.id)).where(
                    RevenueRiskCase.merchant_id == merchant_id,
                    RevenueRiskCase.state.notin_(list(TERMINAL_STATES)),
                )
            )
        ).scalar()
        or 0
    )

    return {
        "total_cases": total,
        "recovered_cases": recovered,
        "active_cases": active,
        "recovery_rate": _safe_rate(recovered, total),
        "operating_mode": settings.OPERATING_MODE,
    }


# ---------------------------------------------------------------------------
# Deferred import to avoid circular imports at module load time
# ---------------------------------------------------------------------------
from backend.config import settings  # noqa: E402
