"""End-to-end diagnosis pipeline tests.

Tests the full chain:
  CaseContext → StubAIProvider → validate_failure_category → DiagnosisOutput
  CaseContext → DeterministicFallbackClassifier → DiagnosisOutput
  CaseContext + timeout → RootCauseReasoner → fallback → DiagnosisOutput

All tests are unit-level (mocked, no DB/Redis).

Requirements: FR-DIAG-001, FR-DIAG-003, FR-DIAG-004, FR-DIAG-005,
              FR-DIAG-007, SEC-AI-001, SEC-AI-002, AI-PROV-001, AI-PROV-002
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from ai.providers.stub import StubAIProvider
from backend.decision_engine.fallback_classifier import DeterministicFallbackClassifier
from backend.decision_engine.ai_output_validator import validate_failure_category
from shared.contracts.enums import FailureCategory
from shared.contracts.schemas import CaseContext, DiagnosisOutput


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_context(error_code: str | None = "INSUFFICIENT_FUNDS") -> CaseContext:
    """Build a minimal CaseContext for testing."""
    return CaseContext(
        case_id=str(uuid.uuid4()),
        merchant_id=str(uuid.uuid4()),
        payment_id="pay_test001",
        amount_paise=50000,
        currency="INR",
        error_code=error_code,
        previous_failure_count=0,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Test 1 — StubAIProvider full chain produces valid DiagnosisOutput
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stub_provider_produces_valid_diagnosis() -> None:
    """StubAIProvider.diagnose() should return a valid, schema-compliant DiagnosisOutput.

    Verifies the full chain:
      CaseContext → StubAIProvider.diagnose() → DiagnosisOutput
      DiagnosisOutput.failure_category → validate_failure_category() → FailureCategory

    Requirements: FR-DIAG-007, AI-PROV-001, SEC-AI-001
    """
    provider = StubAIProvider()
    context = _make_context("INSUFFICIENT_FUNDS")
    result = await provider.diagnose(context)

    assert isinstance(result, DiagnosisOutput)
    assert result.failure_category == FailureCategory.INSUFFICIENT_FUNDS
    assert result.ai_source == "StubAIProvider"
    assert 0.0 <= result.confidence_score <= 1.0

    # Validate the output passes the allowlist validator
    validated = validate_failure_category(result.failure_category.value)
    assert validated == FailureCategory.INSUFFICIENT_FUNDS


# ---------------------------------------------------------------------------
# Test 2 — Fallback classifier full chain
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_fallback_classifier_full_chain() -> None:
    """DeterministicFallbackClassifier.classify() should handle all known error codes.

    Verifies:
      - Each known error_code maps to the expected FailureCategory
      - ai_source is always "DeterministicFallback"
      - All outputs pass the FailureCategory allowlist validator
      - None error_code maps to UNKNOWN

    Requirements: FR-DIAG-004, FR-DIAG-006, SEC-AI-001
    """
    classifier = DeterministicFallbackClassifier()

    for error_code, expected_category in [
        ("INSUFFICIENT_FUNDS", FailureCategory.INSUFFICIENT_FUNDS),
        ("CARD_DECLINED", FailureCategory.CARD_DECLINED),
        ("EXPIRED_CARD", FailureCategory.EXPIRED_CARD),
        ("AUTH_FAILED", FailureCategory.AUTHENTICATION_FAILURE),
        ("GATEWAY_ERROR", FailureCategory.TECHNICAL_FAILURE),
        ("NETWORK_ERROR", FailureCategory.NETWORK_TIMEOUT),
        ("FRAUD_DETECTED", FailureCategory.FRAUD_SUSPECTED),
        ("DAILY_LIMIT_EXCEEDED", FailureCategory.LIMIT_EXCEEDED),
        (None, FailureCategory.UNKNOWN),
    ]:
        result = classifier.classify(error_code=error_code)
        assert result.failure_category == expected_category, (
            f"error_code={error_code!r} expected {expected_category}, got {result.failure_category}"
        )
        assert result.ai_source == "DeterministicFallback", (
            f"Expected ai_source='DeterministicFallback' for error_code={error_code!r}"
        )

        # All outputs pass allowlist validation
        validated = validate_failure_category(result.failure_category.value)
        assert validated is not None, (
            f"validate_failure_category returned None for {result.failure_category!r}"
        )


# ---------------------------------------------------------------------------
# Test 3 — RootCauseReasoner with StubAIProvider (success path)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_root_cause_reasoner_success_path() -> None:
    """RootCauseReasoner should use AI provider and not fall back on success.

    Verifies the full happy path:
      CaseContext → RootCauseReasoner(StubAIProvider) → DiagnosisResult
      used_fallback=False, ai_provider_id="StubAIProvider", ai_timeout=False

    Requirements: FR-DIAG-001, FR-DIAG-003, FR-DIAG-007, AI-PROV-002
    """
    try:
        from backend.decision_engine.root_cause_reasoner import RootCauseReasoner
    except ImportError:
        pytest.skip("RootCauseReasoner not yet implemented")

    provider = StubAIProvider()
    reasoner = RootCauseReasoner(ai_provider=provider, timeout_seconds=5.0)
    context = _make_context("CARD_DECLINED")

    result = await reasoner.diagnose(context)

    assert isinstance(result.diagnosis, DiagnosisOutput)
    assert result.used_fallback is False
    assert result.ai_provider_id == "StubAIProvider"
    assert result.ai_timeout is False


# ---------------------------------------------------------------------------
# Test 4 — RootCauseReasoner falls back on timeout
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_root_cause_reasoner_falls_back_on_timeout() -> None:
    """RootCauseReasoner should invoke the deterministic fallback on AI timeout.

    Verifies the timeout fallback chain:
      CaseContext → RootCauseReasoner(slow_provider) → ai_timeout=True →
      DeterministicFallbackClassifier → DiagnosisResult(used_fallback=True)

    Requirements: FR-DIAG-004, FR-DIAG-005, FR-DIAG-006, AI-PROV-002
    """
    try:
        from backend.decision_engine.root_cause_reasoner import RootCauseReasoner
    except ImportError:
        pytest.skip("RootCauseReasoner not yet implemented")

    from unittest.mock import AsyncMock

    # Provider that always times out
    slow_provider = AsyncMock()
    slow_provider.diagnose = AsyncMock(side_effect=asyncio.TimeoutError("simulated"))

    reasoner = RootCauseReasoner(ai_provider=slow_provider, timeout_seconds=0.001)
    context = _make_context("GATEWAY_ERROR")

    result = await reasoner.diagnose(context)

    assert result.used_fallback is True
    assert result.ai_timeout is True
    assert result.diagnosis.ai_source == "DeterministicFallback"


# ---------------------------------------------------------------------------
# Test 5 — All DiagnosisOutput objects are valid Pydantic models
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_all_outputs_are_valid_pydantic_models() -> None:
    """All diagnosis pipeline outputs should be valid Pydantic models with round-trip stability.

    Verifies:
      - StubAIProvider output is a valid DiagnosisOutput and survives model_dump() round-trip
      - DeterministicFallbackClassifier output is a valid DiagnosisOutput and survives round-trip
      - Both work for known codes, None codes, and unknown codes

    Requirements: FR-DIAG-003, NFR-TEST-008, SEC-AI-001
    """
    provider = StubAIProvider()
    classifier = DeterministicFallbackClassifier()

    for error_code in ["INSUFFICIENT_FUNDS", "CARD_DECLINED", None, "UNKNOWN_XYZ"]:
        context = _make_context(error_code)

        # AI provider output
        ai_result = await provider.diagnose(context)
        assert isinstance(ai_result, DiagnosisOutput), (
            f"StubAIProvider output for error_code={error_code!r} is not DiagnosisOutput"
        )
        # Re-validation round-trip
        DiagnosisOutput.model_validate(ai_result.model_dump())

        # Fallback classifier output
        fallback_result = classifier.classify(error_code=error_code)
        assert isinstance(fallback_result, DiagnosisOutput), (
            f"DeterministicFallbackClassifier output for error_code={error_code!r} is not DiagnosisOutput"
        )
        DiagnosisOutput.model_validate(fallback_result.model_dump())
