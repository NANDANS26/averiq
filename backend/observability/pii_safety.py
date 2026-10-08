"""PII safety guardrails for structured logging and audit payloads.

Enumerates forbidden fields and provides a sanitizer that strips/redacts
them before emission to structured logs or audit_events.payload.

AUD-006: audit_events.payload must never contain raw gateway payloads,
card numbers, bank account numbers, auth credentials, or raw PII.

Requirements: NFR-OBS-007, AUD-006
"""
from __future__ import annotations

import re
from typing import Any

# Forbidden fields — never appear in logs or audit_events.payload
FORBIDDEN_LOG_FIELDS: frozenset[str] = frozenset({
    # Gateway/webhook
    "raw_body", "raw_payload", "raw_response", "webhook_body",
    "provider_response", "gateway_response_raw",
    # Financial PII
    "card_number", "cvv", "expiry", "card_expiry",
    "bank_account_number", "bank_account", "ifsc_code",
    # Auth
    "password", "hashed_password", "password_hash",
    "webhook_secret", "app_secret_key", "api_key", "api_secret",
    "access_token", "refresh_token", "token_secret",
    # Customer PII
    "full_name", "date_of_birth", "national_id",
})

# Patterns to detect PII-like values in string fields
_CARD_PATTERN = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SECRET_KEY_PATTERN = re.compile(r"(secret|key|password|token)\s*[:=]\s*\S+", re.IGNORECASE)

_REDACTED = "[REDACTED]"


def sanitize_log_record(record: dict[str, Any]) -> dict[str, Any]:
    """Remove or redact forbidden fields from a log record dict.

    Operates recursively on nested dicts. Modifies and returns a copy.
    """
    return _sanitize_dict(record)


def sanitize_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove or redact PII from an audit_events.payload dict.

    AUD-006: the payload must not contain raw gateway data, card numbers,
    bank account numbers, or auth credentials.
    """
    return _sanitize_dict(payload)


def _sanitize_dict(d: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in d.items():
        if key in FORBIDDEN_LOG_FIELDS:
            result[key] = _REDACTED
        elif isinstance(value, dict):
            result[key] = _sanitize_dict(value)
        elif isinstance(value, list):
            result[key] = [_sanitize_dict(v) if isinstance(v, dict) else v for v in value]
        elif isinstance(value, str):
            result[key] = _sanitize_string(value)
        else:
            result[key] = value
    return result


def _sanitize_string(value: str) -> str:
    # Redact card number patterns
    value = _CARD_PATTERN.sub(_REDACTED, value)
    # Redact secret-like key=value patterns
    value = _SECRET_KEY_PATTERN.sub(lambda m: f"{m.group(1).split()[0]}={_REDACTED}", value)
    return value
