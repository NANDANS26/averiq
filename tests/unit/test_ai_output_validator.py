"""Unit tests for AI output validator (task 9.5).

Validates the FailureCategory allowlist enforcement in
``backend.decision_engine.ai_output_validator``.

Tests:
  1. Each of the 9 valid FailureCategory values returns the correct enum member.
  2. Unknown value "PAYMENT_PENDING" → None returned.
  3. None input → None (no error raised).
  4. All 9 FailureCategory values are reachable via the allowlist.
  5. Invalid value triggers structlog warning with truncated safe_category_repr.
  6. The allowlist frozenset (_VALID_CATEGORIES) contains exactly 9 entries.

Requirements: SEC-AI-001, SEC-AI-002
"""

from __future__ import annotations

import pytest

from backend.decision_engine.ai_output_validator import (
    _VALID_CATEGORIES,
    validate_failure_category,
)
from shared.contracts.enums import FailureCategory

# ---------------------------------------------------------------------------
# Test 1: Each of the 9 valid FailureCategory values returns the correct enum
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "category",
    [
        FailureCategory.INSUFFICIENT_FUNDS,
        FailureCategory.CARD_DECLINED,
        FailureCategory.EXPIRED_CARD,
        FailureCategory.AUTHENTICATION_FAILURE,
        FailureCategory.TECHNICAL_FAILURE,
        FailureCategory.NETWORK_TIMEOUT,
        FailureCategory.FRAUD_SUSPECTED,
        FailureCategory.LIMIT_EXCEEDED,
        FailureCategory.UNKNOWN,
    ],
)
def test_valid_failure_category_returns_enum(category: FailureCategory) -> None:
    """Each of the 9 valid FailureCategory values must return the correct enum member.

    Validates SEC-AI-001 and SEC-AI-002: AI output enum fields validated against
    allowlist before use; valid values pass through as the canonical enum member.
    """
    result = validate_failure_category(category.value)
    assert result is not None, (
        f"Expected FailureCategory member for {category.value!r}, got None"
    )
    assert result == category, (
        f"Expected {category!r}, got {result!r}"
    )
    assert isinstance(result, FailureCategory), (
        f"Return type must be FailureCategory, got {type(result)}"
    )


# ---------------------------------------------------------------------------
# Test 2: Unknown value "PAYMENT_PENDING" → None returned
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unknown_value_returns_none() -> None:
    """Unknown AI output value 'PAYMENT_PENDING' must be rejected and return None.

    Validates SEC-AI-002: unknown enum fields trigger deterministic fallback
    (signalled by returning None from the validator).
    """
    result = validate_failure_category("PAYMENT_PENDING")
    assert result is None, (
        f"Expected None for unknown value 'PAYMENT_PENDING', got {result!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: None input → None, no error
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_none_input_returns_none_without_error() -> None:
    """None input must return None without raising any exception.

    When the LLM omits the failure_category field entirely, the validator
    returns None so the caller can fall back to the deterministic classifier.
    This is an expected and valid code path — no error should occur.
    """
    result = validate_failure_category(None)
    assert result is None, (
        f"Expected None for None input, got {result!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: All 9 FailureCategory values are reachable via the allowlist
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_failure_categories_reachable_via_allowlist() -> None:
    """All 9 FailureCategory enum members must be present in _VALID_CATEGORIES.

    Ensures that every defined FailureCategory value is reachable through the
    allowlist: no enum member is inadvertently excluded from validation.
    """
    all_category_values = {c.value for c in FailureCategory}
    for value in all_category_values:
        assert value in _VALID_CATEGORIES, (
            f"FailureCategory.{value} is missing from _VALID_CATEGORIES allowlist"
        )
    # Symmetric check: no extra values in the allowlist beyond the enum
    assert _VALID_CATEGORIES == all_category_values, (
        f"_VALID_CATEGORIES contains values not in FailureCategory enum: "
        f"{_VALID_CATEGORIES - all_category_values}"
    )


# ---------------------------------------------------------------------------
# Test 5: Invalid value triggers structlog warning
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invalid_value_triggers_structlog_warning(capfd: pytest.CaptureFixture[str]) -> None:
    """Rejecting an invalid category value must emit a structured warning log.

    The warning must:
      - be emitted via structlog (captured in stdout/stderr by capfd)
      - contain 'safe_category_repr' with the truncated value
      - mention 'SEC-AI-001' in the note field

    NFR-OBS-007: the raw value is truncated/repr'd to prevent PII leakage.
    """
    invalid_value = "NOT_A_REAL_CATEGORY"
    result = validate_failure_category(invalid_value)

    assert result is None, "Invalid value must return None"

    # Capture printed log output (structlog.PrintLoggerFactory writes to stdout)
    captured = capfd.readouterr()
    combined_output = captured.out + captured.err

    # The log entry must contain the event name and the SEC-AI-001 reference
    assert "ai_output_validator.invalid_failure_category" in combined_output, (
        f"Expected warning log event name in output; got: {combined_output!r}"
    )
    assert "SEC-AI-001" in combined_output, (
        f"Expected SEC-AI-001 reference in warning log; got: {combined_output!r}"
    )


@pytest.mark.unit
def test_long_invalid_value_is_truncated_in_log(capfd: pytest.CaptureFixture[str]) -> None:
    """Values longer than 20 chars must be truncated in the log (NFR-OBS-007).

    A 30-character invalid value should appear in the log as a repr of its
    first 20 characters — not the full string.
    """
    long_invalid = "A" * 30  # 30-character string, well beyond the 20-char limit
    result = validate_failure_category(long_invalid)

    assert result is None

    captured = capfd.readouterr()
    combined_output = captured.out + captured.err

    # The full 30-char string must NOT appear verbatim
    assert long_invalid not in combined_output, (
        "Full long invalid value must be truncated in log output"
    )
    # The truncated repr (first 20 chars) should appear
    assert repr(long_invalid[:20]) in combined_output, (
        f"Truncated repr of first 20 chars must appear in log output; got: {combined_output!r}"
    )


# ---------------------------------------------------------------------------
# Test 6: The allowlist frozenset contains exactly 9 entries
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_allowlist_contains_exactly_nine_entries() -> None:
    """The _VALID_CATEGORIES frozenset must contain exactly 9 entries.

    This verifies that the FailureCategory enum defines exactly the 9
    categories required by FR-DIAG-002, and that the allowlist is derived
    from the complete enum with no manual additions or omissions.
    """
    assert len(_VALID_CATEGORIES) == 9, (
        f"Expected exactly 9 entries in _VALID_CATEGORIES, got {len(_VALID_CATEGORIES)}: "
        f"{sorted(_VALID_CATEGORIES)}"
    )
    # Also verify the specific expected values are all present
    expected_values = {
        "INSUFFICIENT_FUNDS",
        "CARD_DECLINED",
        "EXPIRED_CARD",
        "AUTHENTICATION_FAILURE",
        "TECHNICAL_FAILURE",
        "NETWORK_TIMEOUT",
        "FRAUD_SUSPECTED",
        "LIMIT_EXCEEDED",
        "UNKNOWN",
    }
    assert _VALID_CATEGORIES == expected_values, (
        f"Allowlist mismatch.\n"
        f"  Expected: {sorted(expected_values)}\n"
        f"  Actual:   {sorted(_VALID_CATEGORIES)}"
    )
