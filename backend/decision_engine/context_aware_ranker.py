"""Context-Aware Intervention Ranker for Averiq Decision Engine.

Ranks recovery interventions by Expected Net Incremental Recovery Value:
  IncrementalUplift(a) = max(0, P(recovery | X, a) - P(recovery | X, NO_INTERVENTION))
  ExpectedIncrementalRecovery(a) = floor(IncrementalUplift(a) × Amount_paise)
  ExpectedNetRecoveryValue(a) = ExpectedIncrementalRecovery(a) - Cost_paise

Guarantees:
  1. Policy-governed: Blocked candidates never win.
  2. Explainable: Full structured decision trace with deterministic reason codes.
  3. Safe: Strict integer paise arithmetic; no float money.
  4. Separate from Policy: ML and economics rank candidates; Policy approves/blocks.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR
from typing import Any, Final

from ai.models.action_value_model import (
    DEFAULT_INTERVENTION_COSTS_PAISE,
    ActionValueEstimator,
)
from shared.contracts.enums import (
    ExecutionCapability,
    FailureCategory,
    InterventionType,
    OperatingMode,
)
from shared.contracts.schemas import CaseContext, InterventionCandidate, PolicyConfig

RANKER_VERSION: Final[str] = "context-aware-v3.0"


def compute_incremental_ev(
    uplift: float,
    amount_paise: int,
    cost_paise: int,
) -> int:
    """Compute floor(uplift × amount) - cost using Decimal for integer paise precision."""
    gross_dec = Decimal(str(max(0.0, uplift))) * Decimal(amount_paise)
    gross_paise = int(gross_dec.to_integral_value(rounding=ROUND_FLOOR))
    return gross_paise - cost_paise


class ContextAwareInterventionRanker:
    """Ranks interventions based on action-specific value estimation and economics."""

    def __init__(
        self,
        estimator: ActionValueEstimator | None = None,
        intervention_costs: dict[str, int] | None = None,
    ) -> None:
        self.estimator = estimator
        self.costs = intervention_costs or dict(DEFAULT_INTERVENTION_COSTS_PAISE)

    def generate_and_rank_candidates(
        self,
        context: CaseContext,
        policy_config: PolicyConfig,
        operating_mode: str | OperatingMode = "SIMULATED",
        payment_link_count: int = 0,
        model_version: str = "stub-v0",
        action_probabilities: dict[str, float] | None = None,
        failure_category: FailureCategory | None = None,
    ) -> tuple[list[InterventionCandidate], dict[str, Any]]:
        """Generate candidates for all actionable interventions and rank them.

        Returns:
          (ranked_candidates, decision_trace)
        """
        mode_str = (
            operating_mode.value
            if isinstance(operating_mode, OperatingMode)
            else str(operating_mode)
        )

        # 1. Action Probabilities
        if action_probabilities is None:
            if self.estimator is not None:
                probs = self.estimator.predict_action_probabilities({
                    "amount_paise": context.amount_paise,
                    "currency": context.currency,
                    "failure_category": failure_category.value if failure_category else "UNKNOWN",
                    "previous_failure_count": context.previous_failure_count,
                    "time_since_failure_seconds": 0.0,
                })
            else:
                probs = {
                    "NO_INTERVENTION": 0.35,
                    "PAYMENT_LINK": 0.35,
                    "RETRY": 0.35,
                    "CUSTOMER_ACTION": 0.35,
                }
        else:
            probs = dict(action_probabilities)

        p0 = probs.get("NO_INTERVENTION", 0.35)

        candidates: list[InterventionCandidate] = []
        candidate_details: list[dict[str, Any]] = []

        # ---------------------------------------------------------------------
        # 1. PAYMENT_LINK Candidate
        # ---------------------------------------------------------------------
        p_plink = probs.get("PAYMENT_LINK", p0)
        uplift_plink = max(0.0, round(p_plink - p0, 4))
        cost_plink = self.costs.get(InterventionType.PAYMENT_LINK.value, 500)
        ev_plink = compute_incremental_ev(uplift_plink, context.amount_paise, cost_plink)

        is_test_limit = (
            mode_str == OperatingMode.REAL_TEST_MODE.value
            and payment_link_count >= 30
        )
        plink_cap = ExecutionCapability.NO_OP if is_test_limit else ExecutionCapability.CREATE_PAYMENT_LINK

        plink_cand = InterventionCandidate(
            intervention_type=InterventionType.PAYMENT_LINK,
            execution_capability=plink_cap,
            recovery_probability=p_plink,
            expected_recovery_value=ev_plink,
            reasoning=(
                f"PAYMENT_LINK predicted P={p_plink:.2f} (uplift +{uplift_plink:.2f} over baseline {p0:.2f}), "
                f"net EV ₹{ev_plink/100:.2f} after cost ₹{cost_plink/100:.2f}."
            ),
            model_version=model_version,
        )
        candidates.append(plink_cand)
        candidate_details.append({
            "action": "PAYMENT_LINK",
            "probability": p_plink,
            "incremental_uplift": uplift_plink,
            "cost_paise": cost_plink,
            "expected_net_value_paise": ev_plink,
        })

        # ---------------------------------------------------------------------
        # 2. RETRY Candidate
        # ---------------------------------------------------------------------
        p_retry = probs.get("RETRY", p0)
        uplift_retry = max(0.0, round(p_retry - p0, 4))
        cost_retry = self.costs.get(InterventionType.RETRY.value, 100)
        ev_retry = compute_incremental_ev(uplift_retry, context.amount_paise, cost_retry)

        retry_cand = InterventionCandidate(
            intervention_type=InterventionType.RETRY,
            execution_capability=ExecutionCapability.NO_OP,  # Standalone retry recommendation
            recovery_probability=p_retry,
            expected_recovery_value=ev_retry,
            reasoning=(
                f"RETRY predicted P={p_retry:.2f} (uplift +{uplift_retry:.2f}), "
                f"net EV ₹{ev_retry/100:.2f} after cost ₹{cost_retry/100:.2f}."
            ),
            model_version=model_version,
        )
        candidates.append(retry_cand)
        candidate_details.append({
            "action": "RETRY",
            "probability": p_retry,
            "incremental_uplift": uplift_retry,
            "cost_paise": cost_retry,
            "expected_net_value_paise": ev_retry,
        })

        # ---------------------------------------------------------------------
        # 3. CUSTOMER_ACTION Candidate
        # ---------------------------------------------------------------------
        p_action = probs.get("CUSTOMER_ACTION", p0)
        uplift_action = max(0.0, round(p_action - p0, 4))
        cost_action = self.costs.get(InterventionType.CUSTOMER_ACTION.value, 200)
        ev_action = compute_incremental_ev(uplift_action, context.amount_paise, cost_action)

        action_cand = InterventionCandidate(
            intervention_type=InterventionType.CUSTOMER_ACTION,
            execution_capability=ExecutionCapability.NO_OP,
            recovery_probability=p_action,
            expected_recovery_value=ev_action,
            reasoning=(
                f"CUSTOMER_ACTION predicted P={p_action:.2f} (uplift +{uplift_action:.2f}), "
                f"net EV ₹{ev_action/100:.2f} after cost ₹{cost_action/100:.2f}."
            ),
            model_version=model_version,
        )
        candidates.append(action_cand)
        candidate_details.append({
            "action": "CUSTOMER_ACTION",
            "probability": p_action,
            "incremental_uplift": uplift_action,
            "cost_paise": cost_action,
            "expected_net_value_paise": ev_action,
        })

        # ---------------------------------------------------------------------
        # 4. STOP Candidate (Terminal Option)
        # ---------------------------------------------------------------------
        stop_cand = InterventionCandidate(
            intervention_type=InterventionType.STOP,
            execution_capability=ExecutionCapability.NO_OP,
            recovery_probability=0.0,
            expected_recovery_value=0,
            reasoning="STOP terminates recovery efforts. Zero cost, zero net EV.",
            model_version=model_version,
        )
        candidates.append(stop_cand)
        candidate_details.append({
            "action": "STOP",
            "probability": 0.0,
            "incremental_uplift": 0.0,
            "cost_paise": 0,
            "expected_net_value_paise": 0,
        })

        # ---------------------------------------------------------------------
        # Deterministic Ranking
        # Primary: expected_recovery_value descending
        # Secondary: recovery_probability descending
        # Tertiary: lower cost
        # ---------------------------------------------------------------------
        def _sort_key(c: InterventionCandidate) -> tuple[int, float, int]:
            cost = self.costs.get(c.intervention_type.value, 0)
            return (c.expected_recovery_value, c.recovery_probability, -cost)

        ranked = sorted(candidates, key=_sort_key, reverse=True)

        # Build Reason Codes
        best_candidate = ranked[0]
        reason_codes: list[str] = []
        if best_candidate.expected_recovery_value > 0:
            reason_codes.append("POSITIVE_INCREMENTAL_VALUE")
        if best_candidate.intervention_type == InterventionType.PAYMENT_LINK:
            reason_codes.append("PAYMENT_LINK_MAX_VALUE")
        elif best_candidate.intervention_type == InterventionType.RETRY:
            reason_codes.append("RETRY_TRANSIENT_BENEFIT")
        elif best_candidate.intervention_type == InterventionType.CUSTOMER_ACTION:
            reason_codes.append("AUTH_REPROMPT_VALUE")
        elif best_candidate.intervention_type == InterventionType.STOP:
            reason_codes.append("NEGATIVE_VALUE_TERMINATION")

        decision_trace = {
            "ranker_version": RANKER_VERSION,
            "baseline_recovery_probability": p0,
            "candidate_evaluations": candidate_details,
            "model_preferred_action": best_candidate.intervention_type.value,
            "preferred_expected_net_value_paise": best_candidate.expected_recovery_value,
            "reason_codes": reason_codes,
        }

        return ranked, decision_trace
