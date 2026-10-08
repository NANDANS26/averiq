"""Policy lifecycle management.

Lifecycle states: draft → validated → active → retired

Transitions:
  draft      → validated  (validation passes all rule-set checks)
  validated  → active     (activates; retires previous active policy atomically)
  active     → retired    (manual retirement or superseded by new activation)
  draft      → retired    (discard without validating)

Validation rules (draft → validated):
  - min_confidence in [0.0, 1.0]
  - cooldown_seconds >= 0
  - human_approval_threshold >= 0 (integer paise)
  - attribution_window_seconds >= 0
  - max_amount values >= 0 (integer paise)
  - max_retries values >= 0 (integer counts)
  All constraint violations produce a structured list of errors.

At-most-one-active invariant:
  The DB enforces this via partial unique index idx_policies_active.
  The application layer also checks before activation to provide a clear error.

Audit atomicity:
  Every lifecycle transition is recorded in an AuditEvent in the same DB
  transaction as the policy state change.

Requirements: FR-POLICY-012, FR-POLICY-013, FR-POLICY-010
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.models import AuditEvent, Policy
from shared.contracts.schemas import PolicyConfig

logger = structlog.get_logger(__name__)

# Valid lifecycle transitions
ALLOWED_POLICY_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft":     frozenset({"validated", "retired"}),
    "validated": frozenset({"active", "retired"}),
    "active":    frozenset({"retired"}),
    "retired":   frozenset(),  # terminal
}


@dataclass(frozen=True)
class ValidationError:
    field: str
    message: str


@dataclass(frozen=True)
class PolicyValidationResult:
    is_valid: bool
    errors: list[ValidationError] = field(default_factory=list)


class PolicyLifecycleError(Exception):
    def __init__(self, message: str, errors: list[ValidationError] | None = None):
        super().__init__(message)
        self.errors = errors or []


class PolicyLifecycleManager:
    """Manages policy lifecycle state transitions with validation and audit.

    Requirements: FR-POLICY-010, FR-POLICY-012, FR-POLICY-013
    """

    async def validate_policy(self, policy: Policy) -> PolicyValidationResult:
        """Validate a draft policy's configuration before transitioning to 'validated'.

        Checks all rule thresholds for correctness per FR-POLICY-013:
          - min_confidence in [0.0, 1.0]
          - cooldown_seconds: non-negative integer
          - human_approval_threshold: non-negative integer (paise)
          - attribution_window_seconds: non-negative integer
          - max_amount[currency]: non-negative integers (paise)
          - max_retries[intervention_type]: non-negative integers
        """
        errors: list[ValidationError] = []
        cfg = policy.configuration or {}

        # Validate min_confidence in [0.0, 1.0]
        min_confidence = cfg.get("min_confidence", 0.5)
        if not (isinstance(min_confidence, (int, float)) and 0.0 <= min_confidence <= 1.0):
            errors.append(ValidationError("min_confidence", "Must be a float in [0.0, 1.0]"))

        # Validate cooldown_seconds: non-negative integer
        cooldown_seconds = cfg.get("cooldown_seconds", 0)
        if not (isinstance(cooldown_seconds, int) and cooldown_seconds >= 0):
            errors.append(ValidationError("cooldown_seconds", "Must be a non-negative integer"))

        # Validate human_approval_threshold: non-negative integer (paise)
        human_approval_threshold = cfg.get("human_approval_threshold", 0)
        if not (isinstance(human_approval_threshold, int) and human_approval_threshold >= 0):
            errors.append(
                ValidationError(
                    "human_approval_threshold",
                    "Must be a non-negative integer (paise)",
                )
            )

        # Validate attribution_window_seconds: non-negative integer
        attribution_window = cfg.get("attribution_window_seconds", 86400)
        if not (isinstance(attribution_window, int) and attribution_window >= 0):
            errors.append(
                ValidationError(
                    "attribution_window_seconds",
                    "Must be a non-negative integer",
                )
            )

        # Validate max_amount values: non-negative integers (paise)
        max_amount = cfg.get("max_amount", {})
        for currency, amount in max_amount.items():
            if not (isinstance(amount, int) and amount >= 0):
                errors.append(
                    ValidationError(
                        f"max_amount.{currency}",
                        "Must be a non-negative integer (paise)",
                    )
                )

        # Validate max_retries values: non-negative integers
        max_retries = cfg.get("max_retries", {})
        for intervention_type, count in max_retries.items():
            if not (isinstance(count, int) and count >= 0):
                errors.append(
                    ValidationError(
                        f"max_retries.{intervention_type}",
                        "Must be a non-negative integer",
                    )
                )

        return PolicyValidationResult(is_valid=len(errors) == 0, errors=errors)

    async def transition(
        self,
        session: AsyncSession,
        policy: Policy,
        to_state: str,
        actor: str,
        reason: str,
        correlation_id: str,
    ) -> Policy:
        """Perform a lifecycle transition with validation and audit atomicity.

        Records the transition in an AuditEvent in the same DB transaction as
        the policy state change (FR-POLICY-010 — policy_version on every event).

        Raises:
            PolicyLifecycleError: if the transition is invalid or validation fails.
        """
        from_state = policy.lifecycle_state

        if to_state not in ALLOWED_POLICY_TRANSITIONS.get(from_state, frozenset()):
            raise PolicyLifecycleError(
                f"Invalid policy transition: {from_state} → {to_state}"
            )

        # Validate when moving to 'validated' (FR-POLICY-013)
        if to_state == "validated":
            result = await self.validate_policy(policy)
            if not result.is_valid:
                raise PolicyLifecycleError(
                    f"Policy validation failed: {[e.message for e in result.errors]}",
                    errors=result.errors,
                )

        # Enforce at-most-one-active invariant (FR-POLICY-012)
        if to_state == "active":
            existing_active = await self._get_active_policy(session, policy.merchant_id)
            if existing_active and existing_active.id != policy.id:
                # Retire the previous active policy in the same transaction
                await self._retire_policy(session, existing_active, actor, correlation_id)

        # Apply transition
        now = datetime.now(timezone.utc)
        policy.lifecycle_state = to_state
        if to_state == "active":
            policy.activated_at = now
        elif to_state == "retired":
            policy.retired_at = now

        # Audit event (same transaction — atomicity invariant, FR-POLICY-010)
        try:
            corr_uuid = uuid.UUID(correlation_id)
        except (ValueError, AttributeError):
            corr_uuid = uuid.uuid4()

        audit = AuditEvent(
            id=uuid.uuid4(),
            merchant_id=policy.merchant_id,
            case_id=None,
            actor=actor,
            action_type="policy_lifecycle_transition",
            affected_entity_type="Policy",
            affected_entity_id=policy.id,
            from_state=from_state,
            to_state=to_state,
            policy_version=str(policy.version),
            correlation_id=corr_uuid,
            payload={"reason": reason, "from_state": from_state, "to_state": to_state},
            created_at=now,
        )
        session.add(audit)
        await session.flush()

        logger.info(
            "policy_lifecycle_transition",
            policy_id=str(policy.id),
            merchant_id=str(policy.merchant_id),
            from_state=from_state,
            to_state=to_state,
            actor=actor,
            policy_version=str(policy.version),
        )

        return policy

    async def _get_active_policy(
        self, session: AsyncSession, merchant_id: uuid.UUID
    ) -> Policy | None:
        """Return the currently active policy for a merchant, or None."""
        stmt = (
            select(Policy)
            .where(
                Policy.merchant_id == merchant_id,
                Policy.lifecycle_state == "active",
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _retire_policy(
        self,
        session: AsyncSession,
        policy: Policy,
        actor: str,
        correlation_id: str,
    ) -> None:
        """Retire a previously active policy (superseded by a new activation).

        Records an AuditEvent for the retirement in the same transaction.
        """
        now = datetime.now(timezone.utc)
        policy.lifecycle_state = "retired"
        policy.retired_at = now

        try:
            corr_uuid = uuid.UUID(correlation_id)
        except (ValueError, AttributeError):
            corr_uuid = uuid.uuid4()

        audit = AuditEvent(
            id=uuid.uuid4(),
            merchant_id=policy.merchant_id,
            case_id=None,
            actor=actor,
            action_type="policy_lifecycle_transition",
            affected_entity_type="Policy",
            affected_entity_id=policy.id,
            from_state="active",
            to_state="retired",
            policy_version=str(policy.version),
            correlation_id=corr_uuid,
            payload={"reason": "superseded by new active policy"},
            created_at=now,
        )
        session.add(audit)

        logger.info(
            "policy_retired_superseded",
            policy_id=str(policy.id),
            merchant_id=str(policy.merchant_id),
            actor=actor,
            policy_version=str(policy.version),
        )
