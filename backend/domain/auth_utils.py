"""Password hashing and verification utilities for Averiq.

Provides ``hash_password`` and ``verify_password`` as importable helpers so
any module (seed scripts, management commands, tests) can hash or verify
passwords without importing the full auth router.

``passlib[bcrypt]`` is used with the deprecated="auto" policy so old hashes
using a superseded scheme are transparently recognised and flagged for re-hash.

SEC-AUTH-012: webhook_secret is NOT handled here — it uses Fernet-based
reversible encryption (``backend.domain.crypto``) because it must be
retrieved verbatim for HMAC-SHA256 signature verification.
"""

from __future__ import annotations

from passlib.context import CryptContext

# Single, shared CryptContext instance.
# bcrypt is the only active scheme; "auto" handles legacy hash upgrades.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*.

    Use this whenever a new user password is created or updated.
    Never store the raw plaintext.
    """
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return ``True`` if *plain* matches *hashed*.

    Uses constant-time comparison internally to prevent timing attacks.
    """
    return _pwd_context.verify(plain, hashed)


__all__ = ["hash_password", "verify_password"]
