"""Deterministic Fallback Classifier.

Called when:
  1. The AI provider times out (AI_RESPONSE_TIMEOUT_SECONDS exceeded)
  2. The AI provider returns an invalid/unknown FailureCategory
  3. AI output fails Pydantic schema validation
  4. AI provider raises an unexpected exception

The fallback classifier is pure in-memory, has zero external dependencies,
and must complete within 2 seconds (typically microseconds).

Inputs used: error_code, error_description (optional for disambiguation)
Inputs NOT used: LLM output, model scores, historical data

Requirements: FR-DIAG-004, FR-DIAG-005, FR-DIAG-006
"""

from __future__ import annotations

from shared.contracts.enums import FailureCategory
from shared.contracts.schemas import DiagnosisOutput


# ---------------------------------------------------------------------------
# Classification rules (in priority order)
# ---------------------------------------------------------------------------
#
# Each entry is (FailureCategory, frozenset_of_keywords).
# Keywords are matched against the uppercased error_code.  First match wins.
# If no rule matches, UNKNOWN is returned.
#
# Priority order follows the task spec exactly:
#   1. INSUFFICIENT_FUNDS
#   2. EXPIRED_CARD
#   3. CARD_DECLINED
#   4. AUTHENTICATION_FAILURE
#   5. FRAUD_SUSPECTED
#   6. LIMIT_EXCEEDED
#   7. NETWORK_TIMEOUT
#   8. TECHNICAL_FAILURE
#   9. UNKNOWN (fallthrough)
#
_CLASSIFICATION_RULES: tuple[tuple[FailureCategory, frozenset[str]], ...] = (
    (
        FailureCategory.INSUFFICIENT_FUNDS,
        frozenset({"INSUFFICIENT", "FUNDS", "BALANCE", "LOW_BALANCE"}),
    ),
    (
        FailureCategory.EXPIRED_CARD,
        frozenset({"EXPIRED", "EXPIRY"}),
    ),
    (
        FailureCategory.CARD_DECLINED,
        frozenset({"DECLINED", "CARD_BLOCKED", "BLOCKED", "BAD_REQUEST", "INVALID_CARD"}),
    ),
    (
        FailureCategory.AUTHENTICATION_FAILURE,
        frozenset({"AUTH", "3DS", "OTP", "CANCELLED", "CANCEL", "ABANDONED"}),
    ),
    (
        FailureCategory.FRAUD_SUSPECTED,
        frozenset({"FRAUD", "RISK", "SUSPECTED", "BLOCKED_RISK"}),
    ),
    (
        FailureCategory.LIMIT_EXCEEDED,
        frozenset({"LIMIT", "EXCEEDED", "QUOTA"}),
    ),
    (
        FailureCategory.NETWORK_TIMEOUT,
        frozenset({"TIMEOUT", "NETWORK", "CONNECTION", "UNREACHABLE"}),
    ),
    (
        FailureCategory.TECHNICAL_FAILURE,
        frozenset({"SERVER_ERROR", "GATEWAY", "INTERNAL", "TECHNICAL", "PROCESSING"}),
    ),
)

# Confidence scores (FR-DIAG-006)
_KNOWN_CONFIDENCE: float = 0.75   # Known pattern match — lower than AI but still confident
_UNKNOWN_CONFIDENCE: float = 0.30  # No recognizable pattern


def _match_error_code(error_code: str) -> FailureCategory:
    """Match ``error_code`` against the classification rules.

    The ``error_code`` is uppercased before matching.  Rules are evaluated
    in priority order; the first match wins.  If no keyword from any rule
    appears as a substring of the uppercased code, ``UNKNOWN`` is returned.

    Args:
        error_code: Raw error code string from the payment gateway.

    Returns:
        The matched ``FailureCategory`` or ``UNKNOWN``.
    """
    normalized = error_code.upper()
    for category, keywords in _CLASSIFICATION_RULES:
        for keyword in keywords:
            if keyword in normalized:
                return category
    return FailureCategory.UNKNOWN


class DeterministicFallbackClassifier:
    """Pure in-memory classifier using Razorpay error codes.

    This classifier has zero I/O and zero external dependencies.  It must
    complete within 2 seconds (typically microseconds) to satisfy
    FR-DIAG-006.

    Classification is based solely on ``payment.error_code`` and
    ``payment.failure_code`` — never on LLM output, model scores, or
    historical data.

    All 9 ``FailureCategory`` values are reachable:
      - INSUFFICIENT_FUNDS  — keywords: INSUFFICIENT, FUNDS, BALANCE, LOW_BALANCE
      - EXPIRED_CARD        — keywords: EXPIRED, EXPIRY
      - CARD_DECLINED       — keywords: DECLINED, CARD_BLOCKED, BLOCKED, BAD_REQUEST, INVALID_CARD
      - AUTHENTICATION_FAILURE — keywords: AUTH, 3DS, OTP, CANCELLED, CANCEL, ABANDONED
      - FRAUD_SUSPECTED     — keywords: FRAUD, RISK, SUSPECTED, BLOCKED_RISK
      - LIMIT_EXCEEDED      — keywords: LIMIT, EXCEEDED, QUOTA
      - NETWORK_TIMEOUT     — keywords: TIMEOUT, NETWORK, CONNECTION, UNREACHABLE
      - TECHNICAL_FAILURE   — keywords: SERVER_ERROR, GATEWAY, INTERNAL, TECHNICAL, PROCESSING
      - UNKNOWN             — any unrecognized code (or None)

    Requirements: FR-DIAG-004, FR-DIAG-005, FR-DIAG-006.
    """

    def classify(
        self,
        error_code: str | None,
        error_description: str | None = None,
        fallback_reason: str = "ai_provider_fallback",
    ) -> DiagnosisOutput:
        """Classify failure category from ``error_code``.

        Args:
            error_code:
                Raw error code from the payment gateway (e.g.
                ``"BAD_REQUEST_ERROR"``).  ``None`` returns ``UNKNOWN``
                with confidence 0.30.
            error_description:
                Optional human-readable description.  Currently reserved for
                future disambiguation logic; not used in classification today.
            fallback_reason:
                Why the fallback was invoked.  Should be one of:
                  - ``"ai_timeout"``                 — FR-DIAG-005
                  - ``"schema_validation_failure"``  — FR-DIAG-004
                  - ``"ai_provider_fallback"``        — generic / unknown AI error
                  - ``"invalid_failure_category"``   — LLM returned unknown enum value

        Returns:
            A validated ``DiagnosisOutput`` with:
              - ``ai_source = "DeterministicFallback"``
              - ``confidence_score = 0.75`` for a known pattern match
              - ``confidence_score = 0.30`` for ``UNKNOWN``
              - ``reasoning`` describing the classification and fallback reason
              - ``raw_response = None``
        """
        # None error_code → UNKNOWN immediately (no keyword to match)
        if error_code is None:
            category = FailureCategory.UNKNOWN
        else:
            category = _match_error_code(error_code)

        confidence = (
            _UNKNOWN_CONFIDENCE if category is FailureCategory.UNKNOWN else _KNOWN_CONFIDENCE
        )

        reasoning = (
            f"Fallback classification from error_code={error_code!r}. "
            f"Reason: {fallback_reason}"
        )

        return DiagnosisOutput(
            failure_category=category,
            confidence_score=confidence,
            ai_source="DeterministicFallback",
            reasoning=reasoning,
            raw_response=None,
        )


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------


def classify_from_error_code(
    error_code: str | None,
    fallback_reason: str = "ai_provider_fallback",
) -> DiagnosisOutput:
    """Module-level convenience wrapper around ``DeterministicFallbackClassifier.classify``.

    Instantiates a classifier and delegates immediately.  Suitable for
    one-off calls where caller does not need to retain a classifier instance.

    Args:
        error_code:
            Raw error code from the payment gateway.  ``None`` returns
            ``UNKNOWN`` with confidence 0.30.
        fallback_reason:
            Why the fallback was invoked.  Default: ``"ai_provider_fallback"``.

    Returns:
        A validated ``DiagnosisOutput`` from the deterministic fallback
        classifier.  See ``DeterministicFallbackClassifier.classify`` for
        full documentation.
    """
    return DeterministicFallbackClassifier().classify(
        error_code=error_code,
        fallback_reason=fallback_reason,
    )
