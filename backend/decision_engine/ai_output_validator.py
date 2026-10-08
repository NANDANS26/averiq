"""AI output validation — FailureCategory allowlist (SEC-AI-001, SEC-AI-002).

This module validates the ``failure_category`` field returned by an AI/LLM
provider against the authoritative ``FailureCategory`` enum allowlist before
any downstream decision or persistence occurs.

Design rationale (SEC-AI-001, SEC-AI-002):
  - All AI output enum fields MUST be validated against a defined allowlist
    before use.
  - Unknown category values trigger the deterministic fallback classifier;
    they are NEVER propagated into the decision pipeline.
  - Rejection is logged as a structured warning.  The raw LLM value is
    truncated to at most 20 characters in the log to avoid PII leakage
    (NFR-OBS-007) while still providing enough information for debugging.

Requirements: SEC-AI-001, SEC-AI-002
"""

from __future__ import annotations

import structlog

from shared.contracts.enums import FailureCategory

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Allowlist — derived once at module load from the canonical enum definition.
# This is the single source of truth for what constitutes a valid AI output
# for failure_category.  Adding a new category to FailureCategory
# automatically expands the allowlist here.
# ---------------------------------------------------------------------------

_VALID_CATEGORIES: frozenset[str] = frozenset(c.value for c in FailureCategory)


def validate_failure_category(raw_value: str | None) -> FailureCategory | None:
    """Validate a raw ``failure_category`` string against the allowlist.

    Called immediately after receiving an AI/LLM response, before the value
    is used in any downstream decision (Intervention_Engine, Policy_Engine)
    or persisted to the database.

    Behaviour:
      - ``None`` input → returns ``None`` (caller must fall back to the
        deterministic classifier; this is expected when the LLM omits the
        field entirely).
      - Valid enum value → returns the corresponding ``FailureCategory``
        member.
      - Invalid / unknown value → logs a structured warning (value is
        truncated to ≤20 chars to avoid PII leakage) and returns ``None``,
        signalling that the deterministic fallback should be invoked.

    Args:
        raw_value:
            The raw string extracted from the AI provider response, or
            ``None`` if the field was absent.

    Returns:
        A ``FailureCategory`` enum member if the value is valid, or ``None``
        if the value was absent or not recognised by the allowlist.

    SEC-AI-001: validates all AI output against the defined schema before use.
    SEC-AI-002: output enum fields validated against allowlist before use;
                unknown values trigger deterministic fallback.
    NFR-OBS-007: raw value is truncated/redacted in logs to prevent PII leakage.
    """
    if raw_value is None:
        return None

    if raw_value not in _VALID_CATEGORIES:
        # Truncate to ≤20 chars and use repr() so control characters and
        # injection attempts are rendered safely in the structured log.
        safe_repr = (
            repr(raw_value[:20]) if len(raw_value) > 20 else repr(raw_value)
        )
        logger.warning(
            "ai_output_validator.invalid_failure_category",
            safe_category_repr=safe_repr,
            note="Unknown FailureCategory rejected; fallback invoked (SEC-AI-001)",
        )
        return None

    return FailureCategory(raw_value)
