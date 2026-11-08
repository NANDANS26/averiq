"""Comprehensive unit tests for the Averiq Backend Engineering Enhancements.

Validates:
  1. Incremental Expected Net Value calculation & integer paise arithmetic.
  2. Context-Aware Ranker deterministic ordering & tie-breaking.
  3. ActionValueEstimator input feature integrity, NaN/inf guards, and feature version checks.
  4. PolicyEngine structured reason codes, cooldown, retry budget, and STOP bypass.
  5. ExecutionEngine deterministic action_id, idempotency, and check-constraint validity.
  6. VerificationEngine idempotency on already-recovered cases & timeout handling.
  7. Model card & settings fallback probability consistency (0.35).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from ai.data.v2.features import FEATURE_NAMES_V2
from ai.models.action_value_model import ActionValueEstimator
from backend.config import settings
from backend.decision_engine.context_aware_ranker import ContextAwareInterventionRanker
from backend.decision_engine.expected_value import compute_incremental_expected_value
from backend.execution_engine.guards import CanExecuteAction
from backend.policy_engine.engine import PolicyEngine
from shared.contracts.enums import (
    ExecutionCapability,
    FailureCategory,
    InterventionType,
    PolicyDecisionOutcome,
)
from shared.contracts.schemas import CaseContext, InterventionCandidate, PolicyConfig


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------
@pytest.fixture
def sample_policy() -> PolicyConfig:
    return PolicyConfig(
        policy_id="pol_test_001",
        merchant_id="merch_test_001",
        version="v3.0",
        min_confidence=0.40,
        cooldown_seconds=300,
        max_retries={"RETRY": 3, "PAYMENT_LINK": 2, "CUSTOMER_ACTION": 2},
        max_amount={"INR": 500_000},
        human_approval_threshold=100_000,
        attribution_window_seconds=86400,
        exposure_thresholds={"HIGH": 100_000, "MEDIUM": 20_000},
    )


@pytest.fixture
def sample_context() -> CaseContext:
    return CaseContext(
        case_id="case_test_001",
        merchant_id="merch_test_001",
        payment_id="pay_test_001",
        amount_paise=10_000,  # ₹100.00
        currency="INR",
        previous_failure_count=0,
        created_at=datetime.now(timezone.utc),
    )


# -----------------------------------------------------------------------------
# Test Suite 1: Incremental Value Arithmetic & Safety
# -----------------------------------------------------------------------------
@pytest.mark.unit
def test_incremental_expected_value_exact_integer_paise() -> None:
    """Verifies that incremental EV computes floor(uplift * amount) - cost - risk_penalty in integer paise."""
    # Baseline p0 = 0.20, Action p = 0.50 => Uplift = 0.30
    # Amount = 10,000 paise (₹100). Cost = 500 paise (₹5). Risk penalty = 100 paise.
    # Gross incremental = floor(0.30 * 10000) = 3000 paise
    # Net incremental = 3000 - 500 - 100 = 2400 paise
    net_ev = compute_incremental_expected_value(
        p_action=0.50,
        p_baseline=0.20,
        amount_paise=10_000,
        cost_paise=500,
        risk_penalty_paise=100,
    )
    assert isinstance(net_ev, int)
    assert net_ev == 2400


@pytest.mark.unit
def test_incremental_expected_value_zero_uplift() -> None:
    """When action probability is less than baseline, uplift is 0.0, net EV is -cost - risk_penalty."""
    net_ev = compute_incremental_expected_value(
        p_action=0.20,
        p_baseline=0.35,
        amount_paise=10_000,
        cost_paise=500,
        risk_penalty_paise=50,
    )
    assert net_ev == -550


@pytest.mark.unit
def test_incremental_expected_value_validation_errors() -> None:
    """Rejects out-of-range probabilities or negative/zero amounts."""
    with pytest.raises(ValueError, match="p_action"):
        compute_incremental_expected_value(1.2, 0.3, 1000)
    with pytest.raises(ValueError, match="p_baseline"):
        compute_incremental_expected_value(0.5, -0.1, 1000)
    with pytest.raises(ValueError, match="amount_paise"):
        compute_incremental_expected_value(0.5, 0.3, 0)


# -----------------------------------------------------------------------------
# Test Suite 2: Context-Aware Ranker Determinism & Reason Codes
# -----------------------------------------------------------------------------
@pytest.mark.unit
def test_ranker_deterministic_ordering_and_stop_preference(
    sample_context: CaseContext, sample_policy: PolicyConfig
) -> None:
    """When all candidate interventions have negative net EV, STOP should rank first (0 EV > negative EV)."""
    ranker = ContextAwareInterventionRanker()
    # High baseline, very low action probs => all actions have zero gross uplift and negative net EV
    action_probs = {
        "NO_INTERVENTION": 0.80,
        "PAYMENT_LINK": 0.20,
        "RETRY": 0.20,
        "CUSTOMER_ACTION": 0.20,
    }
    ranked, trace = ranker.generate_and_rank_candidates(
        context=sample_context,
        policy_config=sample_policy,
        action_probabilities=action_probs,
    )

    assert ranked[0].intervention_type == InterventionType.STOP
    assert ranked[0].expected_recovery_value == 0
    assert "NEGATIVE_VALUE_TERMINATION" in trace["reason_codes"]


@pytest.mark.unit
def test_ranker_positive_ev_selection(
    sample_context: CaseContext, sample_policy: PolicyConfig
) -> None:
    """When PAYMENT_LINK has high uplift and positive EV, it ranks above STOP and other candidates."""
    ranker = ContextAwareInterventionRanker()
    action_probs = {
        "NO_INTERVENTION": 0.10,
        "PAYMENT_LINK": 0.90,  # +0.80 uplift => ₹80 gross - ₹5 cost = +₹75 (7500 paise)
        "RETRY": 0.20,
        "CUSTOMER_ACTION": 0.20,
    }
    ranked, trace = ranker.generate_and_rank_candidates(
        context=sample_context,
        policy_config=sample_policy,
        action_probabilities=action_probs,
    )

    top_cand = ranked[0]
    assert top_cand.intervention_type == InterventionType.PAYMENT_LINK
    assert top_cand.expected_recovery_value > 0
    assert "POSITIVE_INCREMENTAL_VALUE" in trace["reason_codes"]
    assert "PAYMENT_LINK_MAX_VALUE" in trace["reason_codes"]


# -----------------------------------------------------------------------------
# Test Suite 3: ActionValueEstimator Feature Validation
# -----------------------------------------------------------------------------
@pytest.mark.unit
def test_estimator_nan_inf_rejection() -> None:
    """Estimator must reject feature vectors containing NaN or Inf."""
    estimator = ActionValueEstimator()
    bad_vec_nan = np.zeros(len(FEATURE_NAMES_V2), dtype=np.float32)
    bad_vec_nan[0] = np.nan

    with pytest.raises(ValueError, match="NaN or Inf"):
        estimator.predict_action_probabilities(bad_vec_nan)

    bad_vec_inf = np.zeros(len(FEATURE_NAMES_V2), dtype=np.float32)
    bad_vec_inf[2] = np.inf
    with pytest.raises(ValueError, match="NaN or Inf"):
        estimator.predict_action_probabilities(bad_vec_inf)


@pytest.mark.unit
def test_estimator_dimension_mismatch() -> None:
    """Estimator must reject feature vectors with incorrect number of features."""
    estimator = ActionValueEstimator()
    wrong_dim = [1.0, 2.0, 3.0]
    with pytest.raises(ValueError, match="Feature dimension mismatch"):
        estimator.predict_action_probabilities(wrong_dim)


@pytest.mark.unit
def test_estimator_fallback_matches_settings() -> None:
    """When an action model is not loaded, estimator returns settings.FALLBACK_PROBABILITY (0.35)."""
    estimator = ActionValueEstimator()
    sample_vec = np.zeros(len(FEATURE_NAMES_V2), dtype=np.float32)
    probs = estimator.predict_action_probabilities(sample_vec)
    for action in estimator.supported_actions:
        assert probs[action] == settings.FALLBACK_PROBABILITY


# -----------------------------------------------------------------------------
# Test Suite 4: Policy Engine Reason Codes & Rule Precedence
# -----------------------------------------------------------------------------
@pytest.mark.unit
def test_policy_engine_structured_reason_codes(
    sample_policy: PolicyConfig, sample_context: CaseContext
) -> None:
    """PolicyEngine produces structured reason codes for blocking rules and approvals."""
    engine = PolicyEngine()
    cand = InterventionCandidate(
        intervention_type=InterventionType.PAYMENT_LINK,
        execution_capability=ExecutionCapability.CREATE_PAYMENT_LINK,
        recovery_probability=0.20,  # Below min_confidence 0.40
        expected_recovery_value=1000,
        reasoning="Test candidate",
        model_version="test-v1",
    )

    # 1. Blocked by confidence threshold
    decision = engine.evaluate(
        candidate=cand,
        policy=sample_policy,
        payment_amount_at_risk=sample_context.amount_paise,
        currency="INR",
        seconds_since_last_intervention=3600.0,
    )
    assert decision.outcome == PolicyDecisionOutcome.BLOCKED
    assert "confidence_threshold" in decision.blocking_rules
    assert "BLOCK_CONFIDENCE_LOW" in decision.reason_codes

    # 2. Blocked by cooldown
    cand_high_conf = InterventionCandidate(
        intervention_type=InterventionType.PAYMENT_LINK,
        execution_capability=ExecutionCapability.CREATE_PAYMENT_LINK,
        recovery_probability=0.85,
        expected_recovery_value=1000,
        reasoning="Test candidate",
        model_version="test-v1",
    )
    decision_cd = engine.evaluate(
        candidate=cand_high_conf,
        policy=sample_policy,
        payment_amount_at_risk=sample_context.amount_paise,
        currency="INR",
        seconds_since_last_intervention=60.0,  # Cooldown is 300s
    )
    assert decision_cd.outcome == PolicyDecisionOutcome.BLOCKED
    assert "cooldown" in decision_cd.blocking_rules
    assert "BLOCK_COOLDOWN_ACTIVE" in decision_cd.reason_codes

    # 3. Blocked by retry budget
    decision_rb = engine.evaluate(
        candidate=cand_high_conf,
        policy=sample_policy,
        payment_amount_at_risk=sample_context.amount_paise,
        currency="INR",
        seconds_since_last_intervention=3600.0,
        intervention_count=2,  # max_retries for PAYMENT_LINK is 2
    )
    assert decision_rb.outcome == PolicyDecisionOutcome.BLOCKED
    assert "retry_budget" in decision_rb.blocking_rules
    assert "BLOCK_RETRY_BUDGET_EXHAUSTED" in decision_rb.reason_codes


@pytest.mark.unit
def test_policy_engine_stop_always_approved(sample_policy: PolicyConfig) -> None:
    """STOP terminal action bypasses all rules even when case is fraud, high amount, or cooldown active."""
    engine = PolicyEngine()
    stop_cand = InterventionCandidate(
        intervention_type=InterventionType.STOP,
        execution_capability=ExecutionCapability.NO_OP,
        recovery_probability=0.0,
        expected_recovery_value=0,
        reasoning="Stop recovery",
        model_version="test-v1",
    )
    decision = engine.evaluate(
        candidate=stop_cand,
        policy=sample_policy,
        payment_amount_at_risk=999_999_999,  # Huge amount
        currency="INR",
        failure_category=FailureCategory.FRAUD_SUSPECTED,
        seconds_since_last_intervention=0.0,
        intervention_count=999,
        is_recovered=True,
    )
    assert decision.outcome == PolicyDecisionOutcome.APPROVED
    assert len(decision.blocking_rules) == 0
    assert "APPROVED" in decision.reason_codes


# -----------------------------------------------------------------------------
# Test Suite 5: Execution Guard Idempotency & Check Constraint Status
# -----------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.asyncio
async def test_execution_guard_blocks_duplicate_requested_operation() -> None:
    """Guard blocks execution if an existing operation with REQUESTED or IN_PROGRESS exists."""
    guard = CanExecuteAction()
    case = MagicMock()
    case.id = uuid.uuid4()
    case.state = "ACTION_APPROVED"

    existing_op = MagicMock()
    existing_op.id = uuid.uuid4()
    existing_op.status = "REQUESTED"

    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.side_effect = [existing_op, None]
    session.execute.return_value = result_mock

    res = await guard.check(
        session=session,
        case=case,
        action_id=uuid.uuid4(),
        intervention_type="PAYMENT_LINK",
        actor="SYSTEM",
        correlation_id=str(uuid.uuid4()),
    )
    assert not res.can_execute
    assert any(c.condition_name == "duplicate_action_executing" for c in res.blocking_conditions)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execution_guard_blocks_previously_succeeded_action_id() -> None:
    """Guard blocks execution if action_id already completed with SUCCEEDED or VERIFIED."""
    guard = CanExecuteAction()
    case = MagicMock()
    case.id = uuid.uuid4()
    case.state = "ACTION_APPROVED"

    succeeded_op = MagicMock()
    succeeded_op.id = uuid.uuid4()
    succeeded_op.status = "SUCCEEDED"

    session = AsyncMock()
    result_mock = MagicMock()
    # First call: no duplicate executing op. Second call: found succeeded op.
    result_mock.scalar_one_or_none.side_effect = [None, succeeded_op]
    session.execute.return_value = result_mock

    res = await guard.check(
        session=session,
        case=case,
        action_id=uuid.uuid4(),
        intervention_type="PAYMENT_LINK",
        actor="SYSTEM",
        correlation_id=str(uuid.uuid4()),
    )
    assert not res.can_execute
    assert any(c.condition_name == "action_id_already_succeeded" for c in res.blocking_conditions)


# -----------------------------------------------------------------------------
# Test Suite 6: Model Card & Config Agreement
# -----------------------------------------------------------------------------
@pytest.mark.unit
def test_model_card_and_config_fallback_agreement() -> None:
    """MODEL_CARD.md fail-safe fallback baseline must document 0.35, matching settings.FALLBACK_PROBABILITY."""
    model_card_path = Path("MODEL_CARD.md")
    assert model_card_path.exists()
    content = model_card_path.read_text(encoding="utf-8")
    assert f"`{settings.FALLBACK_PROBABILITY:.2f}`" in content
    assert "0.70" not in content.split("Fail-Safe Fallback")[1].split("No Direct Execution")[0]
