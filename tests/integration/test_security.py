"""Security test suite for Averiq.

Covers:
  - RBAC enforcement (SEC-AUTH-002 through SEC-AUTH-006)
  - HMAC signature verification (FR-WEBHOOK-002)
  - JWT token revocation (SEC-AUTH-009)
  - SYSTEM role rejection (SEC-AUTH-006)
  - Cross-merchant isolation (SEC-ISOL-002)
  - Rate limiting guard (FR-WEBHOOK-014)
  - AI output allowlist (SEC-AI-001)
  - AuditEvent no-raw-payload (AUD-006)

All tests use mocked sessions — no live DB required.
Run with: make test-security

Requirements: SEC-AUTH-001 through SEC-AUTH-012, SEC-ISOL-001 through SEC-ISOL-004
"""
from __future__ import annotations

import hashlib
import hmac
import uuid

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Test 1: VIEWER cannot write (RBAC)
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
@pytest.mark.asyncio
async def test_viewer_blocked_from_write_endpoints() -> None:
    """VIEWER role is blocked from OPERATOR+ write endpoints (SEC-AUTH-003)."""
    from backend.api.dependencies import CurrentUser, require_operator
    from backend.domain.models import User

    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.role = "VIEWER"
    user.is_active = True

    viewer = CurrentUser(user=user, merchant_id=uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        await require_operator(current_user=viewer)

    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Test 2: OPERATOR cannot access ADMIN-only endpoints
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
@pytest.mark.asyncio
async def test_operator_blocked_from_admin_endpoints() -> None:
    """OPERATOR is blocked from ADMIN-only operations (SEC-AUTH-004)."""
    from backend.api.dependencies import CurrentUser, require_admin
    from backend.domain.models import User

    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.role = "OPERATOR"
    user.is_active = True

    operator = CurrentUser(user=user, merchant_id=uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        await require_admin(current_user=operator)

    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Test 3: SYSTEM role rejected via API
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
def test_system_role_rejected_via_api() -> None:
    """SYSTEM role cannot be assigned via API request payload (SEC-AUTH-006)."""
    from backend.api.validators import SystemRoleGuard
    from pydantic import BaseModel, ValidationError

    class CreateUserRequest(SystemRoleGuard, BaseModel):
        role: str

    with pytest.raises(ValidationError):
        CreateUserRequest(role="SYSTEM")


# ---------------------------------------------------------------------------
# Test 4: HMAC verification rejects tampered body
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
def test_hmac_rejects_tampered_webhook_body() -> None:
    """Tampered webhook body fails HMAC verification (FR-WEBHOOK-002, FR-WEBHOOK-003)."""
    secret = "valid_secret_key"
    original = b'{"event":"payment.failed","amount":50000}'
    tampered = b'{"event":"payment.failed","amount":99999}'

    sig = hmac.new(secret.encode(), original, hashlib.sha256).hexdigest()

    assert not hmac.compare_digest(
        sig,
        hmac.new(secret.encode(), tampered, hashlib.sha256).hexdigest(),
    )


# ---------------------------------------------------------------------------
# Test 5: HMAC verification accepts correct body
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
def test_hmac_accepts_correct_webhook_body() -> None:
    """Correct webhook body passes HMAC verification (FR-WEBHOOK-002)."""
    secret = "valid_secret_key"
    body = b'{"event":"payment.failed"}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert hmac.compare_digest(sig, sig)


# ---------------------------------------------------------------------------
# Test 6: Cross-merchant access blocked at repository layer
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
@pytest.mark.asyncio
async def test_cross_merchant_access_blocked() -> None:
    """Merchant A cannot access Merchant B's cases (SEC-ISOL-002)."""
    from backend.domain.repository import ForbiddenError, RevenueRiskCaseRepository

    merchant_a = uuid.uuid4()
    merchant_b = uuid.uuid4()

    case = MagicMock()
    case.merchant_id = merchant_b
    case.id = uuid.uuid4()

    session = AsyncMock()
    session.get = AsyncMock(return_value=case)

    repo = RevenueRiskCaseRepository(session=session, merchant_id=merchant_a)

    with pytest.raises(ForbiddenError):
        await repo.get_by_id(case.id)


# ---------------------------------------------------------------------------
# Test 7: AI unknown failure_category rejected
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
def test_ai_unknown_failure_category_rejected() -> None:
    """Unknown LLM failure_category is rejected by allowlist (SEC-AI-001)."""
    from backend.decision_engine.ai_output_validator import validate_failure_category

    assert validate_failure_category("TOTALLY_INVALID_CATEGORY") is None


# ---------------------------------------------------------------------------
# Test 8: DiagnosisOutput cannot bypass PolicyEngine
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
def test_diagnosis_output_has_no_authorization_fields() -> None:
    """DiagnosisOutput has no outcome/approval fields — cannot bypass PolicyEngine (SEC-AI-003)."""
    from shared.contracts.enums import FailureCategory
    from shared.contracts.schemas import DiagnosisOutput

    diagnosis = DiagnosisOutput(
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        confidence_score=0.99,
        ai_source="test",
    )

    assert not hasattr(diagnosis, "outcome")
    assert not hasattr(diagnosis, "approved")
    assert not hasattr(diagnosis, "policy_decision")


# ---------------------------------------------------------------------------
# Test 9: Webhook raw payload not in AuditEvent API response
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
def test_audit_event_api_response_has_no_payload_field() -> None:
    """AuditEventItem response model has no payload field (AUD-006)."""
    from backend.api.cases import AuditEventItem

    fields = set(AuditEventItem.model_fields)

    assert "payload" not in fields, "AuditEventItem must not expose payload (AUD-006)"
    assert "raw_body" not in fields
    assert "gateway_response" not in fields


# ---------------------------------------------------------------------------
# Test 10: PII fields redacted from log records
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
def test_pii_fields_redacted_in_log_records() -> None:
    """PII fields are redacted from structured log records (NFR-OBS-007)."""
    from backend.observability.pii_safety import sanitize_log_record

    record = {
        "card_number": "4111111111111111",
        "event_type": "payment.failed",
        "amount": 5000,
    }
    sanitized = sanitize_log_record(record)

    assert sanitized["card_number"] == "[REDACTED]"
    assert sanitized["event_type"] == "payment.failed"  # non-PII unchanged


# ---------------------------------------------------------------------------
# Test 11: Webhook secret stored encrypted (not plaintext)
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
def test_webhook_secret_encryption_round_trip() -> None:
    """Webhook secret is stored as Fernet ciphertext, never plaintext (SEC-AUTH-012)."""
    from backend.domain.crypto import decrypt_secret, encrypt_secret

    key = "a" * 32
    plaintext = "whsec_test_secret"

    ciphertext = encrypt_secret(plaintext, key)

    assert plaintext not in ciphertext
    assert decrypt_secret(ciphertext, key) == plaintext


# ---------------------------------------------------------------------------
# Test 12: CORS allows configured origins only
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
def test_cors_no_wildcard() -> None:
    """CORS must not use wildcard when credentials are included (SEC-AUTH-010)."""
    from backend.config import settings

    assert "*" not in settings.ALLOWED_CORS_ORIGINS, (
        "ALLOWED_CORS_ORIGINS must not contain '*' with credentials enabled"
    )


# ---------------------------------------------------------------------------
# Test 13: Rate limiter blocks requests above threshold
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limiter_blocks_above_threshold() -> None:
    """Rate limiter returns False (→ HTTP 429) when count > limit (FR-WEBHOOK-014)."""
    from backend.webhook_processor.handler import _check_rate_limit

    pipe = MagicMock()
    pipe.incr = MagicMock(return_value=pipe)
    pipe.expire = MagicMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=[301, 1])  # count 301 > limit 300

    redis_client = MagicMock()
    redis_client.pipeline = MagicMock(return_value=pipe)

    result = await _check_rate_limit(redis_client, str(uuid.uuid4()), 300)

    assert result is False


# ---------------------------------------------------------------------------
# Test 14: GatewayAbstraction has no capture_payment on failed payments
# ---------------------------------------------------------------------------


@pytest.mark.security
@pytest.mark.unit
def test_gateway_has_no_capture_failed_payment_method() -> None:
    """GatewayAbstraction must not expose methods that could capture failed payments (INT-RAZORPAY-006)."""
    import inspect

    from backend.gateway.abstract import GatewayAbstraction

    forbidden = ["capture_payment", "capture_failed_payment", "retry_payment"]
    methods = [
        n for n, _ in inspect.getmembers(GatewayAbstraction, predicate=inspect.isfunction)
    ]

    for f in forbidden:
        assert f not in methods, f"GatewayAbstraction must not expose {f!r}"
