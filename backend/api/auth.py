"""Authentication API router for Averiq.

Provides:
  POST /api/v1/auth/login   — authenticates with email + password, returns JWT
  POST /api/v1/auth/logout  — revokes active JWT by storing jti in token_revocations

Requirements: SEC-AUTH-001, SEC-AUTH-002, SEC-AUTH-008, SEC-AUTH-009, SEC-AUTH-012
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import ALGORITHM, CurrentUser, get_current_user, oauth2_scheme
from backend.config import settings
from backend.database import get_session
from backend.domain.auth_utils import verify_password
from backend.domain.models import TokenRevocation, User

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    merchant_id: str
    role: str
    email: str


class LogoutResponse(BaseModel):
    status: str = "logged_out"


def create_access_token(
    user_id: uuid.UUID,
    merchant_id: uuid.UUID,
    role: str,
    email: str,
    expires_delta: timedelta | None = None,
) -> tuple[str, uuid.UUID, datetime]:
    """Generate a signed JWT access token conforming to backend.api.dependencies."""
    now = datetime.now(timezone.utc)
    delta = expires_delta or timedelta(seconds=settings.TOKEN_EXPIRY_SECONDS)
    expire = now + delta
    jti = uuid.uuid4()

    payload = {
        "sub": str(user_id),
        "merchant_id": str(merchant_id),
        "role": role,
        "email": email,
        "jti": str(jti),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    encoded_jwt = jwt.encode(payload, settings.APP_SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt, jti, expire


@router.post(
    "/auth/login",
    response_model=LoginResponse,
    summary="Authenticate user and issue JWT access token",
)
async def login(
    req: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LoginResponse:
    """Validate credentials and return signed JWT."""
    stmt = select(User).where(User.email == req.email.lower().strip())
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    invalid_cred_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if user is None:
        logger.warning("auth.login_user_not_found", email=req.email)
        raise invalid_cred_exception

    if not user.is_active:
        logger.warning("auth.login_user_inactive", user_id=str(user.id))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    if user.role == "SYSTEM":
        logger.warning("auth.login_system_role_forbidden", user_id=str(user.id))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SYSTEM role cannot authenticate via the HTTP API",
        )

    if not verify_password(req.password, user.password_hash):
        logger.warning("auth.login_invalid_password", user_id=str(user.id))
        raise invalid_cred_exception

    token, jti, expires_at = create_access_token(
        user_id=user.id,
        merchant_id=user.merchant_id,
        role=user.role,
        email=user.email,
    )

    logger.info(
        "auth.login_success",
        user_id=str(user.id),
        merchant_id=str(user.merchant_id),
        role=user.role,
    )

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.TOKEN_EXPIRY_SECONDS,
        user_id=str(user.id),
        merchant_id=str(user.merchant_id),
        role=user.role,
        email=user.email,
    )


@router.post(
    "/auth/logout",
    response_model=LogoutResponse,
    summary="Revoke active JWT token",
)
async def logout(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    token: Annotated[str, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LogoutResponse:
    """Revoke current access token by adding its jti to token_revocations."""
    try:
        payload = jwt.decode(
            token,
            settings.APP_SECRET_KEY,
            algorithms=[ALGORITHM],
        )
        jti_str = payload.get("jti")
        exp_timestamp = payload.get("exp")
        if not jti_str or not exp_timestamp:
            raise HTTPException(status_code=400, detail="Invalid token claims for logout")
        
        jti = uuid.UUID(jti_str)
        expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
        now = datetime.now(timezone.utc)

        revocation = TokenRevocation(
            jti=jti,
            user_id=current_user.user_id,
            revoked_at=now,
            expires_at=expires_at,
        )
        session.add(revocation)
        await session.commit()

        logger.info(
            "auth.logout_success",
            user_id=str(current_user.user_id),
            jti=str(jti),
        )
        return LogoutResponse()
    except (JWTError, ValueError) as exc:
        logger.warning("auth.logout_token_decode_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to decode token for logout",
        )
