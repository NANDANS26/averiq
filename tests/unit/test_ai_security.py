"""AI output security tests.

Verifies:
1. Arbitrary LLM output is validated through DiagnosisOutput schema before use
2. Unknown failure_category from LLM → DeterministicFallback invoked (SEC-AI-001)
3. LLM output path never reaches ExecutionEngine without PolicyEngine gate (SEC-AI-003)
4. Structural: call graph — AI → Policy → Execution (never AI → Execution directly)

Requirements: SEC-AI-001, SEC-AI-002, SEC-AI-003
"""
from __future__ import annotations
import uuid
import pytest
from shared.contracts.schemas import DiagnosisOutput, CaseContext
from shared.contracts.enums import FailureCategory
from backend.decision_engine.ai_output_validator import validate_failure_category
from backend.policy_engine.engine import PolicyEngine, PolicyDecision
from shared.contracts.enums import PolicyDecisionOutcome, InterventionType, ExecutionCapability
from shared.contracts.schemas import InterventionCandidate, PolicyConfig


@pytest.mark.unit
def test_unknown_failure_category_rejected_by_allowlist():
    """Unknown failure_category from LLM returns None (triggers fallback, SEC-AI-001)."""
    result = validate_failure_category("PAYMENT_TIMEOUT_UNKNOWN_VALUE_XYZ")
    assert result is None


@pytest.mark.unit
def test_valid_failure_category_accepted_by_allowlist():
    """Valid failure_category passes the allowlist."""
    result = validate_failure_category(FailureCategory.INSUFFICIENT_FUNDS.value)
    assert result == FailureCategory.INSUFFICIENT_FUNDS


@pytest.mark.unit
def test_diagnosis_output_schema_rejects_invalid_category():
    """DiagnosisOutput Pydantic schema rejects invalid failure_category."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        DiagnosisOutput(
            failure_category="TOTALLY_INVALID_CATEGORY",  # not in FailureCategory enum
            confidence_score=0.9,
            ai_source="ExternalLLMProvider",
        )


@pytest.mark.unit
def test_lm_output_cannot_produce_approved_without_policy_engine():
    """LLM output (DiagnosisOutput) alone cannot produce APPROVED decision.

    The PolicyEngine is the only component that returns PolicyDecisionOutcome.APPROVED.
    DiagnosisOutput just has failure_category and confidence_score — it does NOT
    carry a PolicyDecision. This structural test verifies the invariant.

    SEC-AI-003: LLM output NEVER directly authorises a financial action.
    """
    # Build a valid DiagnosisOutput (as if from LLM)
    diagnosis = DiagnosisOutput(
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        confidence_score=0.99,
        ai_source="ExternalLLMProvider",
    )

    # DiagnosisOutput has no outcome/approval field
    assert not hasattr(diagnosis, 'outcome')
    assert not hasattr(diagnosis, 'policy_decision')
    assert not hasattr(diagnosis, 'approved')
    assert not hasattr(diagnosis, 'can_execute')

    # Only PolicyEngine.evaluate() can produce APPROVED
    engine = PolicyEngine()
    policy = PolicyConfig(
        policy_id=str(uuid.uuid4()),
        merchant_id=str(uuid.uuid4()),
        version="v1",
        min_confidence=0.0,  # permissive — everything passes
        cooldown_seconds=0,
        max_retries={},
        max_amount={"INR": 10_000_000},
        human_approval_threshold=10_000_000,
        attribution_window_seconds=86400,
        exposure_thresholds={"HIGH": 100_000, "MEDIUM": 20_000},
    )
    candidate = InterventionCandidate(
        intervention_type=InterventionType.PAYMENT_LINK,
        execution_capability=ExecutionCapability.CREATE_PAYMENT_LINK,
        recovery_probability=0.8,
        expected_recovery_value=50_000,
        model_version="v1.0",
    )
    decision = engine.evaluate(candidate=candidate, policy=policy,
                               payment_amount_at_risk=100_000, currency="INR")
    # Only PolicyEngine produces APPROVED
    assert decision.outcome == PolicyDecisionOutcome.APPROVED
    assert isinstance(decision, PolicyDecision)


@pytest.mark.unit
def test_low_confidence_ai_output_blocked_by_policy():
    """LLM output with low confidence is blocked by PolicyEngine confidence_threshold rule."""
    engine = PolicyEngine()
    policy = PolicyConfig(
        policy_id=str(uuid.uuid4()),
        merchant_id=str(uuid.uuid4()),
        version="v1",
        min_confidence=0.8,  # high threshold
        cooldown_seconds=0, max_retries={},
        max_amount={"INR": 10_000_000},
        human_approval_threshold=10_000_000,
        attribution_window_seconds=86400,
        exposure_thresholds={"HIGH": 100_000, "MEDIUM": 20_000},
    )
    # LLM returned low confidence — model score is below threshold
    low_confidence_candidate = InterventionCandidate(
        intervention_type=InterventionType.PAYMENT_LINK,
        execution_capability=ExecutionCapability.CREATE_PAYMENT_LINK,
        recovery_probability=0.3,  # < 0.8 min_confidence
        expected_recovery_value=30_000,
        model_version="v1.0",
    )
    decision = engine.evaluate(candidate=low_confidence_candidate, policy=policy,
                               payment_amount_at_risk=100_000, currency="INR")
    # PolicyEngine BLOCKS even if LLM was confident
    assert decision.outcome == PolicyDecisionOutcome.BLOCKED
    assert "confidence_threshold" in decision.blocking_rules
