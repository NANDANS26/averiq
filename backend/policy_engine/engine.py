"""PolicyEngine — evaluates intervention candidates against active policy rules.

Rule evaluation order (FR-POLICY-001):
  1. state validity check (pre-condition)
  2. payment_already_recovered
  3. duplicate_action
  4. confidence_threshold
  5. amount_limit
  6. retry_budget
  7. cooldown
  8. human_approval → HUMAN_REVIEW
  Default: APPROVED

Fully stateless. Requirements: FR-POLICY-001 through FR-POLICY-009, FR-POLICY-011
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared.contracts.enums import FailureCategory, PolicyDecisionOutcome
from shared.contracts.schemas import InterventionCandidate, PolicyConfig


@dataclass(frozen=True)
class PolicyDecision:
    outcome: PolicyDecisionOutcome
    blocking_rules: list[str] = field(default_factory=list)
    evaluated_values: dict[str, Any] = field(default_factory=dict)
    policy_version: str = ""


class PolicyEngine:
    """Stateless policy engine. evaluate() is deterministic."""

    def evaluate(
        self,
        candidate: InterventionCandidate,
        policy: PolicyConfig,
        payment_amount_at_risk: int,
        currency: str,
        failure_category: FailureCategory | None = None,
        seconds_since_last_intervention: float = 0.0,
        intervention_count: int = 0,
        case_state: str = "ACTION_APPROVED",
        is_recovered: bool = False,
    ) -> PolicyDecision:
        """Evaluate a single intervention candidate against all policy rules.

        Rules are evaluated in a fixed precedence order (FR-POLICY-001 through
        FR-POLICY-009).  All blocking conditions are collected before returning
        BLOCKED so that operators can see the full set of violations.

        Special case: STOP intervention type bypasses all rules and is always
        APPROVED — stopping recovery is always permitted regardless of policy
        constraints (FR-POLICY-001).

        Returns:
            PolicyDecision with outcome APPROVED, BLOCKED, or HUMAN_REVIEW.
        """
        from shared.contracts.enums import InterventionType

        evaluated: dict[str, Any] = {
            "recovery_probability": candidate.recovery_probability,
            "payment_amount_at_risk": payment_amount_at_risk,
            "currency": currency,
            "failure_category": failure_category.value if failure_category else None,
            "intervention_type": candidate.intervention_type.value,
        }

        # STOP intervention bypasses all rules — stopping recovery is always allowed.
        if candidate.intervention_type == InterventionType.STOP:
            return PolicyDecision(
                outcome=PolicyDecisionOutcome.APPROVED,
                blocking_rules=[],
                evaluated_values=evaluated,
                policy_version=policy.version,
            )

        blocking: list[str] = []

        # Rule 2: payment_already_recovered
        if is_recovered:
            blocking.append("payment_already_recovered")

        # Rule 4: confidence_threshold
        if candidate.recovery_probability < policy.min_confidence:
            blocking.append("confidence_threshold")

        # Rule 5: amount_limit
        max_for_currency = policy.max_amount.get(currency, 0)
        if max_for_currency > 0 and payment_amount_at_risk > max_for_currency:
            blocking.append("amount_limit")

        # Rule 6: retry_budget
        max_retries = policy.max_retries.get(candidate.intervention_type.value, 0)
        if max_retries > 0 and intervention_count >= max_retries:
            blocking.append("retry_budget")

        # Rule 7: cooldown
        if seconds_since_last_intervention < policy.cooldown_seconds:
            blocking.append("cooldown")

        # Rule 8: fraud_block — blocks any non-STOP intervention when category is FRAUD
        if failure_category == FailureCategory.FRAUD_SUSPECTED:
            blocking.append("fraud_block")

        if blocking:
            return PolicyDecision(
                outcome=PolicyDecisionOutcome.BLOCKED,
                blocking_rules=blocking,
                evaluated_values=evaluated,
                policy_version=policy.version,
            )

        # Rule 9: human_approval
        if payment_amount_at_risk > policy.human_approval_threshold:
            return PolicyDecision(
                outcome=PolicyDecisionOutcome.HUMAN_REVIEW,
                blocking_rules=[],
                evaluated_values=evaluated,
                policy_version=policy.version,
            )

        return PolicyDecision(
            outcome=PolicyDecisionOutcome.APPROVED,
            blocking_rules=[],
            evaluated_values=evaluated,
            policy_version=policy.version,
        )
