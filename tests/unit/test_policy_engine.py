"""Unit tests for PolicyEngine rule evaluation order and boundary conditions.

Tests:
- One test per rule covering the boundary condition
- Rule evaluation order: payment_already_recovered fires before confidence_threshold
- STOP intervention bypasses all rules → APPROVED
- fraud_block blocks when failure_category == FRAUD_SUSPECTED
- human_approval routes to HUMAN_REVIEW when amount > threshold

Requirements: FR-POLICY-001 through FR-POLICY-008
"""
from __future__ import annotations

import uuid

import pytest

from shared.contracts.enums import (
    ExecutionCapability,
    FailureCategory,
    InterventionType,
    PolicyDecisionOutcome,
)
from shared.contracts.schemas import InterventionCandidate, PolicyConfig
from backend.policy_engine.engine import PolicyDecision, PolicyEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy(**overrides):
    """Build a PolicyConfig with sensible defaults, accepting named overrides."""
    defaults = dict(
        policy_id=str(uuid.uuid4()),
        merchant_id=str(uuid.uuid4()),
        version="v1",
        min_confidence=0.5,
        cooldown_seconds=0,
        max_retries={},
        max_amount={"INR": 1_000_000},
        human_approval_threshold=500_000,
        attribution_window_seconds=86400,
        exposure_thresholds={"HIGH": 100_000, "MEDIUM": 20_000},
    )
    defaults.update(overrides)
    return PolicyConfig(**defaults)


def _make_candidate(
    prob: float = 0.8,
    ev: int = 50_000,
    itype: InterventionType = InterventionType.PAYMENT_LINK,
) -> InterventionCandidate:
    """Build an InterventionCandidate with sensible defaults."""
    return InterventionCandidate(
        intervention_type=itype,
        execution_capability=ExecutionCapability.CREATE_PAYMENT_LINK,
        recovery_probability=prob,
        expected_recovery_value=ev,
        model_version="v1.0",
    )


engine = PolicyEngine()


# ---------------------------------------------------------------------------
# Test 1: confidence_threshold blocks when probability < min_confidence
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_confidence_threshold_blocks_when_below_min() -> None:
    """confidence_threshold rule blocks when recovery_probability < min_confidence.

    Boundary: 0.4 < 0.5 (min_confidence) → BLOCKED.

    Requirements: FR-POLICY-002
    """
    policy = _make_policy(min_confidence=0.5)
    candidate = _make_candidate(prob=0.4)

    result = engine.evaluate(
        candidate=candidate,
        policy=policy,
        payment_amount_at_risk=100_000,
        currency="INR",
    )

    assert result.outcome == PolicyDecisionOutcome.BLOCKED
    assert "confidence_threshold" in result.blocking_rules


# ---------------------------------------------------------------------------
# Test 2: confidence_threshold allows at exact boundary (0.5 >= 0.5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_confidence_threshold_allows_at_exact_boundary() -> None:
    """confidence_threshold does NOT block when recovery_probability == min_confidence.

    Boundary: 0.5 >= 0.5 → rule does not fire.

    Requirements: FR-POLICY-002
    """
    policy = _make_policy(min_confidence=0.5)
    candidate = _make_candidate(prob=0.5)

    result = engine.evaluate(
        candidate=candidate,
        policy=policy,
        payment_amount_at_risk=100_000,
        currency="INR",
    )

    assert result.outcome == PolicyDecisionOutcome.APPROVED
    assert "confidence_threshold" not in result.blocking_rules


# ---------------------------------------------------------------------------
# Test 3: amount_limit blocks when payment_amount_at_risk > max_amount
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_amount_limit_blocks_when_above_max() -> None:
    """amount_limit rule blocks when payment_amount_at_risk > max_amount for currency.

    Boundary: 1_000_001 > 1_000_000 → BLOCKED.

    Requirements: FR-POLICY-003
    """
    policy = _make_policy(max_amount={"INR": 1_000_000})
    candidate = _make_candidate(prob=0.8)

    result = engine.evaluate(
        candidate=candidate,
        policy=policy,
        payment_amount_at_risk=1_000_001,
        currency="INR",
    )

    assert result.outcome == PolicyDecisionOutcome.BLOCKED
    assert "amount_limit" in result.blocking_rules


# ---------------------------------------------------------------------------
# Test 4: amount_limit allows when amount == max_amount (boundary)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_amount_limit_allows_at_exact_boundary() -> None:
    """amount_limit does NOT block when payment_amount_at_risk == max_amount.

    Boundary: 1_000_000 == 1_000_000 → rule does not fire (> is strict).

    Requirements: FR-POLICY-003
    """
    policy = _make_policy(max_amount={"INR": 1_000_000})
    candidate = _make_candidate(prob=0.8)

    result = engine.evaluate(
        candidate=candidate,
        policy=policy,
        payment_amount_at_risk=1_000_000,
        currency="INR",
    )

    assert "amount_limit" not in result.blocking_rules


# ---------------------------------------------------------------------------
# Test 5: retry_budget blocks when intervention_count >= max_retries
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_retry_budget_blocks_when_count_at_limit() -> None:
    """retry_budget rule blocks when intervention_count >= max_retries.

    Boundary: count=3 >= max_retries=3 → BLOCKED.

    Requirements: FR-POLICY-004
    """
    policy = _make_policy(max_retries={"PAYMENT_LINK": 3})
    candidate = _make_candidate(prob=0.8, itype=InterventionType.PAYMENT_LINK)

    result = engine.evaluate(
        candidate=candidate,
        policy=policy,
        payment_amount_at_risk=100_000,
        currency="INR",
        intervention_count=3,
    )

    assert result.outcome == PolicyDecisionOutcome.BLOCKED
    assert "retry_budget" in result.blocking_rules


# ---------------------------------------------------------------------------
# Test 6: retry_budget allows when count == max_retries - 1
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_retry_budget_allows_when_one_below_limit() -> None:
    """retry_budget does NOT block when intervention_count == max_retries - 1.

    Boundary: count=2 < max_retries=3 → rule does not fire.

    Requirements: FR-POLICY-004
    """
    policy = _make_policy(max_retries={"PAYMENT_LINK": 3})
    candidate = _make_candidate(prob=0.8, itype=InterventionType.PAYMENT_LINK)

    result = engine.evaluate(
        candidate=candidate,
        policy=policy,
        payment_amount_at_risk=100_000,
        currency="INR",
        intervention_count=2,
    )

    assert "retry_budget" not in result.blocking_rules


# ---------------------------------------------------------------------------
# Test 7: cooldown blocks when seconds_since_last < cooldown_seconds
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cooldown_blocks_when_within_cooldown_window() -> None:
    """cooldown rule blocks when seconds_since_last_intervention < cooldown_seconds.

    Scenario: 10 seconds elapsed but cooldown requires 60 seconds → BLOCKED.

    Requirements: FR-POLICY-005
    """
    policy = _make_policy(cooldown_seconds=60)
    candidate = _make_candidate(prob=0.8)

    result = engine.evaluate(
        candidate=candidate,
        policy=policy,
        payment_amount_at_risk=100_000,
        currency="INR",
        seconds_since_last_intervention=10.0,
    )

    assert result.outcome == PolicyDecisionOutcome.BLOCKED
    assert "cooldown" in result.blocking_rules


# ---------------------------------------------------------------------------
# Test 8: human_approval routes to HUMAN_REVIEW when amount > threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_human_approval_routes_to_human_review() -> None:
    """human_approval rule routes to HUMAN_REVIEW when amount > human_approval_threshold.

    Scenario: amount=600_000 > threshold=500_000 → HUMAN_REVIEW.

    Requirements: FR-POLICY-007
    """
    policy = _make_policy(human_approval_threshold=500_000)
    candidate = _make_candidate(prob=0.8)

    result = engine.evaluate(
        candidate=candidate,
        policy=policy,
        payment_amount_at_risk=600_000,
        currency="INR",
    )

    assert result.outcome == PolicyDecisionOutcome.HUMAN_REVIEW


# ---------------------------------------------------------------------------
# Test 9: STOP intervention type → APPROVED (bypasses all rules)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stop_intervention_bypasses_all_rules() -> None:
    """STOP intervention type is always APPROVED, regardless of any blocking conditions.

    Even when multiple rules would otherwise block (low confidence, high amount,
    fraud suspected), STOP goes straight to APPROVED.  Stopping recovery is
    always permitted.

    Requirements: FR-POLICY-001
    """
    # Configure policy so that multiple rules would fire for a normal intervention
    policy = _make_policy(
        min_confidence=0.9,       # would block confidence_threshold
        cooldown_seconds=3600,    # would block cooldown
        human_approval_threshold=10_000,  # would trigger human_review
    )
    stop_candidate = _make_candidate(
        prob=0.1,                 # below min_confidence
        itype=InterventionType.STOP,
    )

    result = engine.evaluate(
        candidate=stop_candidate,
        policy=policy,
        payment_amount_at_risk=500_000,   # above human_approval_threshold
        currency="INR",
        failure_category=FailureCategory.FRAUD_SUSPECTED,  # would trigger fraud_block
        seconds_since_last_intervention=0.0,  # within cooldown
    )

    assert result.outcome == PolicyDecisionOutcome.APPROVED
    assert result.blocking_rules == []


# ---------------------------------------------------------------------------
# Test 10: payment_already_recovered fires before confidence_threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_payment_already_recovered_fires_before_confidence_threshold() -> None:
    """payment_already_recovered blocks even when confidence is below threshold.

    When is_recovered=True AND probability < min_confidence, both rules fire.
    The payment_already_recovered rule must appear in blocking_rules because it
    is evaluated first (rule precedence order).

    Requirements: FR-POLICY-001, FR-POLICY-002
    """
    policy = _make_policy(min_confidence=0.5)
    # Low probability would trigger confidence_threshold, but recovery takes precedence
    candidate = _make_candidate(prob=0.1)

    result = engine.evaluate(
        candidate=candidate,
        policy=policy,
        payment_amount_at_risk=100_000,
        currency="INR",
        is_recovered=True,
    )

    assert result.outcome == PolicyDecisionOutcome.BLOCKED
    # payment_already_recovered must be in blocking rules
    assert "payment_already_recovered" in result.blocking_rules
    # confidence_threshold also fires (both are collected)
    assert "confidence_threshold" in result.blocking_rules
    # payment_already_recovered appears first (rule order)
    assert result.blocking_rules.index("payment_already_recovered") < result.blocking_rules.index("confidence_threshold")


# ---------------------------------------------------------------------------
# Bonus: fraud_block blocks when failure_category == FRAUD_SUSPECTED
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fraud_block_blocks_when_fraud_suspected() -> None:
    """fraud_block rule blocks when failure_category is FRAUD_SUSPECTED.

    No automated intervention should proceed when fraud is detected.

    Requirements: FR-POLICY-006
    """
    policy = _make_policy()
    candidate = _make_candidate(prob=0.8)

    result = engine.evaluate(
        candidate=candidate,
        policy=policy,
        payment_amount_at_risk=100_000,
        currency="INR",
        failure_category=FailureCategory.FRAUD_SUSPECTED,
    )

    assert result.outcome == PolicyDecisionOutcome.BLOCKED
    assert "fraud_block" in result.blocking_rules


@pytest.mark.unit
def test_fraud_block_does_not_fire_for_non_fraud_categories() -> None:
    """fraud_block rule does NOT fire for non-FRAUD failure categories."""
    policy = _make_policy()
    candidate = _make_candidate(prob=0.8)

    for category in [
        FailureCategory.INSUFFICIENT_FUNDS,
        FailureCategory.EXPIRED_CARD,
        FailureCategory.NETWORK_TIMEOUT,
        FailureCategory.TECHNICAL_FAILURE,
    ]:
        result = engine.evaluate(
            candidate=candidate,
            policy=policy,
            payment_amount_at_risk=100_000,
            currency="INR",
            failure_category=category,
        )
        assert "fraud_block" not in result.blocking_rules, (
            f"fraud_block should not fire for {category}"
        )
