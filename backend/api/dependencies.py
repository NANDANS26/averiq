"""FastAPI dependency injection for authentication and RBAC.

Dependencies:
  get_current_user()    — decode + verify JWT; check revocation; return authenticated user
  require_role(role)    — factory returning a dependency that enforces minimum role
  get_merchant_id()     — extracts authenticated merchant_id for repository scoping

RBAC Role Hierarchy (SEC-AUTH-002):
  ADMIN > OPERATOR > VIEWER > SYSTEM (internal only)

Permissions Matrix:
  VIEWER:   GET /cases, GET /audit, GET /analytics — read-only
  OPERATOR: VIEWER + approve/reject human reviews, trigger manual actions
  ADMIN:    OPERATOR + manage users, activate/retire policies, manage merchants
  SYSTEM:   Internal service calls only; must NEVER be assigned via API

SYSTEM role protection (SEC-AUTH-006):
  Any request payload containing role='SYSTEM' must be rejected with HTTP 422.
  This is enforced at the dependency layer via a Pydantic validator mixed into
  request schemas — see backend.api.validators.SystemRoleGuard.

Requirements: SEC-AUTH-002, SEC-AUTH-003, SEC-AUTH-004, SEC-AUTH-005, SEC-AUTH-006
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import ExpiredSignatureError, JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_session
from backend.domain.models import TokenRevocation, User

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# JWT configuration
# ---------------------------------------------------------------------------

ALGORITHM = "HS256"

# tokenUrl must match the login endpoint defined in backend/api/auth.py (task 4.1)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# ---------------------------------------------------------------------------
# Role hierarchy
# ---------------------------------------------------------------------------

# SYSTEM is excluded from the hierarchy with a sentinel value of -1 because it
# is an internal-only pseudo-role that must never be used via the HTTP API.
# Any user object with role='SYSTEM' that somehow reaches get_current_user()
# is rejected immediately with HTTP 403.
ROLE_HIERARCHY: dict[str, int] = {
    "VIEWER": 0,
    "OPERATOR": 1,
    "ADMIN": 2,
    # SYSTEM is intentionally excluded from the numeric hierarchy so that
    # ROLE_HIERARCHY.get("SYSTEM", -1) always returns -1, which is below
    # every named role. This prevents SYSTEM from satisfying require_role().
}


# ---------------------------------------------------------------------------
# Authenticated user context
# ---------------------------------------------------------------------------


class CurrentUser:
    """Authenticated user context injected into endpoint handlers.

    Attributes:
        user:        The full ORM User object (lazy-load safe; already fetched).
        merchant_id: The UUID of the merchant this user belongs to.
        user_id:     Shortcut to user.id.
        role:        Shortcut to user.role (ADMIN | OPERATOR | VIEWER).
    """

    __slots__ = ("merchant_id", "role", "user", "user_id")

    def __init__(self, user: User, merchant_id: uuid.UUID) -> None:
        self.user = user
        self.merchant_id = merchant_id
        self.user_id: uuid.UUID = user.id
        self.role: str = user.role

    def __repr__(self) -> str:
        return (
            f"<CurrentUser user_id={self.user_id!s}"
            f" role={self.role!r}"
            f" merchant_id={self.merchant_id!s}>"
        )


# ---------------------------------------------------------------------------
# Core authentication dependency
# ---------------------------------------------------------------------------


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentUser:
    """Decode the Bearer JWT, verify it has not been revoked, and load the User.

    Raises:
        HTTP 401 — if the token is missing, malformed, expired, or revoked.
        HTTP 403 — if the authenticated user has the SYSTEM role (internal only).

    Steps:
      1. Decode JWT and validate signature using APP_SECRET_KEY.
      2. Extract 'jti' and 'sub' (user_id) from payload.
      3. Check token_revocations table for the jti.
      4. Load User from DB; verify is_active=True.
      5. Reject SYSTEM role.
      6. Return CurrentUser context.

    Requirements: SEC-AUTH-001, SEC-AUTH-008, SEC-AUTH-009
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # ------------------------------------------------------------------
    # Step 1: Decode and verify JWT signature
    # ------------------------------------------------------------------
    try:
        payload = jwt.decode(
            token,
            settings.APP_SECRET_KEY,
            algorithms=[ALGORITHM],
        )
    except ExpiredSignatureError:
        logger.warning("auth.token_expired")
        raise credentials_exception
    except JWTError:
        logger.warning("auth.token_invalid")
        raise credentials_exception

    # ------------------------------------------------------------------
    # Step 2: Extract required claims
    # ------------------------------------------------------------------
    jti_raw: str | None = payload.get("jti")
    user_id_raw: str | None = payload.get("sub")
    merchant_id_raw: str | None = payload.get("merchant_id")

    if not jti_raw or not user_id_raw or not merchant_id_raw:
        logger.warning("auth.token_missing_claims", jti=bool(jti_raw), sub=bool(user_id_raw))
        raise credentials_exception

    try:
        jti = uuid.UUID(jti_raw)
        user_id = uuid.UUID(user_id_raw)
        merchant_id = uuid.UUID(merchant_id_raw)
    except ValueError:
        logger.warning("auth.token_invalid_uuid_claims")
        raise credentials_exception

    # ------------------------------------------------------------------
    # Step 3: Revocation check — O(1) PK lookup on token_revocations
    # ------------------------------------------------------------------
    revoked: TokenRevocation | None = await session.get(TokenRevocation, jti)
    if revoked is not None:
        logger.warning("auth.token_revoked", jti=str(jti), user_id=str(user_id))
        raise credentials_exception

    # ------------------------------------------------------------------
    # Step 4: Load User and verify account is active
    # ------------------------------------------------------------------
    user: User | None = await session.get(User, user_id)
    if user is None or not user.is_active:
        logger.warning("auth.user_not_found_or_inactive", user_id=str(user_id))
        raise credentials_exception

    # ------------------------------------------------------------------
    # Step 5: Reject SYSTEM role — internal-only pseudo-role (SEC-AUTH-006)
    # ------------------------------------------------------------------
    if user.role == "SYSTEM":
        logger.warning(
            "auth.system_role_rejected_via_api",
            user_id=str(user_id),
            merchant_id=str(merchant_id),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SYSTEM role cannot authenticate via the HTTP API",
        )

    # ------------------------------------------------------------------
    # Step 6: Return authenticated context
    # ------------------------------------------------------------------
    return CurrentUser(user=user, merchant_id=merchant_id)


# ---------------------------------------------------------------------------
# Role-based access control dependency factory
# ---------------------------------------------------------------------------


def require_role(minimum_role: str) -> Any:
    """Return a FastAPI dependency that enforces a minimum role.

    The role hierarchy is:
      VIEWER (0) < OPERATOR (1) < ADMIN (2)

    SYSTEM is excluded: SYSTEM actors never arrive via the HTTP API. Any
    authenticated user with role SYSTEM is rejected by get_current_user()
    before reaching this guard.

    Args:
        minimum_role: One of "VIEWER", "OPERATOR", "ADMIN".

    Returns:
        An async FastAPI dependency that yields the CurrentUser on success
        or raises HTTP 403 on insufficient role.

    Requirements: SEC-AUTH-003, SEC-AUTH-004, SEC-AUTH-005
    """

    async def _check_role(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        caller_level = ROLE_HIERARCHY.get(current_user.role, -1)
        required_level = ROLE_HIERARCHY.get(minimum_role, 0)

        if caller_level < required_level:
            logger.warning(
                "authz.insufficient_role",
                user_id=str(current_user.user_id),
                user_role=current_user.role,
                required_role=minimum_role,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {minimum_role} role or higher",
            )

        return current_user

    return _check_role


# ---------------------------------------------------------------------------
# Convenience dependency aliases
# ---------------------------------------------------------------------------

# Use these as endpoint dependencies:
#   @router.get("/cases", dependencies=[Depends(require_viewer)])
#   async def list_cases(current_user: CurrentUser = Depends(require_viewer)):

require_viewer = require_role("VIEWER")
require_operator = require_role("OPERATOR")
require_admin = require_role("ADMIN")


# ---------------------------------------------------------------------------
# Merchant ID helper
# ---------------------------------------------------------------------------


async def get_merchant_id(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> uuid.UUID:
    """Extract the authenticated merchant_id from the current user context.

    Convenience dependency for endpoints and repositories that need the
    merchant_id for scoped queries without the full CurrentUser object.

    Usage::

        @router.get("/cases")
        async def list_cases(merchant_id: uuid.UUID = Depends(get_merchant_id)):
            ...
    """
    return current_user.merchant_id


# ---------------------------------------------------------------------------
# Type alias for annotated dependencies (Python 3.12 style)
# ---------------------------------------------------------------------------
