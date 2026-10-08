"""Property-Based Tests: RBAC Authorization Enforcement (Property 11).

Property 11: RBAC Authorization Enforcement
  - For any VIEWER user and any write/execute endpoint, the require_operator/require_admin
    dependency raises HTTP 403.
  - For any OPERATOR user and any ADMIN-only endpoint, the require_admin dependency raises HTTP 403.
  - Any attempt to assign role='SYSTEM' via a request payload raises HTTP 422.

Feature: averiq, Property 11: RBAC Authorization Enforcement
Requirements: SEC-AUTH-003, SEC-AUTH-004, NFR-TEST-006

**Validates: Requirements SEC-AUTH-003, SEC-AUTH-004, NFR-TEST-006**
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from backend.api.dependencies import (
    ROLE_HIERARCHY,
    CurrentUser,
    require_role,
)
from backend.api.validators import SystemRoleGuard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_current_user(role: str) -> CurrentUser:
    """Build a CurrentUser with a mocked ORM User for the given role.

    This constructs the lightweight in-memory context object that
    require_role() operates on — no database, no network.
    """
    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()
    mock_user.role = role
    mock_user.is_active = True
    return CurrentUser(user=mock_user, merchant_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# ROLE_HIERARCHY structure tests (unit, deterministic — no Hypothesis needed)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_viewer_has_lowest_role_level() -> None:
    """VIEWER must be at level 0 — the floor of the hierarchy."""
    assert ROLE_HIERARCHY["VIEWER"] == 0


@pytest.mark.unit
def test_operator_outranks_viewer() -> None:
    """OPERATOR must have a strictly higher level than VIEWER."""
    assert ROLE_HIERARCHY["OPERATOR"] > ROLE_HIERARCHY["VIEWER"]


@pytest.mark.unit
def test_admin_outranks_operator() -> None:
    """ADMIN must have a strictly higher level than OPERATOR."""
    assert ROLE_HIERARCHY["ADMIN"] > ROLE_HIERARCHY["OPERATOR"]


@pytest.mark.unit
def test_system_not_in_hierarchy() -> None:
    """SYSTEM must not appear in ROLE_HIERARCHY.

    SYSTEM is an internal-only pseudo-role. It must never satisfy any
    require_role() guard. The sentinel value of -1 (returned by .get())
    ensures this at runtime, but the strongest guarantee is that the key
    is absent entirely.

    Requirements: SEC-AUTH-006
    """
    assert "SYSTEM" not in ROLE_HIERARCHY


@pytest.mark.unit
def test_role_hierarchy_contains_exactly_three_api_roles() -> None:
    """ROLE_HIERARCHY must contain exactly VIEWER, OPERATOR, and ADMIN."""
    assert set(ROLE_HIERARCHY.keys()) == {"VIEWER", "OPERATOR", "ADMIN"}


@pytest.mark.unit
def test_role_levels_are_strictly_ordered() -> None:
    """The three API roles must have distinct, strictly increasing levels."""
    levels = list(ROLE_HIERARCHY.values())
    assert len(levels) == len(set(levels)), "Role levels must be distinct"


# ---------------------------------------------------------------------------
# Property 11a: VIEWER rejected from OPERATOR+ endpoints
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@pytest.mark.asyncio
@given(st.sampled_from(["OPERATOR", "ADMIN"]))
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
async def test_p11_viewer_rejected_from_operator_and_admin_endpoints(
    minimum_role: str,
) -> None:
    """A VIEWER user must receive HTTP 403 when accessing OPERATOR or ADMIN endpoints.

    Feature: averiq, Property 11: RBAC Authorization Enforcement — 11a

    **Validates: Requirements SEC-AUTH-003**

    For every minimum_role in {OPERATOR, ADMIN}, a VIEWER CurrentUser is below
    the required level. require_role() must raise HTTPException(403) without
    performing any I/O.
    """
    viewer = _make_current_user("VIEWER")
    guard = require_role(minimum_role)

    with pytest.raises(HTTPException) as exc_info:
        await guard(current_user=viewer)

    assert exc_info.value.status_code == 403, (
        f"Expected 403 for VIEWER accessing {minimum_role!r} endpoint, "
        f"got {exc_info.value.status_code}"
    )


# ---------------------------------------------------------------------------
# Property 11b: OPERATOR rejected from ADMIN-only endpoints
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@pytest.mark.asyncio
@given(st.just("ADMIN"))
@settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
async def test_p11_operator_rejected_from_admin_endpoints(minimum_role: str) -> None:
    """An OPERATOR user must receive HTTP 403 when accessing ADMIN-only endpoints.

    Feature: averiq, Property 11: RBAC Authorization Enforcement — 11b

    **Validates: Requirements SEC-AUTH-004**

    OPERATOR is level 1; ADMIN requires level 2.  require_role("ADMIN") must
    raise HTTPException(403) for any OPERATOR-level user.
    """
    operator = _make_current_user("OPERATOR")
    guard = require_role(minimum_role)

    with pytest.raises(HTTPException) as exc_info:
        await guard(current_user=operator)

    assert exc_info.value.status_code == 403, (
        f"Expected 403 for OPERATOR accessing ADMIN-only endpoint, "
        f"got {exc_info.value.status_code}"
    )


# ---------------------------------------------------------------------------
# Property 11c: Correct roles ARE accepted (inverse / positive property)
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@pytest.mark.asyncio
@given(
    st.sampled_from(["VIEWER", "OPERATOR", "ADMIN"]),
    st.sampled_from(["VIEWER", "OPERATOR", "ADMIN"]),
)
@settings(max_examples=100)
async def test_p11_correct_role_accepted(user_role: str, minimum_role: str) -> None:
    """A user with sufficient role must NOT receive HTTP 403.

    Feature: averiq, Property 11: RBAC Authorization Enforcement — 11c

    **Validates: Requirements SEC-AUTH-003, SEC-AUTH-004**

    This is the positive case: when user_level >= required_level, the guard
    must return the CurrentUser unchanged.  When user_level < required_level,
    it must raise HTTP 403 — covering the same boundary as 11a/11b but from
    all (user_role, minimum_role) pairs simultaneously.
    """
    user = _make_current_user(user_role)
    guard = require_role(minimum_role)

    user_level = ROLE_HIERARCHY[user_role]
    required_level = ROLE_HIERARCHY[minimum_role]

    if user_level >= required_level:
        # Sufficient role: guard must succeed and return the same CurrentUser.
        result = await guard(current_user=user)
        assert result is user, (
            f"Guard returned {result!r} instead of the original CurrentUser "
            f"for user_role={user_role!r}, minimum_role={minimum_role!r}"
        )
    else:
        # Insufficient role: guard must raise 403.
        with pytest.raises(HTTPException) as exc_info:
            await guard(current_user=user)
        assert exc_info.value.status_code == 403, (
            f"Expected 403 for user_role={user_role!r} on {minimum_role!r} endpoint, "
            f"got {exc_info.value.status_code}"
        )


# ---------------------------------------------------------------------------
# SYSTEM role protection — SystemRoleGuard
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_system_role_guard_rejects_system() -> None:
    """SystemRoleGuard must reject role='SYSTEM' with ValidationError → HTTP 422.

    FastAPI converts a Pydantic ValidationError from a request body schema
    to HTTP 422 Unprocessable Entity.  This test validates the Pydantic layer.

    Requirements: SEC-AUTH-006
    """

    class TestSchema(SystemRoleGuard, BaseModel):
        role: str

    with pytest.raises(ValidationError) as exc_info:
        TestSchema(role="SYSTEM")

    errors = exc_info.value.errors()
    assert len(errors) >= 1, "Expected at least one validation error"
    assert any("SYSTEM" in str(e.get("msg", "")) for e in errors), (
        f"Expected error message to mention 'SYSTEM'. Errors: {errors}"
    )


@pytest.mark.pbt
@pytest.mark.unit
@given(st.sampled_from(["ADMIN", "OPERATOR", "VIEWER"]))
@settings(max_examples=50)
def test_system_role_guard_accepts_valid_roles(role: str) -> None:
    """SystemRoleGuard must accept all valid non-SYSTEM roles without error.

    Feature: averiq, Property 11: RBAC Authorization Enforcement — SYSTEM guard

    **Validates: Requirements SEC-AUTH-006**

    ADMIN, OPERATOR, and VIEWER are all legitimate API roles.
    The SystemRoleGuard mixin must permit them without raising a ValidationError.
    """

    class TestSchema(SystemRoleGuard, BaseModel):
        role: str

    schema = TestSchema(role=role)
    assert schema.role == role, (
        f"SystemRoleGuard mutated role value: expected {role!r}, got {schema.role!r}"
    )


@pytest.mark.unit
def test_system_role_guard_rejects_system_case_sensitive() -> None:
    """SystemRoleGuard rejection must be case-sensitive: 'SYSTEM' is blocked, 'system' is not.

    The requirement (SEC-AUTH-006) specifically targets the uppercase 'SYSTEM'
    sentinel value used internally.  Lowercase 'system' is a different string
    and should pass through without error (it would fail later at the role
    enum validation level, which is a separate concern).

    Requirements: SEC-AUTH-006
    """

    class TestSchema(SystemRoleGuard, BaseModel):
        role: str

    # Uppercase 'SYSTEM' must be rejected.
    with pytest.raises(ValidationError):
        TestSchema(role="SYSTEM")

    # Lowercase 'system' must NOT be blocked by SystemRoleGuard specifically.
    schema = TestSchema(role="system")
    assert schema.role == "system", (
        f"SystemRoleGuard should not block lowercase 'system'; got {schema.role!r}"
    )


@pytest.mark.unit
def test_system_role_guard_rejects_system_with_correct_field_location() -> None:
    """ValidationError from SystemRoleGuard must reference the 'role' field.

    This confirms that FastAPI will surface the error as a field-level 422
    detail for the 'role' field, not as a generic body error.

    Requirements: SEC-AUTH-006, API-006
    """

    class TestSchema(SystemRoleGuard, BaseModel):
        role: str

    with pytest.raises(ValidationError) as exc_info:
        TestSchema(role="SYSTEM")

    errors = exc_info.value.errors()
    # At least one error must reference the 'role' field in its location.
    locations = [e.get("loc", ()) for e in errors]
    assert any("role" in loc for loc in locations), (
        f"Expected error location to include 'role'. Locations: {locations}"
    )


# ---------------------------------------------------------------------------
# Boundary: VIEWER on VIEWER-only endpoint (should succeed)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_viewer_accepted_on_viewer_endpoint() -> None:
    """VIEWER must be accepted on endpoints that require only VIEWER access.

    Requirements: SEC-AUTH-003
    """
    viewer = _make_current_user("VIEWER")
    guard = require_role("VIEWER")

    result = await guard(current_user=viewer)
    assert result is viewer


@pytest.mark.unit
@pytest.mark.asyncio
async def test_admin_accepted_on_operator_endpoint() -> None:
    """ADMIN (higher role) must be accepted on OPERATOR-minimum endpoints.

    Verifies that higher roles satisfy lower-or-equal requirements — the
    role hierarchy is inclusive upwards.

    Requirements: SEC-AUTH-005
    """
    admin = _make_current_user("ADMIN")
    guard = require_role("OPERATOR")

    result = await guard(current_user=admin)
    assert result is admin


@pytest.mark.unit
@pytest.mark.asyncio
async def test_admin_accepted_on_admin_endpoint() -> None:
    """ADMIN must be accepted on ADMIN-only endpoints.

    Requirements: SEC-AUTH-005
    """
    admin = _make_current_user("ADMIN")
    guard = require_role("ADMIN")

    result = await guard(current_user=admin)
    assert result is admin


# ---------------------------------------------------------------------------
# Guard returns identity: same CurrentUser object is returned unchanged
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_guard_returns_same_current_user_object() -> None:
    """require_role() must return the exact same CurrentUser object on success.

    The guard is a pass-through for authorized users; it must not create a
    new CurrentUser or modify the existing one.
    """
    user = _make_current_user("ADMIN")
    guard = require_role("ADMIN")

    result = await guard(current_user=user)
    assert result is user


# ---------------------------------------------------------------------------
# 403 detail message is meaningful (not empty)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_403_detail_is_non_empty_string() -> None:
    """The HTTP 403 raised by require_role must include a non-empty detail string.

    An empty or None detail would produce an unhelpful client error message
    and lose information about which role was required.
    """
    viewer = _make_current_user("VIEWER")
    guard = require_role("ADMIN")

    with pytest.raises(HTTPException) as exc_info:
        await guard(current_user=viewer)

    detail = exc_info.value.detail
    assert detail, "HTTPException detail must be non-empty"
    assert isinstance(detail, str), f"HTTPException detail must be a string, got {type(detail)}"
