"""StubAIProvider — zero-configuration deterministic AI provider.

Used in:
  - Local development (no LLM API key required)
  - CI/CD pipelines
  - Unit and integration tests
  - Bulk synthetic data evaluation runs

The stub classifies ``failure_category`` deterministically from ``error_code``
using a lookup table. It never makes external calls, never requires
configuration, and always returns a valid ``DiagnosisOutput`` within
microseconds.

Requirements: FR-DIAG-007, AI-PROV-001
"""

from __future__ import annotations

from shared.contracts.enums import FailureCategory
from shared.contracts.schemas import CaseContext, DiagnosisOutput

# ---------------------------------------------------------------------------
# Error code lookup table
# ---------------------------------------------------------------------------

# Mapping from Razorpay error_code prefixes/values → FailureCategory.
# These mappings cover the most common Razorpay error codes.
_ERROR_CODE_MAP: dict[str, FailureCategory] = {
    # Insufficient funds
    "INSUFFICIENT_FUNDS": FailureCategory.INSUFFICIENT_FUNDS,
    "INSUFFICIENT_BALANCE": FailureCategory.INSUFFICIENT_FUNDS,
    "LOW_BALANCE": FailureCategory.INSUFFICIENT_FUNDS,
    # Card declined
    "CARD_DECLINED": FailureCategory.CARD_DECLINED,
    "BAD_REQUEST_ERROR": FailureCategory.CARD_DECLINED,
    "CARD_BLOCKED": FailureCategory.CARD_DECLINED,
    # Expired card
    "EXPIRED_CARD": FailureCategory.EXPIRED_CARD,
    "CARD_EXPIRED": FailureCategory.EXPIRED_CARD,
    # Authentication failure
    "PAYMENT_CANCELLED": FailureCategory.AUTHENTICATION_FAILURE,
    "AUTHENTICATION_FAILED": FailureCategory.AUTHENTICATION_FAILURE,
    "THREE_DS_FAILED": FailureCategory.AUTHENTICATION_FAILURE,
    # Technical failure
    "SERVER_ERROR": FailureCategory.TECHNICAL_FAILURE,
    "GATEWAY_ERROR": FailureCategory.TECHNICAL_FAILURE,
    "PAYMENT_FAILED": FailureCategory.TECHNICAL_FAILURE,
    # Network timeout
    "CONNECTION_ERROR": FailureCategory.NETWORK_TIMEOUT,
    "TIMEOUT": FailureCategory.NETWORK_TIMEOUT,
    "NETWORK_ERROR": FailureCategory.NETWORK_TIMEOUT,
    # Fraud suspected
    "FRAUD_SUSPECTED": FailureCategory.FRAUD_SUSPECTED,
    "RISK_BLOCKED": FailureCategory.FRAUD_SUSPECTED,
    "SUSPECTED_FRAUD": FailureCategory.FRAUD_SUSPECTED,
    # Limit exceeded
    "LIMIT_EXCEEDED": FailureCategory.LIMIT_EXCEEDED,
    "DAILY_LIMIT_EXCEEDED": FailureCategory.LIMIT_EXCEEDED,
    "TRANSACTION_LIMIT_EXCEEDED": FailureCategory.LIMIT_EXCEEDED,
}

# Confidence levels
_CONFIDENCE_KNOWN: float = 0.95
_CONFIDENCE_PRE_CLASSIFIED: float = 0.85
_CONFIDENCE_UNKNOWN: float = 0.40


def _normalize_error_code(raw: str) -> str:
    """Normalize an error code to UPPER_SNAKE_CASE for lookup.

    Steps:
      1. Strip leading/trailing whitespace.
      2. Convert to uppercase.
      3. Replace spaces and hyphens with underscores.
    """
    return raw.strip().upper().replace(" ", "_").replace("-", "_")


class StubAIProvider:
    """Deterministic AI provider stub for development and testing.

    Satisfies the ``AIProvider`` protocol via structural subtyping
    (``typing.Protocol``) — no explicit ``implements`` or base class required.
    All outputs are deterministic given the same input — no randomness.

    Classification algorithm:
      1. If ``context.failure_category`` is already set, use that directly
         with confidence ``_CONFIDENCE_PRE_CLASSIFIED`` (0.85).
      2. If ``context.error_code`` is None, return ``UNKNOWN`` with
         confidence ``_CONFIDENCE_UNKNOWN`` (0.40).
      3. Normalize the error_code to UPPER_SNAKE_CASE.
      4. Perform an exact lookup in ``_ERROR_CODE_MAP``.
      5. If not found, scan for any map key that is a substring of the
         normalized error_code (partial/prefix match).
      6. Fall back to ``UNKNOWN`` with confidence ``_CONFIDENCE_UNKNOWN``.

    Always sets:
      - ``ai_source = "StubAIProvider"``
      - ``raw_response = None`` (no external call)
    """

    async def diagnose(self, context: CaseContext) -> DiagnosisOutput:
        """Classify failure_category deterministically from the CaseContext.

        Parameters
        ----------
        context:
            The payment failure context assembled by the Root_Cause_Reasoner.
            Only ``context.error_code`` and ``context.failure_category`` are
            used for classification; all other fields are ignored by this stub.

        Returns
        -------
        DiagnosisOutput
            A fully-populated ``DiagnosisOutput`` with ``ai_source`` always
            set to ``"StubAIProvider"`` and ``raw_response`` always ``None``.
        """
        # --- Step 1: pre-classified ---
        if context.failure_category is not None:
            return DiagnosisOutput(
                failure_category=context.failure_category,
                confidence_score=_CONFIDENCE_PRE_CLASSIFIED,
                ai_source="StubAIProvider",
                reasoning=(
                    "Failure category was pre-classified before StubAIProvider invocation."
                ),
                raw_response=None,
            )

        # --- Step 2: no error_code ---
        if context.error_code is None:
            return DiagnosisOutput(
                failure_category=FailureCategory.UNKNOWN,
                confidence_score=_CONFIDENCE_UNKNOWN,
                ai_source="StubAIProvider",
                reasoning=(
                    "No error_code available for classification; defaulting to UNKNOWN."
                ),
                raw_response=None,
            )

        # --- Step 3 & 4: normalize and exact lookup ---
        normalized = _normalize_error_code(context.error_code)
        category = _ERROR_CODE_MAP.get(normalized)

        # --- Step 5: partial/prefix lookup ---
        if category is None:
            for key, mapped_category in _ERROR_CODE_MAP.items():
                if key in normalized:
                    category = mapped_category
                    break

        # --- Step 6: fallback ---
        if category is None:
            return DiagnosisOutput(
                failure_category=FailureCategory.UNKNOWN,
                confidence_score=_CONFIDENCE_UNKNOWN,
                ai_source="StubAIProvider",
                reasoning=(
                    f"error_code not in known classification map: '{context.error_code}'"
                ),
                raw_response=None,
            )

        return DiagnosisOutput(
            failure_category=category,
            confidence_score=_CONFIDENCE_KNOWN,
            ai_source="StubAIProvider",
            reasoning="Deterministic classification from error_code",
            raw_response=None,
        )
