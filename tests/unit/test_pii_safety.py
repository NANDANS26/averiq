"""Unit tests for PII safety guardrails (task 19.2).

Tests:
  1. sanitize_log_record redacts the ``password`` field.
  2. sanitize_log_record redacts the ``webhook_secret`` field.
  3. sanitize_log_record passes through non-PII fields unchanged.
  4. sanitize_audit_payload redacts the ``raw_body`` field (AUD-006).
  5. FORBIDDEN_LOG_FIELDS contains at least 10 entries.

Requirements: NFR-OBS-007, AUD-006
"""
from __future__ import annotations

import pytest

from backend.observability.pii_safety import (
    FORBIDDEN_LOG_FIELDS,
    sanitize_audit_payload,
    sanitize_log_record,
)

_REDACTED = "[REDACTED]"


# ---------------------------------------------------------------------------
# Test 1: sanitize_log_record redacts ``password``
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sanitize_log_record_redacts_password() -> None:
    """sanitize_log_record must replace the value of 'password' with [REDACTED].

    Ensures that plaintext passwords never leak into structured log output.
    """
    record = {
        "event": "user.login",
        "email": "user@example.com",
        "password": "super_secret_123",
    }
    result = sanitize_log_record(record)

    assert result["password"] == _REDACTED, (
        "The 'password' field must be redacted in log records"
    )
    # Non-sensitive fields must pass through unchanged
    assert result["event"] == "user.login"
    assert result["email"] == "user@example.com"


# ---------------------------------------------------------------------------
# Test 2: sanitize_log_record redacts ``webhook_secret``
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sanitize_log_record_redacts_webhook_secret() -> None:
    """sanitize_log_record must replace the value of 'webhook_secret' with [REDACTED].

    Webhook secrets are auth credentials; they must never appear in logs.
    """
    record = {
        "event": "webhook.verified",
        "merchant_id": "merch_abc123",
        "webhook_secret": "whsec_verysecretvalue",
    }
    result = sanitize_log_record(record)

    assert result["webhook_secret"] == _REDACTED, (
        "The 'webhook_secret' field must be redacted in log records"
    )
    assert result["merchant_id"] == "merch_abc123"


# ---------------------------------------------------------------------------
# Test 3: sanitize_log_record passes through non-PII fields unchanged
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sanitize_log_record_passes_through_safe_fields() -> None:
    """sanitize_log_record must leave non-sensitive fields untouched.

    Only FORBIDDEN_LOG_FIELDS and string patterns matching PII should be
    altered. Business metadata like case_id, amount, state must be preserved.
    """
    record = {
        "event": "case.state_changed",
        "case_id": "case_xyz789",
        "old_state": "OPEN",
        "new_state": "INTERVENTION_PENDING",
        "amount_paise": 150000,
        "merchant_id": "merch_abc123",
    }
    result = sanitize_log_record(record)

    assert result["case_id"] == "case_xyz789"
    assert result["old_state"] == "OPEN"
    assert result["new_state"] == "INTERVENTION_PENDING"
    assert result["amount_paise"] == 150000
    assert result["merchant_id"] == "merch_abc123"
    assert result["event"] == "case.state_changed"


# ---------------------------------------------------------------------------
# Test 4: sanitize_audit_payload redacts ``raw_body`` (AUD-006)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sanitize_audit_payload_redacts_raw_body() -> None:
    """sanitize_audit_payload must redact 'raw_body' from audit payloads (AUD-006).

    audit_events.payload must never contain raw gateway payloads to comply
    with AUD-006 (no raw PII in audit log).
    """
    payload = {
        "event_type": "payment.failed",
        "payment_id": "pay_ABC123",
        "raw_body": '{"card_number": "4111111111111111", "amount": 10000}',
        "error_code": "INSUFFICIENT_FUNDS",
    }
    result = sanitize_audit_payload(payload)

    assert result["raw_body"] == _REDACTED, (
        "AUD-006: 'raw_body' must be redacted from audit payloads"
    )
    # Safe fields must remain
    assert result["payment_id"] == "pay_ABC123"
    assert result["error_code"] == "INSUFFICIENT_FUNDS"


# ---------------------------------------------------------------------------
# Test 5: FORBIDDEN_LOG_FIELDS contains at least 10 entries
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_forbidden_log_fields_has_minimum_entries() -> None:
    """FORBIDDEN_LOG_FIELDS must contain at least 10 distinct field names.

    A comprehensive blocklist is required to prevent accidental PII leakage
    across all categories: auth credentials, financial PII, and gateway data.
    """
    assert len(FORBIDDEN_LOG_FIELDS) >= 10, (
        f"FORBIDDEN_LOG_FIELDS must contain at least 10 entries, "
        f"got {len(FORBIDDEN_LOG_FIELDS)}: {sorted(FORBIDDEN_LOG_FIELDS)}"
    )
    # Spot-check key categories are represented
    assert "password" in FORBIDDEN_LOG_FIELDS, "Auth credential 'password' must be forbidden"
    assert "card_number" in FORBIDDEN_LOG_FIELDS, "Financial PII 'card_number' must be forbidden"
    assert "raw_body" in FORBIDDEN_LOG_FIELDS, "Gateway payload 'raw_body' must be forbidden"
