"""Unit tests for DeterministicFallbackClassifier (task 9.3, task 21.4).

Tests verify that the classifier:
  1. Returns the correct FailureCategory for each of the 9 possible values.
  2. Returns UNKNOWN for None error_code.
  3. Returns UNKNOWN for unrecognized error codes.
  4. Always sets ai_source = "DeterministicFallback".
  5. Returns confidence = 0.75 for known pattern matches.
  6. Returns confidence = 0.30 for UNKNOWN.
  7. Completes within 2 seconds (pure in-memory, no I/O).
  8. Includes fallback_reason in the reasoning string.

All tests are pure unit tests — no database, no Redis, no network calls.
Requirements: FR-DIAG-004, FR-DIAG-005, FR-DIAG-006.
"""

from __future__ import annotations

import time

import pytest

from backend.decision_engine.fallback_classifier import (
    DeterministicFallbackClassifier,
    classify_from_error_code,
)
from shared.contracts.enums import FailureCategory
from shared.contracts.schemas import DiagnosisOutput

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

CLASSIFIER = DeterministicFallbackClassifier()

_AI_SOURCE = "DeterministicFallback"
_KNOWN_CONFIDENCE = 0.75
_UNKNOWN_CONFIDENCE = 0.30


# ---------------------------------------------------------------------------
# All 9 FailureCategory values are reachable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_insufficient_funds_via_insufficient_keyword() -> None:
    """INSUFFICIENT_FUNDS is returned for an error_code containing 'INSUFFICIENT'."""
    result = CLASSIFIER.classify(error_code="INSUFFICIENT_BALANCE_ERROR")
    assert result.failure_category == FailureCategory.INSUFFICIENT_FUNDS


@pytest.mark.unit
def test_insufficient_funds_via_funds_keyword() -> None:
    """INSUFFICIENT_FUNDS is returned for an error_code containing 'FUNDS'."""
    result = CLASSIFIER.classify(error_code="NO_FUNDS_AVAILABLE")
    assert result.failure_category == FailureCategory.INSUFFICIENT_FUNDS


@pytest.mark.unit
def test_insufficient_funds_via_balance_keyword() -> None:
    """INSUFFICIENT_FUNDS is returned for an error_code containing 'BALANCE'."""
    result = CLASSIFIER.classify(error_code="LOW_BALANCE_ERROR")
    assert result.failure_category == FailureCategory.INSUFFICIENT_FUNDS


@pytest.mark.unit
def test_insufficient_funds_via_low_balance_keyword() -> None:
    """INSUFFICIENT_FUNDS is returned for an error_code containing 'LOW_BALANCE'."""
    result = CLASSIFIER.classify(error_code="LOW_BALANCE")
    assert result.failure_category == FailureCategory.INSUFFICIENT_FUNDS


@pytest.mark.unit
def test_expired_card_via_expired_keyword() -> None:
    """EXPIRED_CARD is returned for an error_code containing 'EXPIRED'."""
    result = CLASSIFIER.classify(error_code="CARD_EXPIRED_ERROR")
    assert result.failure_category == FailureCategory.EXPIRED_CARD


@pytest.mark.unit
def test_expired_card_via_expiry_keyword() -> None:
    """EXPIRED_CARD is returned for an error_code containing 'EXPIRY'."""
    result = CLASSIFIER.classify(error_code="EXPIRY_DATE_INVALID")
    assert result.failure_category == FailureCategory.EXPIRED_CARD


@pytest.mark.unit
def test_card_declined_via_declined_keyword() -> None:
    """CARD_DECLINED is returned for an error_code containing 'DECLINED'."""
    result = CLASSIFIER.classify(error_code="PAYMENT_DECLINED")
    assert result.failure_category == FailureCategory.CARD_DECLINED


@pytest.mark.unit
def test_card_declined_via_blocked_keyword() -> None:
    """CARD_DECLINED is returned for an error_code containing 'BLOCKED'."""
    result = CLASSIFIER.classify(error_code="CARD_BLOCKED_BY_BANK")
    assert result.failure_category == FailureCategory.CARD_DECLINED


@pytest.mark.unit
def test_card_declined_via_bad_request_keyword() -> None:
    """CARD_DECLINED is returned for an error_code containing 'BAD_REQUEST'."""
    result = CLASSIFIER.classify(error_code="BAD_REQUEST_ERROR")
    assert result.failure_category == FailureCategory.CARD_DECLINED


@pytest.mark.unit
def test_card_declined_via_invalid_card_keyword() -> None:
    """CARD_DECLINED is returned for an error_code containing 'INVALID_CARD'."""
    result = CLASSIFIER.classify(error_code="INVALID_CARD_NUMBER")
    assert result.failure_category == FailureCategory.CARD_DECLINED


@pytest.mark.unit
def test_authentication_failure_via_auth_keyword() -> None:
    """AUTHENTICATION_FAILURE is returned for an error_code containing 'AUTH'."""
    result = CLASSIFIER.classify(error_code="AUTH_FAILED")
    assert result.failure_category == FailureCategory.AUTHENTICATION_FAILURE


@pytest.mark.unit
def test_authentication_failure_via_3ds_keyword() -> None:
    """AUTHENTICATION_FAILURE is returned for an error_code containing '3DS'."""
    result = CLASSIFIER.classify(error_code="3DS_VERIFICATION_FAILED")
    assert result.failure_category == FailureCategory.AUTHENTICATION_FAILURE


@pytest.mark.unit
def test_authentication_failure_via_otp_keyword() -> None:
    """AUTHENTICATION_FAILURE is returned for an error_code containing 'OTP'."""
    result = CLASSIFIER.classify(error_code="OTP_INCORRECT")
    assert result.failure_category == FailureCategory.AUTHENTICATION_FAILURE


@pytest.mark.unit
def test_authentication_failure_via_cancelled_keyword() -> None:
    """AUTHENTICATION_FAILURE is returned for an error_code containing 'CANCELLED'."""
    result = CLASSIFIER.classify(error_code="PAYMENT_CANCELLED_BY_USER")
    assert result.failure_category == FailureCategory.AUTHENTICATION_FAILURE


@pytest.mark.unit
def test_authentication_failure_via_abandoned_keyword() -> None:
    """AUTHENTICATION_FAILURE is returned for an error_code containing 'ABANDONED'."""
    result = CLASSIFIER.classify(error_code="PAYMENT_ABANDONED")
    assert result.failure_category == FailureCategory.AUTHENTICATION_FAILURE


@pytest.mark.unit
def test_fraud_suspected_via_fraud_keyword() -> None:
    """FRAUD_SUSPECTED is returned for an error_code containing 'FRAUD'."""
    result = CLASSIFIER.classify(error_code="SUSPECTED_FRAUD_DETECTED")
    assert result.failure_category == FailureCategory.FRAUD_SUSPECTED


@pytest.mark.unit
def test_fraud_suspected_via_risk_keyword() -> None:
    """FRAUD_SUSPECTED is returned for an error_code containing 'RISK'."""
    result = CLASSIFIER.classify(error_code="HIGH_RISK_TRANSACTION")
    assert result.failure_category == FailureCategory.FRAUD_SUSPECTED


@pytest.mark.unit
def test_fraud_suspected_via_suspected_keyword() -> None:
    """FRAUD_SUSPECTED is returned for an error_code containing 'SUSPECTED'."""
    result = CLASSIFIER.classify(error_code="SUSPECTED_FRAUD")
    assert result.failure_category == FailureCategory.FRAUD_SUSPECTED


@pytest.mark.unit
def test_limit_exceeded_via_limit_keyword() -> None:
    """LIMIT_EXCEEDED is returned for an error_code containing 'LIMIT'."""
    result = CLASSIFIER.classify(error_code="DAILY_LIMIT_EXCEEDED")
    assert result.failure_category == FailureCategory.LIMIT_EXCEEDED


@pytest.mark.unit
def test_limit_exceeded_via_exceeded_keyword() -> None:
    """LIMIT_EXCEEDED is returned for an error_code containing 'EXCEEDED'."""
    result = CLASSIFIER.classify(error_code="TRANSACTION_AMOUNT_EXCEEDED")
    assert result.failure_category == FailureCategory.LIMIT_EXCEEDED


@pytest.mark.unit
def test_limit_exceeded_via_quota_keyword() -> None:
    """LIMIT_EXCEEDED is returned for an error_code containing 'QUOTA'."""
    result = CLASSIFIER.classify(error_code="QUOTA_EXCEEDED")
    assert result.failure_category == FailureCategory.LIMIT_EXCEEDED


@pytest.mark.unit
def test_network_timeout_via_timeout_keyword() -> None:
    """NETWORK_TIMEOUT is returned for an error_code containing 'TIMEOUT'."""
    result = CLASSIFIER.classify(error_code="GATEWAY_TIMEOUT")
    assert result.failure_category == FailureCategory.NETWORK_TIMEOUT


@pytest.mark.unit
def test_network_timeout_via_network_keyword() -> None:
    """NETWORK_TIMEOUT is returned for an error_code containing 'NETWORK'."""
    result = CLASSIFIER.classify(error_code="NETWORK_FAILURE")
    assert result.failure_category == FailureCategory.NETWORK_TIMEOUT


@pytest.mark.unit
def test_network_timeout_via_connection_keyword() -> None:
    """NETWORK_TIMEOUT is returned for an error_code containing 'CONNECTION'."""
    result = CLASSIFIER.classify(error_code="CONNECTION_REFUSED")
    assert result.failure_category == FailureCategory.NETWORK_TIMEOUT


@pytest.mark.unit
def test_network_timeout_via_unreachable_keyword() -> None:
    """NETWORK_TIMEOUT is returned for an error_code containing 'UNREACHABLE'."""
    result = CLASSIFIER.classify(error_code="HOST_UNREACHABLE")
    assert result.failure_category == FailureCategory.NETWORK_TIMEOUT


@pytest.mark.unit
def test_technical_failure_via_server_error_keyword() -> None:
    """TECHNICAL_FAILURE is returned for an error_code containing 'SERVER_ERROR'."""
    result = CLASSIFIER.classify(error_code="INTERNAL_SERVER_ERROR")
    assert result.failure_category == FailureCategory.TECHNICAL_FAILURE


@pytest.mark.unit
def test_technical_failure_via_gateway_keyword() -> None:
    """TECHNICAL_FAILURE is returned for an error_code containing 'GATEWAY'."""
    result = CLASSIFIER.classify(error_code="PAYMENT_GATEWAY_ERROR")
    assert result.failure_category == FailureCategory.TECHNICAL_FAILURE


@pytest.mark.unit
def test_technical_failure_via_internal_keyword() -> None:
    """TECHNICAL_FAILURE is returned for an error_code containing 'INTERNAL'."""
    result = CLASSIFIER.classify(error_code="INTERNAL_ERROR")
    assert result.failure_category == FailureCategory.TECHNICAL_FAILURE


@pytest.mark.unit
def test_technical_failure_via_technical_keyword() -> None:
    """TECHNICAL_FAILURE is returned for an error_code containing 'TECHNICAL'."""
    result = CLASSIFIER.classify(error_code="TECHNICAL_DECLINE")
    assert result.failure_category == FailureCategory.TECHNICAL_FAILURE


@pytest.mark.unit
def test_technical_failure_via_processing_keyword() -> None:
    """TECHNICAL_FAILURE is returned for an error_code containing 'PROCESSING'."""
    result = CLASSIFIER.classify(error_code="PAYMENT_PROCESSING_ERROR")
    assert result.failure_category == FailureCategory.TECHNICAL_FAILURE


@pytest.mark.unit
def test_unknown_for_unrecognized_error_code() -> None:
    """UNKNOWN is returned for an error_code that matches no keyword."""
    result = CLASSIFIER.classify(error_code="SOME_TOTALLY_UNKNOWN_CODE_XYZ")
    assert result.failure_category == FailureCategory.UNKNOWN


# ---------------------------------------------------------------------------
# None error_code → UNKNOWN
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_none_error_code_returns_unknown() -> None:
    """None error_code must return FailureCategory.UNKNOWN (task requirement)."""
    result = CLASSIFIER.classify(error_code=None)
    assert result.failure_category == FailureCategory.UNKNOWN


@pytest.mark.unit
def test_none_error_code_returns_unknown_confidence() -> None:
    """None error_code must return confidence_score = 0.30."""
    result = CLASSIFIER.classify(error_code=None)
    assert result.confidence_score == _UNKNOWN_CONFIDENCE


# ---------------------------------------------------------------------------
# ai_source is always "DeterministicFallback"
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ai_source_is_always_deterministic_fallback_for_known_code() -> None:
    """ai_source must be 'DeterministicFallback' for a known error code."""
    result = CLASSIFIER.classify(error_code="INSUFFICIENT_FUNDS")
    assert result.ai_source == _AI_SOURCE


@pytest.mark.unit
def test_ai_source_is_always_deterministic_fallback_for_none_code() -> None:
    """ai_source must be 'DeterministicFallback' when error_code is None."""
    result = CLASSIFIER.classify(error_code=None)
    assert result.ai_source == _AI_SOURCE


@pytest.mark.unit
def test_ai_source_is_always_deterministic_fallback_for_unknown_code() -> None:
    """ai_source must be 'DeterministicFallback' for an unrecognized error code."""
    result = CLASSIFIER.classify(error_code="TOTALLY_UNRECOGNIZED_CODE")
    assert result.ai_source == _AI_SOURCE


# ---------------------------------------------------------------------------
# Confidence values
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_confidence_is_0_75_for_known_pattern_match() -> None:
    """confidence_score must be 0.75 for a recognized error code pattern."""
    result = CLASSIFIER.classify(error_code="INSUFFICIENT_BALANCE")
    assert result.confidence_score == _KNOWN_CONFIDENCE


@pytest.mark.unit
def test_confidence_is_0_30_for_unknown() -> None:
    """confidence_score must be 0.30 when the result is UNKNOWN."""
    result = CLASSIFIER.classify(error_code="SOME_UNKNOWN_XYZ_CODE")
    assert result.confidence_score == _UNKNOWN_CONFIDENCE


@pytest.mark.unit
def test_confidence_score_is_float_not_int() -> None:
    """confidence_score must be a float (not int) for all results."""
    result_known = CLASSIFIER.classify(error_code="DECLINED")
    result_unknown = CLASSIFIER.classify(error_code=None)
    assert isinstance(result_known.confidence_score, float)
    assert isinstance(result_unknown.confidence_score, float)


# ---------------------------------------------------------------------------
# DiagnosisOutput schema validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_result_is_diagnosis_output_instance() -> None:
    """classify() must return a DiagnosisOutput Pydantic model instance."""
    result = CLASSIFIER.classify(error_code="DECLINED")
    assert isinstance(result, DiagnosisOutput)


@pytest.mark.unit
def test_raw_response_is_always_none() -> None:
    """raw_response must always be None for the deterministic fallback."""
    result = CLASSIFIER.classify(error_code="DECLINED")
    assert result.raw_response is None


@pytest.mark.unit
def test_reasoning_contains_error_code() -> None:
    """reasoning must reference the provided error_code."""
    error_code = "BAD_REQUEST_ERROR"
    result = CLASSIFIER.classify(error_code=error_code)
    assert error_code in (result.reasoning or "")


@pytest.mark.unit
def test_reasoning_contains_fallback_reason() -> None:
    """reasoning must include the fallback_reason string."""
    reason = "ai_timeout"
    result = CLASSIFIER.classify(error_code="DECLINED", fallback_reason=reason)
    assert reason in (result.reasoning or "")


@pytest.mark.unit
def test_default_fallback_reason_is_ai_provider_fallback() -> None:
    """Default fallback_reason ('ai_provider_fallback') appears in reasoning."""
    result = CLASSIFIER.classify(error_code="DECLINED")
    assert "ai_provider_fallback" in (result.reasoning or "")


@pytest.mark.unit
def test_schema_validation_failure_reason_in_reasoning() -> None:
    """fallback_reason 'schema_validation_failure' (FR-DIAG-004) appears in reasoning."""
    result = CLASSIFIER.classify(
        error_code="GATEWAY_ERROR",
        fallback_reason="schema_validation_failure",
    )
    assert "schema_validation_failure" in (result.reasoning or "")


# ---------------------------------------------------------------------------
# Case insensitivity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lowercase_error_code_is_normalised_correctly() -> None:
    """Error codes in lowercase are uppercased before matching; result is identical."""
    result_upper = CLASSIFIER.classify(error_code="INSUFFICIENT_BALANCE")
    result_lower = CLASSIFIER.classify(error_code="insufficient_balance")
    assert result_lower.failure_category == result_upper.failure_category
    assert result_lower.confidence_score == result_upper.confidence_score


@pytest.mark.unit
def test_mixed_case_error_code_matches_correctly() -> None:
    """Mixed-case error codes are normalised before matching."""
    result = CLASSIFIER.classify(error_code="Card_Declined_Error")
    assert result.failure_category == FailureCategory.CARD_DECLINED


# ---------------------------------------------------------------------------
# Priority ordering — first matching rule wins
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_priority_order_insufficient_before_card_declined() -> None:
    """When error_code matches both INSUFFICIENT_FUNDS and CARD_DECLINED keywords,
    INSUFFICIENT_FUNDS wins (it has higher priority)."""
    # 'INSUFFICIENT' (rule 1) and 'BLOCKED' (rule 3) both present.
    result = CLASSIFIER.classify(error_code="INSUFFICIENT_FUNDS_BLOCKED")
    assert result.failure_category == FailureCategory.INSUFFICIENT_FUNDS


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_classify_from_error_code_returns_diagnosis_output() -> None:
    """Module-level classify_from_error_code() returns a DiagnosisOutput."""
    result = classify_from_error_code(error_code="DECLINED")
    assert isinstance(result, DiagnosisOutput)
    assert result.ai_source == _AI_SOURCE


@pytest.mark.unit
def test_classify_from_error_code_none_returns_unknown() -> None:
    """Module-level classify_from_error_code(None) returns UNKNOWN."""
    result = classify_from_error_code(error_code=None)
    assert result.failure_category == FailureCategory.UNKNOWN


@pytest.mark.unit
def test_classify_from_error_code_passes_fallback_reason() -> None:
    """Module-level function passes fallback_reason through to reasoning."""
    result = classify_from_error_code(
        error_code="DECLINED",
        fallback_reason="schema_validation_failure",
    )
    assert "schema_validation_failure" in (result.reasoning or "")


# ---------------------------------------------------------------------------
# Performance: completes within 2 seconds (FR-DIAG-006)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_classifier_completes_within_2_seconds_known_code() -> None:
    """FR-DIAG-006: classify() must complete within 2 seconds for known error codes."""
    start = time.monotonic()
    CLASSIFIER.classify(error_code="INSUFFICIENT_FUNDS_ERROR")
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"classify() took {elapsed:.3f}s — must complete within 2s"


@pytest.mark.unit
def test_classifier_completes_within_2_seconds_none_code() -> None:
    """FR-DIAG-006: classify() must complete within 2 seconds for None error code."""
    start = time.monotonic()
    CLASSIFIER.classify(error_code=None)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"classify() took {elapsed:.3f}s — must complete within 2s"


@pytest.mark.unit
def test_classifier_completes_within_2_seconds_unknown_code() -> None:
    """FR-DIAG-006: classify() must complete within 2 seconds for unknown error codes."""
    start = time.monotonic()
    CLASSIFIER.classify(error_code="TOTALLY_UNKNOWN_ERROR_CODE")
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"classify() took {elapsed:.3f}s — must complete within 2s"


# ---------------------------------------------------------------------------
# All 9 FailureCategory values are reachable from the classifier
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_nine_failure_categories_are_reachable() -> None:
    """All 9 FailureCategory values can be produced by the classifier.

    Uses one representative error code per category.
    """
    expected_mappings = {
        FailureCategory.INSUFFICIENT_FUNDS: "INSUFFICIENT_FUNDS",
        FailureCategory.EXPIRED_CARD: "CARD_EXPIRED",
        FailureCategory.CARD_DECLINED: "PAYMENT_DECLINED",
        FailureCategory.AUTHENTICATION_FAILURE: "AUTH_FAILED",
        FailureCategory.FRAUD_SUSPECTED: "FRAUD_DETECTED",
        FailureCategory.LIMIT_EXCEEDED: "DAILY_LIMIT_EXCEEDED",
        FailureCategory.NETWORK_TIMEOUT: "NETWORK_ERROR",
        FailureCategory.TECHNICAL_FAILURE: "SERVER_ERROR",
        FailureCategory.UNKNOWN: None,  # None error_code → UNKNOWN
    }

    results: set[FailureCategory] = set()
    for expected_category, error_code in expected_mappings.items():
        result = CLASSIFIER.classify(error_code=error_code)
        assert result.failure_category == expected_category, (
            f"Expected {expected_category} for error_code={error_code!r}, "
            f"got {result.failure_category}"
        )
        results.add(result.failure_category)

    assert results == set(FailureCategory), (
        f"Not all FailureCategory values are reachable. Missing: "
        f"{set(FailureCategory) - results}"
    )
