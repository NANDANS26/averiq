"""Shared Pydantic validators for API request schemas.

SYSTEM role protection (SEC-AUTH-006):
  Any API request payload that includes a 'role' field must use the
  SystemRoleGuard mixin to ensure SYSTEM cannot be assigned via the API.

  The SYSTEM pseudo-role is reserved exclusively for internal service-to-service
  calls. It must never appear in user-facing API payloads (user creation, user
  update, admin assignment, or any other schema).

Usage::

    from backend.api.validators import SystemRoleGuard
    from pydantic import BaseModel

    class CreateUserRequest(SystemRoleGuard, BaseModel):
        email: str
        role: str

    class UpdateUserRequest(SystemRoleGuard, BaseModel):
        role: str | None = None

    # Any payload with role='SYSTEM' now automatically raises a 422 Pydantic
    # ValidationError before it reaches endpoint logic.

Requirements: SEC-AUTH-006
"""

from __future__ import annotations

from pydantic import field_validator


class SystemRoleGuard:
    """Pydantic model mixin that rejects 'SYSTEM' as a role value.

    Mix this class into any Pydantic ``BaseModel`` that includes a ``role``
    field (user creation, user update, admin endpoints, etc.) to enforce the
    SEC-AUTH-006 requirement:

      > The SYSTEM role SHALL NOT be assignable to human users via any API
      > or administrative interface.

    The validator runs in ``mode='before'`` so it fires on the raw input value
    before any type coercion, ensuring SYSTEM is blocked even if the field
    type is an enum or a constrained string.

    FastAPI automatically translates a ``ValueError`` raised by a Pydantic
    validator into an HTTP 422 Unprocessable Entity response with a
    field-level error detail (API-006).

    Example error response::

        {
            "error": "VALIDATION_ERROR",
            "detail": [
                {
                    "type": "value_error",
                    "loc": ["body", "role"],
                    "msg": "Value error, SYSTEM role cannot be assigned via the API (SEC-AUTH-006)",
                    "input": "SYSTEM"
                }
            ],
            "request_id": "..."
        }
    """

    @field_validator("role", mode="before", check_fields=False)
    @classmethod
    def reject_system_role(cls, v: object) -> object:
        """Reject any payload that submits role='SYSTEM'.

        Args:
            v: The raw value of the ``role`` field before type coercion.

        Returns:
            The original value, unchanged, if it is not 'SYSTEM'.

        Raises:
            ValueError: If the value is the string 'SYSTEM' (case-sensitive).
        """
        if v == "SYSTEM":
            raise ValueError(
                "SYSTEM role cannot be assigned via the API (SEC-AUTH-006). "
                "The SYSTEM role is reserved for internal service-to-service "
                "operations only and must never be assigned to a human user."
            )
        return v
