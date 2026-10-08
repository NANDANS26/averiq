"""Application-level encryption for webhook secrets.

Uses Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256).
The APP_SECRET_KEY is used to derive a valid 32-byte Fernet key via
PBKDF2-HMAC-SHA256.

CRITICAL: The raw plaintext secret must NEVER appear in:
- API responses
- Structured logs
- Exception messages
- Database queries (only the ciphertext is persisted)

Why Fernet (not bcrypt)?
────────────────────────
Webhook secrets must be *retrieved* for HMAC-SHA256 signature verification
(FR-WEBHOOK-002).  bcrypt and Argon2 are one-way key-derivation functions
designed for passwords; they cannot be reversed.  Fernet provides symmetric
encryption so the ciphertext in ``merchants.webhook_secret`` can be decrypted
when needed for HMAC verification, while still protecting the secret at rest.

Key derivation
──────────────
APP_SECRET_KEY (arbitrary length) → SHA-256 digest (32 bytes)
→ base64url-encode → valid 32-byte Fernet key

This ensures any APP_SECRET_KEY that passes the minimum-length validation in
``backend.config.Settings`` produces a valid Fernet key without exposing the
raw secret directly as the key material.

Requirements: DATA-004, SEC-AUTH-012
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet


def _derive_fernet_key(app_secret_key: str) -> bytes:
    """Derive a 32-byte Fernet key from APP_SECRET_KEY using SHA-256.

    SHA-256 produces a 32-byte digest which, when base64url-encoded, yields
    exactly the 44-byte key format that Fernet requires.

    Args:
        app_secret_key: The value of the APP_SECRET_KEY environment variable.
            Must be at least 32 characters (enforced by Settings validator).

    Returns:
        44-byte base64url-encoded key suitable for ``Fernet(key)``.
    """
    digest = hashlib.sha256(app_secret_key.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(plaintext: str, key: str) -> str:
    """Encrypt a plaintext webhook secret using Fernet symmetric encryption.

    The returned ciphertext is a URL-safe base64-encoded string that should be
    stored verbatim in ``merchants.webhook_secret``.  It is safe to store in a
    TEXT column and to transmit over HTTPS.

    Args:
        plaintext: The raw webhook secret received from Razorpay.  Must not be
            logged or returned in API responses after this point.
        key: The APP_SECRET_KEY value.  Only the SHA-256 digest of this value
            is used as actual key material.

    Returns:
        Fernet-encrypted ciphertext as a UTF-8 string (base64url-encoded).

    Example::

        from backend.config import settings
        ciphertext = encrypt_secret(raw_secret, settings.APP_SECRET_KEY)
        merchant.webhook_secret = ciphertext
    """
    fernet = Fernet(_derive_fernet_key(key))
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str, key: str) -> str:
    """Decrypt a Fernet-encrypted webhook secret.

    Used at HMAC-SHA256 signature verification time (FR-WEBHOOK-002) to
    recover the raw webhook secret from its stored ciphertext.

    IMPORTANT: The returned plaintext must never be logged, included in error
    messages, or returned in any API response.

    Args:
        ciphertext: The base64url-encoded ciphertext from ``merchants.webhook_secret``.
        key: The APP_SECRET_KEY value (same key used during encryption).

    Returns:
        The original plaintext webhook secret as a UTF-8 string.

    Raises:
        cryptography.fernet.InvalidToken: If the ciphertext is corrupted,
            tampered, or was encrypted with a different key.  The caller must
            treat this as an unrecoverable error and should NOT expose the
            exception detail in API responses.

    Example::

        from backend.config import settings
        raw_secret = decrypt_secret(merchant.webhook_secret, settings.APP_SECRET_KEY)
        # Use raw_secret for HMAC verification; discard immediately after
    """
    fernet = Fernet(_derive_fernet_key(key))
    return fernet.decrypt(ciphertext.encode()).decode()
