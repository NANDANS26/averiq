"""Revenue risk analytics endpoints.

GET /api/v1/analytics/revenue
  Returns a summary of revenue at risk, grouped by:
    - State bucket (active non-terminal states)
    - Calculation path (SINGLE_PAYMENT vs SUBSCRIPTION_PROJECTED)

  Currency isolation (FR-RISK-007):
    - Only aggregates amounts in the merchant's configured_currency
    - Cases with different currencies are counted but isolated into
      "currency_mismatch_cases" — they are NOT included in totals

Requirements: FR-RISK-006, FR-RISK-007
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import CurrentUser, require_viewer
from backend.database import get_session
from backend.domain.models import Merchant, RevenueRiskCase

router = APIRouter()

# ---------------------------------------------------------------------------
# Terminal states — excluded from active totals
# ---------------------------------------------------------------------------

TERMINAL_STATES: frozenset[str] = frozenset(
    {"RECOVERED", "STOPPED", "ESCALATED", "FAILED"}
)


# ---------------------------------------------------------------------------
# Response schemas (DATA-001: all amounts are integer paise, never float)
# ---------------------------------------------------------------------------


class StateBucketTotal(BaseModel):
    """Aggregated revenue at risk for one non-terminal state bucket.

    Amounts are integer paise (minor currency units).  Float is forbidden
    for authoritative financial values (DATA-001, DATA-003).
    """

    state: str
    case_count: int
    total_amount_at_risk: int  # integer paise, never float
    currency: str


class PathTotal(BaseModel):
    """Aggregated revenue at risk for one calculation path.

    ``total_amount_at_risk`` is the sum of payment_amount_at_risk.
    ``total_projected_revenue_at_risk`` is the sum of
    projected_subscription_revenue_at_risk (only populated for
    SUBSCRIPTION_PROJECTED cases; 0 for SINGLE_PAYMENT).

    Amounts are integer paise (minor currency units).  Float is forbidden
    for authoritative financial values (DATA-001, DATA-003).
    """

    calculation_path: str  # SINGLE_PAYMENT | SUBSCRIPTION_PROJECTED
    case_count: int
    total_amount_at_risk: int  # integer paise, never float
    total_projected_revenue_at_risk: int  # integer paise (sum of projected values)
    currency: str


class CurrencyMismatchInfo(BaseModel):
    """Summary of cases whose currency differs from the configured merchant currency.

    These cases are counted but EXCLUDED from all totals (FR-RISK-007).
    Mixed-currency aggregation is rejected in MVP; cases are isolated here
    so operators can see the count without corrupting the INR totals.
    """

    currency: str
    case_count: int
    note: str = (
        "These cases are excluded from totals due to currency mismatch"
        " with configured_currency"
    )


class RevenueRiskSummary(BaseModel):
    """Top-level revenue risk summary response.

    All monetary amounts (total_amount_at_risk, total_projected_revenue_at_risk)
    are integer paise in the configured_currency.  Float is forbidden for
    authoritative financial values (DATA-001, DATA-003).

    ``by_state`` covers active non-terminal cases in the configured currency.
    ``by_calculation_path`` separates SINGLE_PAYMENT from SUBSCRIPTION_PROJECTED.
    ``currency_mismatch_cases`` lists counts of isolated non-matching cases.
    ``operating_mode`` mirrors the merchant's operating_mode for UI display.
    """

    configured_currency: str
    total_amount_at_risk: int  # integer paise, configured_currency only
    total_projected_revenue_at_risk: int  # integer paise
    active_case_count: int
    by_state: list[StateBucketTotal]
    by_calculation_path: list[PathTotal]
    currency_mismatch_cases: list[CurrencyMismatchInfo]
    operating_mode: str


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/revenue", response_model=RevenueRiskSummary)
async def get_revenue_risk_summary(
    current_user: Annotated[CurrentUser, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RevenueRiskSummary:
    """Return a summary of revenue at risk for the authenticated merchant.

    Aggregates active (non-terminal) RevenueRiskCase records, grouping
    results by state bucket and calculation path.

    Currency isolation (FR-RISK-007):
      Only cases whose ``currency`` matches ``merchant.configured_currency``
      are included in totals.  Cases with any other currency are counted
      separately in ``currency_mismatch_cases`` and excluded from all sums.

    Separate display (FR-RISK-006):
      ``by_calculation_path`` lists SINGLE_PAYMENT and SUBSCRIPTION_PROJECTED
      amounts separately so the caller can present observed vs. projected risk
      side-by-side.

    Amounts:
      All returned amounts are integer paise (minor currency units).
      Float is never used for authoritative financial amounts (DATA-001).

    Requirements: FR-RISK-006, FR-RISK-007
    """
    merchant_id = current_user.merchant_id

    # ------------------------------------------------------------------
    # Step 1: Load Merchant to obtain configured_currency and operating_mode
    # ------------------------------------------------------------------
    merchant: Merchant | None = await session.get(Merchant, merchant_id)
    if merchant is None:
        # Should never happen — the JWT was valid and the user exists.
        # Returning an empty summary is safer than a 500.
        return RevenueRiskSummary(
            configured_currency="INR",
            total_amount_at_risk=0,
            total_projected_revenue_at_risk=0,
            active_case_count=0,
            by_state=[],
            by_calculation_path=[],
            currency_mismatch_cases=[],
            operating_mode="SIMULATED",
        )

    configured_currency: str = merchant.currency
    operating_mode: str = merchant.operating_mode

    # ------------------------------------------------------------------
    # Step 2: State totals for matching-currency, non-terminal cases
    #
    # SELECT state,
    #        COUNT(*),
    #        SUM(payment_amount_at_risk)
    # FROM revenue_risk_cases
    # WHERE merchant_id = :mid
    #   AND currency    = :configured_currency
    #   AND state NOT IN ('RECOVERED','STOPPED','ESCALATED','FAILED')
    # GROUP BY state
    #
    # The SUM is guaranteed integer paise because payment_amount_at_risk is
    # BIGINT and we only include rows whose currency matches — no mixed
    # currency arithmetic (DATA-001, DATA-003).
    # ------------------------------------------------------------------
    state_rows_stmt = (
        select(
            RevenueRiskCase.state,
            func.count().label("case_count"),
            func.coalesce(func.sum(RevenueRiskCase.payment_amount_at_risk), 0).label(
                "total_amount"
            ),
        )
        .where(
            and_(
                RevenueRiskCase.merchant_id == merchant_id,
                RevenueRiskCase.currency == configured_currency,
                RevenueRiskCase.state.not_in(list(TERMINAL_STATES)),
            )
        )
        .group_by(RevenueRiskCase.state)
    )
    state_result = await session.execute(state_rows_stmt)
    state_rows = state_result.all()

    by_state: list[StateBucketTotal] = [
        StateBucketTotal(
            state=row.state,
            case_count=int(row.case_count),
            # int() ensures no accidental float if the DB driver returns Decimal
            total_amount_at_risk=int(row.total_amount),
            currency=configured_currency,
        )
        for row in state_rows
    ]

    # ------------------------------------------------------------------
    # Step 3: Path totals for matching-currency, non-terminal cases
    #
    # SELECT risk_calculation_path,
    #        COUNT(*),
    #        SUM(payment_amount_at_risk),
    #        COALESCE(SUM(projected_subscription_revenue_at_risk), 0)
    # FROM revenue_risk_cases
    # WHERE merchant_id = :mid
    #   AND currency    = :configured_currency
    #   AND state NOT IN ('RECOVERED','STOPPED','ESCALATED','FAILED')
    # GROUP BY risk_calculation_path
    # ------------------------------------------------------------------
    path_rows_stmt = (
        select(
            RevenueRiskCase.risk_calculation_path,
            func.count().label("case_count"),
            func.coalesce(func.sum(RevenueRiskCase.payment_amount_at_risk), 0).label(
                "total_amount"
            ),
            func.coalesce(
                func.sum(RevenueRiskCase.projected_subscription_revenue_at_risk), 0
            ).label("total_projected"),
        )
        .where(
            and_(
                RevenueRiskCase.merchant_id == merchant_id,
                RevenueRiskCase.currency == configured_currency,
                RevenueRiskCase.state.not_in(list(TERMINAL_STATES)),
            )
        )
        .group_by(RevenueRiskCase.risk_calculation_path)
    )
    path_result = await session.execute(path_rows_stmt)
    path_rows = path_result.all()

    by_calculation_path: list[PathTotal] = [
        PathTotal(
            calculation_path=row.risk_calculation_path,
            case_count=int(row.case_count),
            total_amount_at_risk=int(row.total_amount),
            # int() ensures no accidental float if the DB driver returns Decimal
            total_projected_revenue_at_risk=int(row.total_projected),
            currency=configured_currency,
        )
        for row in path_rows
    ]

    # ------------------------------------------------------------------
    # Step 4: Currency-mismatch cases (FR-RISK-007)
    #
    # SELECT currency, COUNT(*)
    # FROM revenue_risk_cases
    # WHERE merchant_id = :mid
    #   AND currency   != :configured_currency
    #   AND state NOT IN ('RECOVERED','STOPPED','ESCALATED','FAILED')
    # GROUP BY currency
    # ------------------------------------------------------------------
    mismatch_stmt = (
        select(
            RevenueRiskCase.currency,
            func.count().label("case_count"),
        )
        .where(
            and_(
                RevenueRiskCase.merchant_id == merchant_id,
                RevenueRiskCase.currency != configured_currency,
                RevenueRiskCase.state.not_in(list(TERMINAL_STATES)),
            )
        )
        .group_by(RevenueRiskCase.currency)
    )
    mismatch_result = await session.execute(mismatch_stmt)
    mismatch_rows = mismatch_result.all()

    currency_mismatch_cases: list[CurrencyMismatchInfo] = [
        CurrencyMismatchInfo(
            currency=row.currency,
            case_count=int(row.case_count),
        )
        for row in mismatch_rows
    ]

    # ------------------------------------------------------------------
    # Step 5: Compute top-level totals from already-fetched state rows
    # (avoids an extra DB round-trip)
    # ------------------------------------------------------------------
    total_amount_at_risk: int = sum(b.total_amount_at_risk for b in by_state)
    total_projected_revenue_at_risk: int = sum(
        p.total_projected_revenue_at_risk for p in by_calculation_path
    )
    active_case_count: int = sum(b.case_count for b in by_state)

    return RevenueRiskSummary(
        configured_currency=configured_currency,
        total_amount_at_risk=total_amount_at_risk,
        total_projected_revenue_at_risk=total_projected_revenue_at_risk,
        active_case_count=active_case_count,
        by_state=by_state,
        by_calculation_path=by_calculation_path,
        currency_mismatch_cases=currency_mismatch_cases,
        operating_mode=operating_mode,
    )


# ---------------------------------------------------------------------------
# Canonical Phase 3.6 Revenue Recovery Metrics (Workstream E)
# ---------------------------------------------------------------------------


class CanonicalRecoveryCohort(BaseModel):
    """Canonical recovery analytics for a single time cohort (7d, 30d, all_time)."""

    timeframe: str
    total_cases: int
    recovered_cases: int
    recovery_rate: float
    total_at_risk_paise: int
    organic_recovery_paise: int
    gross_recovered_paise: int
    incremental_recovery_paise: int
    total_intervention_cost_paise: int
    net_incremental_recovery_paise: int
    average_net_recovery_per_case_paise: int
    escalated_cases_count: int
    failed_cases_count: int
    human_review_cases_count: int
    fallback_cases_count: int


class CanonicalRecoveryResponse(BaseModel):
    """Response envelope for canonical revenue recovery analytics."""

    currency: str
    cohorts: dict[str, CanonicalRecoveryCohort]
    definition_notes: dict[str, str]


@router.get("/recovery-canonical", response_model=CanonicalRecoveryResponse)
async def get_canonical_recovery_analytics(
    current_user: Annotated[CurrentUser, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CanonicalRecoveryResponse:
    """Return canonical Phase 3.6 revenue recovery metrics for 7d, 30d, and all_time.

    Canonical definitions:
      - Organic Recovery: sum(P0 * payment_amount_at_risk) for evaluated cases
      - Gross Recovered: sum(payment_amount_at_risk) for RECOVERED cases
      - Incremental Recovery: max(0, Gross Recovered - Organic Recovery)
      - Intervention Costs: sum of cost_paise for executed interventions
      - Net Incremental: Incremental Recovery - Intervention Costs
      - Integer paise strictly preserved; no float money.
    """
    from datetime import datetime, timezone, timedelta
    from backend.domain.models import RevenueRiskCase, Intervention, HumanReview

    merchant_id = current_user.merchant_id

    # Fetch merchant currency
    m_stmt = select(Merchant.currency).where(Merchant.id == merchant_id)
    m_res = await session.execute(m_stmt)
    configured_currency = m_res.scalar_one_or_none() or "INR"

    now = datetime.now(timezone.utc)
    cutoffs = {
        "7d": now - timedelta(days=7),
        "30d": now - timedelta(days=30),
        "all_time": datetime.min.replace(tzinfo=timezone.utc),
    }

    # Load all cases for merchant in configured currency
    case_stmt = select(RevenueRiskCase).where(
        RevenueRiskCase.merchant_id == merchant_id,
        RevenueRiskCase.currency == configured_currency,
    )
    case_rows = (await session.execute(case_stmt)).scalars().all()

    # Load all interventions for merchant
    intv_stmt = select(Intervention).where(
        Intervention.merchant_id == merchant_id,
    )
    intv_rows = (await session.execute(intv_stmt)).scalars().all()

    # Map intervention costs
    from ai.models.action_value_model import DEFAULT_INTERVENTION_COSTS_PAISE
    intv_by_case: dict[uuid.UUID, list[Intervention]] = {}
    for inv in intv_rows:
        intv_by_case.setdefault(inv.case_id, []).append(inv)

    cohorts_res: dict[str, CanonicalRecoveryCohort] = {}

    for cohort_name, cutoff in cutoffs.items():
        c_cases = [c for c in case_rows if c.created_at >= cutoff]
        tot_cases = len(c_cases)
        rec_cases = [c for c in c_cases if c.state == "RECOVERED"]
        escalated_cases = [c for c in c_cases if c.state == "ESCALATED"]
        failed_cases = [c for c in c_cases if c.state == "FAILED"]
        fallback_cases = [
            c for c in c_cases
            if (c.case_metadata and c.case_metadata.get("fallback_reason"))
            or c.ai_source == "DeterministicFallback"
        ]

        # Total at risk
        tot_at_risk = sum(c.payment_amount_at_risk for c in c_cases)

        # Gross recovered
        gross_rec = sum(
            c.recovered_amount if c.recovered_amount is not None else c.payment_amount_at_risk
            for c in rec_cases
        )

        # Organic recovery: baseline expectation sum(floor(P0 * amount))
        organic_rec = 0
        for c in c_cases:
            meta = c.case_metadata or {}
            trace = meta.get("decision_trace", {})
            p0 = trace.get("baseline_recovery_probability", 0.35)
            # integer paise baseline expectation
            organic_rec += int(float(p0) * c.payment_amount_at_risk)

        # Incremental recovery: exact canonical formula (Gross - Organic)
        incremental_rec = gross_rec - organic_rec

        # Intervention cost
        tot_cost = 0
        human_review_count = 0
        for c in c_cases:
            meta = c.case_metadata or {}
            if meta.get("requires_human_review"):
                human_review_count += 1
            for inv in intv_by_case.get(c.id, []):
                if inv.requires_human_approval:
                    human_review_count += 1
                itype = inv.intervention_type
                # If intervention executed or pending execution, calculate cost
                cost = DEFAULT_INTERVENTION_COSTS_PAISE.get(itype, 0)
                tot_cost += cost

        net_incremental = incremental_rec - tot_cost
        avg_net_per_case = int(net_incremental / tot_cases) if tot_cases > 0 else 0
        rec_rate = round(len(rec_cases) / tot_cases, 4) if tot_cases > 0 else 0.0

        cohorts_res[cohort_name] = CanonicalRecoveryCohort(
            timeframe=cohort_name,
            total_cases=tot_cases,
            recovered_cases=len(rec_cases),
            recovery_rate=rec_rate,
            total_at_risk_paise=tot_at_risk,
            organic_recovery_paise=organic_rec,
            gross_recovered_paise=gross_rec,
            incremental_recovery_paise=incremental_rec,
            total_intervention_cost_paise=tot_cost,
            net_incremental_recovery_paise=net_incremental,
            average_net_recovery_per_case_paise=avg_net_per_case,
            escalated_cases_count=len(escalated_cases),
            failed_cases_count=len(failed_cases),
            human_review_cases_count=human_review_count,
            fallback_cases_count=len(fallback_cases),
        )

    return CanonicalRecoveryResponse(
        currency=configured_currency,
        cohorts=cohorts_res,
        definition_notes={
            "organic_recovery": "Expected recovery without intervention: sum(floor(P0 * Amount))",
            "gross_recovered": "Realized recovery from successful terminal states: sum(Amount)",
            "incremental_recovery": "Realized gross recovery beyond organic baseline: Gross - Organic (can be negative if underperforming)",
            "intervention_cost": "Sum of execution and communication costs in paise",
            "net_incremental_recovery": "Economic surplus: Incremental Recovery - Intervention Cost",
        },
    )

