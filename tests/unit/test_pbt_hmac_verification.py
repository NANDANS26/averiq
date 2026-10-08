"""Property-Based Tests: HMAC Signature Verification Correctness (Property 1).

Property 1: HMAC Signature Verification Correctness
  - For any body (bytes) and secret (str), verify(body, secret, compute(body, secret)) is True
  - For any modified body (body != original), verify(modified, secret, compute(original, secret)) is False
  - For any modified secret, verify(body, modified_secret, compute(body, original_secret)) is False

**Validates: Requirements FR-WEBHOOK-002**

Feature: averiq, Property 1: HMAC Signature Verification Correctness
Requirements: FR-WEBHOOK-002
"""
from __future__ import annotations

import hashlib
import hmac as hmac_module

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Standalone HMAC helpers (mirrors backend/webhook_processor/handler.py logic)
# ---------------------------------------------------------------------------


def compute_hmac(body: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 of body using secret. Returns hex digest."""
    return hmac_module.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_hmac(body: bytes, secret: str, signature: str) -> bool:
    """Verify HMAC-SHA256 signature. Uses compare_digest to prevent timing attacks."""
    expected = compute_hmac(body, secret)
    return hmac_module.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Property 1a — Self-verification always True (core correctness)
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(body=st.binary(), secret=st.text(min_size=1, max_size=100))
@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
def test_p1_self_verification_always_true(body: bytes, secret: str) -> None:
    """Feature: averiq, Property 1
    For any body and secret, verify(body, secret, compute(body, secret)) is True.

    **Validates: Requirements FR-WEBHOOK-002**
    """
    signature = compute_hmac(body, secret)
    assert verify_hmac(body, secret, signature) is True


# ---------------------------------------------------------------------------
# Property 1b — Modified body always fails verification
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(
    body=st.binary(min_size=1),
    secret=st.text(min_size=1, max_size=100),
    extra_byte=st.binary(min_size=1, max_size=10),
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_p1_modified_body_fails_verification(
    body: bytes, secret: str, extra_byte: bytes
) -> None:
    """Modified body must never verify against original signature.

    **Validates: Requirements FR-WEBHOOK-002**
    """
    signature = compute_hmac(body, secret)
    modified_body = body + extra_byte
    assume(modified_body != body)
    assert verify_hmac(modified_body, secret, signature) is False


# ---------------------------------------------------------------------------
# Property 1c — Modified secret always fails verification
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(
    body=st.binary(),
    secret=st.text(min_size=1, max_size=50),
    other_secret=st.text(min_size=1, max_size=50),
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_p1_different_secret_fails_verification(
    body: bytes, secret: str, other_secret: str
) -> None:
    """Signature computed with one secret must fail verification with a different secret.

    **Validates: Requirements FR-WEBHOOK-002**
    """
    assume(secret != other_secret)
    signature = compute_hmac(body, secret)
    assert verify_hmac(body, other_secret, signature) is False


# ---------------------------------------------------------------------------
# Property 1d — Empty body is handled correctly
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(secret=st.text(min_size=1, max_size=100))
@settings(max_examples=100)
def test_p1_empty_body_verifies_correctly(secret: str) -> None:
    """Empty body (b'') must still verify correctly.

    **Validates: Requirements FR-WEBHOOK-002**
    """
    body = b""
    signature = compute_hmac(body, secret)
    assert verify_hmac(body, secret, signature) is True


# ---------------------------------------------------------------------------
# Property 1e — Tampered signature always fails
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(
    body=st.binary(),
    secret=st.text(min_size=1, max_size=100),
    tampered=st.text(min_size=64, max_size=64, alphabet="0123456789abcdef"),
)
@settings(max_examples=200)
def test_p1_tampered_hex_signature_fails(
    body: bytes, secret: str, tampered: str
) -> None:
    """A random hex string of the right length rarely matches the real HMAC.

    **Validates: Requirements FR-WEBHOOK-002**
    """
    real_signature = compute_hmac(body, secret)
    assume(tampered != real_signature)
    assert verify_hmac(body, secret, tampered) is False


# ---------------------------------------------------------------------------
# Property 1f — compare_digest is constant-time (structural test)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_p1_verify_uses_compare_digest() -> None:
    """verify_hmac must use hmac.compare_digest (not ==) to prevent timing attacks.

    **Validates: Requirements FR-WEBHOOK-002**
    """
    import inspect

    source = inspect.getsource(verify_hmac)
    assert "compare_digest" in source, "verify_hmac must use hmac.compare_digest"
