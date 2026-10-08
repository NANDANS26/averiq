"""Unit tests for StubAIProvider (task 9.1).

Tests verify that:
  1. Known error codes map to the correct FailureCategory.
  2. A None error_code returns FailureCategory.UNKNOWN.
  3. A pre-classified failure_category is used directly (confidence 0.85).
  4. ai_source is always "StubAIProvider".
  5. raw_response is always None.
  6. StubAIProvider satisfies the AIProvider runtime-checkable protocol.
  7. Normalization handles whitespace, mixed case, and hyphens.
  8. Unknown error codes return FailureCategory.UNKNOWN with low confidence.

All tests are pure unit tests — no database, no Redis, no external calls.

Requirements: FR-DIAG-007, AI-PROV-001
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ai.providers.stub import StubAIProvider
from shared.contracts.enums import FailureCategory
from shared.contracts.protocols import AIProvider
from shared.contracts.schemas import CaseContext, DiagnosisOutput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    error_code: str | None = None,
    failure_category: FailureCategory | None = None,
) -> CaseContext:
    """Build a minimal CaseContext for testing."""
    return CaseContext(
        case_id="case-test-001",
        merchant_id="merchant-test-001",
        payment_id="pay_test001",
        amount_paise=10000,
        currency="INR",
        error_code=error_code,
        failure_category=failure_category,
        previous_failure_count=0,
        created_at=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stub_ai_provider_satisfies_protocol() -> None:
    """StubAIProvider must satisfy the runtime-checkable AIProvider protocol."""
    provider = StubAIProvider()
    assert isinstance(provider, AIProvider)


# ---------------------------------------------------------------------------
# ai_source always "StubAIProvider"
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_source_is_always_stub() -> None:
    """ai_source must always be 'StubAIProvider' regardless of input."""
    provider = StubAIProvider()
    context = _make_context(error_code="INSUFFICIENT_FUNDS")
    result = await provider.diagnose(context)
    assert result.ai_source == "StubAIProvider"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_source_is_stub_on_unknown_code() -> None:
    """ai_source must be 'StubAIProvider' even for unrecognized error codes."""
    provider = StubAIProvider()
    context = _make_context(error_code="SOME_TOTALLY_UNKNOWN_CODE_XYZ")
    result = await provider.diagnose(context)
    assert result.ai_source == "StubAIProvider"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_source_is_stub_when_no_error_code() -> None:
    """ai_source must be 'StubAIProvider' when error_code is None."""
    provider = StubAIProvider()
    context = _make_context(error_code=None)
    result = await provider.diagnose(context)
    assert result.ai_source == "StubAIProvider"


# ---------------------------------------------------------------------------
# raw_response always None
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_response_is_always_none() -> None:
    """raw_response must always be None — no external calls are made."""
    provider = StubAIProvider()

    for error_code in ["INSUFFICIENT_FUNDS", "UNKNOWN_CODE_999", None]:
        context = _make_context(error_code=error_code)
        result = await provider.diagnose(context)
        assert result.raw_response is None, (
            f"raw_response should be None for error_code={error_code!r}"
        )


# ---------------------------------------------------------------------------
# None error_code → UNKNOWN with low confidence
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_none_error_code_returns_unknown() -> None:
    """None error_code must return FailureCategory.UNKNOWN."""
    provider = StubAIProvider()
    context = _make_context(error_code=None)
    result = await provider.diagnose(context)
    assert result.failure_category == FailureCategory.UNKNOWN


@pytest.mark.unit
@pytest.mark.asyncio
async def test_none_error_code_has_low_confidence() -> None:
    """None error_code must return confidence_score of 0.40."""
    provider = StubAIProvider()
    context = _make_context(error_code=None)
    result = await provider.diagnose(context)
    assert result.confidence_score == pytest.approx(0.40)


# ---------------------------------------------------------------------------
# Known error codes → correct FailureCategory with high confidence
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code, expected_category",
    [
        # Insufficient funds
        ("INSUFFICIENT_FUNDS", FailureCategory.INSUFFICIENT_FUNDS),
        ("INSUFFICIENT_BALANCE", FailureCategory.INSUFFICIENT_FUNDS),
        ("LOW_BALANCE", FailureCategory.INSUFFICIENT_FUNDS),
        # Card declined
        ("CARD_DECLINED", FailureCategory.CARD_DECLINED),
        ("BAD_REQUEST_ERROR", FailureCategory.CARD_DECLINED),
        ("CARD_BLOCKED", FailureCategory.CARD_DECLINED),
        # Expired card
        ("EXPIRED_CARD", FailureCategory.EXPIRED_CARD),
        ("CARD_EXPIRED", FailureCategory.EXPIRED_CARD),
        # Authentication failure
        ("PAYMENT_CANCELLED", FailureCategory.AUTHENTICATION_FAILURE),
        ("AUTHENTICATION_FAILED", FailureCategory.AUTHENTICATION_FAILURE),
        ("THREE_DS_FAILED", FailureCategory.AUTHENTICATION_FAILURE),
        # Technical failure
        ("SERVER_ERROR", FailureCategory.TECHNICAL_FAILURE),
        ("GATEWAY_ERROR", FailureCategory.TECHNICAL_FAILURE),
        ("PAYMENT_FAILED", FailureCategory.TECHNICAL_FAILURE),
        # Network timeout
        ("CONNECTION_ERROR", FailureCategory.NETWORK_TIMEOUT),
        ("TIMEOUT", FailureCategory.NETWORK_TIMEOUT),
        ("NETWORK_ERROR", FailureCategory.NETWORK_TIMEOUT),
        # Fraud suspected
        ("FRAUD_SUSPECTED", FailureCategory.FRAUD_SUSPECTED),
        ("RISK_BLOCKED", FailureCategory.FRAUD_SUSPECTED),
        ("SUSPECTED_FRAUD", FailureCategory.FRAUD_SUSPECTED),
        # Limit exceeded
        ("LIMIT_EXCEEDED", FailureCategory.LIMIT_EXCEEDED),
        ("DAILY_LIMIT_EXCEEDED", FailureCategory.LIMIT_EXCEEDED),
        ("TRANSACTION_LIMIT_EXCEEDED", FailureCategory.LIMIT_EXCEEDED),
    ],
)
async def test_known_error_code_maps_to_correct_category(
    error_code: str,
    expected_category: FailureCategory,
) -> None:
    """Each known error code must map to the correct FailureCategory."""
    provider = StubAIProvider()
    context = _make_context(error_code=error_code)
    result = await provider.diagnose(context)
    assert result.failure_category == expected_category, (
        f"error_code={error_code!r} expected {expected_category}, got {result.failure_category}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_known_error_code_has_high_confidence() -> None:
    """A known error code must return confidence_score of 0.95."""
    provider = StubAIProvider()
    context = _make_context(error_code="INSUFFICIENT_FUNDS")
    result = await provider.diagnose(context)
    assert result.confidence_score == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# Unknown error codes → UNKNOWN with low confidence
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_error_code_returns_unknown_category() -> None:
    """An unrecognized error_code must return FailureCategory.UNKNOWN."""
    provider = StubAIProvider()
    context = _make_context(error_code="TOTALLY_UNRECOGNIZED_XYZ_999")
    result = await provider.diagnose(context)
    assert result.failure_category == FailureCategory.UNKNOWN


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_error_code_has_low_confidence() -> None:
    """An unrecognized error_code must return confidence_score of 0.40."""
    provider = StubAIProvider()
    context = _make_context(error_code="TOTALLY_UNRECOGNIZED_XYZ_999")
    result = await provider.diagnose(context)
    assert result.confidence_score == pytest.approx(0.40)


# ---------------------------------------------------------------------------
# Pre-classified failure_category shortcut
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pre_classified_category_used_directly() -> None:
    """When failure_category is already set, it must be used as-is."""
    provider = StubAIProvider()
    context = _make_context(
        error_code="INSUFFICIENT_FUNDS",  # would map to INSUFFICIENT_FUNDS normally
        failure_category=FailureCategory.FRAUD_SUSPECTED,  # override
    )
    result = await provider.diagnose(context)
    # Pre-classification takes priority over error_code lookup
    assert result.failure_category == FailureCategory.FRAUD_SUSPECTED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pre_classified_category_has_medium_confidence() -> None:
    """Pre-classified failure_category must return confidence_score of 0.85."""
    provider = StubAIProvider()
    context = _make_context(failure_category=FailureCategory.CARD_DECLINED)
    result = await provider.diagnose(context)
    assert result.confidence_score == pytest.approx(0.85)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pre_classified_with_none_error_code() -> None:
    """Pre-classification must work even when error_code is None."""
    provider = StubAIProvider()
    context = _make_context(error_code=None, failure_category=FailureCategory.EXPIRED_CARD)
    result = await provider.diagnose(context)
    assert result.failure_category == FailureCategory.EXPIRED_CARD
    assert result.confidence_score == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_code_is_case_insensitive() -> None:
    """Error code lookup must be case-insensitive (lowercased input maps correctly)."""
    provider = StubAIProvider()
    for variant in ["insufficient_funds", "Insufficient_Funds", "INSUFFICIENT_FUNDS"]:
        context = _make_context(error_code=variant)
        result = await provider.diagnose(context)
        assert result.failure_category == FailureCategory.INSUFFICIENT_FUNDS, (
            f"Variant {variant!r} was not classified as INSUFFICIENT_FUNDS"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_code_whitespace_is_stripped() -> None:
    """Leading/trailing whitespace in error_code must be stripped before lookup."""
    provider = StubAIProvider()
    context = _make_context(error_code="  INSUFFICIENT_FUNDS  ")
    result = await provider.diagnose(context)
    assert result.failure_category == FailureCategory.INSUFFICIENT_FUNDS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_code_hyphens_normalized_to_underscores() -> None:
    """Hyphens in error_code must be normalized to underscores."""
    provider = StubAIProvider()
    context = _make_context(error_code="INSUFFICIENT-FUNDS")
    result = await provider.diagnose(context)
    assert result.failure_category == FailureCategory.INSUFFICIENT_FUNDS


# ---------------------------------------------------------------------------
# Output schema compliance
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_output_is_diagnosis_output_instance() -> None:
    """diagnose() must return a DiagnosisOutput instance."""
    provider = StubAIProvider()
    context = _make_context(error_code="CARD_DECLINED")
    result = await provider.diagnose(context)
    assert isinstance(result, DiagnosisOutput)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_confidence_score_always_in_valid_range() -> None:
    """confidence_score must always be in [0.0, 1.0]."""
    provider = StubAIProvider()
    for error_code in [
        "INSUFFICIENT_FUNDS",
        "TOTALLY_UNKNOWN",
        None,
    ]:
        context = _make_context(error_code=error_code)
        result = await provider.diagnose(context)
        assert 0.0 <= result.confidence_score <= 1.0, (
            f"confidence_score {result.confidence_score} out of range for error_code={error_code!r}"
        )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_input_always_produces_same_output() -> None:
    """StubAIProvider must be fully deterministic: same input → same output."""
    provider = StubAIProvider()
    context = _make_context(error_code="GATEWAY_ERROR")

    first = await provider.diagnose(context)
    second = await provider.diagnose(context)

    assert first.failure_category == second.failure_category
    assert first.confidence_score == second.confidence_score
    assert first.ai_source == second.ai_source
    assert first.reasoning == second.reasoning
    assert first.raw_response == second.raw_response
