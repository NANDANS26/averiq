"""Unit tests for policy lifecycle management.

Tests:
1. draft→validated succeeds with valid config
2. draft→validated fails with min_confidence=-0.1
3. draft→validated fails with negative cooldown_seconds
4. draft→validated fails with non-integer human_approval_threshold
5. validated→active retires previous active policy
6. retired→active raises PolicyLifecycleError (invalid transition)
7. Transition audit event is recorded in same flush call
8. At-most-one-active: second activation retires previous one

Requirements: FR-POLICY-010, FR-POLICY-012, FR-POLICY-013
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from backend.policy_engine.lifecycle import (
    ALLOWED_POLICY_TRANSITIONS,
    PolicyLifecycleError,
    PolicyLifecycleManager,
    PolicyValidationResult,
    ValidationError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy(
    lifecycle_state: str = "draft",
    configuration: dict | None = None,
    merchant_id: uuid.UUID | None = None,
    policy_id: uuid.UUID | None = None,
    version: int = 1,
) -> MagicMock:
    """Return a lightweight mock that looks like a ``Policy`` ORM object."""
    policy = MagicMock()
    policy.id = policy_id or uuid.uuid4()
    policy.merchant_id = merchant_id or uuid.uuid4()
    policy.version = version
    policy.lifecycle_state = lifecycle_state
    policy.configuration = configuration if configuration is not None else {
        "min_confidence": 0.7,
        "cooldown_seconds": 300,
        "human_approval_threshold": 50000,
        "attribution_window_seconds": 86400,
        "max_amount": {"INR": 500000},
        "max_retries": {"RETRY": 3, "PAYMENT_LINK": 2},
    }
    policy.activated_at = None
    policy.retired_at = None
    return policy


def _make_session() -> AsyncMock:
    """Return a mock AsyncSession with the minimal surface used by lifecycle.py."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    # execute() returns a result whose scalar_one_or_none() returns None by default
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=mock_result)
    return session


def _correlation_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# 1. draft→validated succeeds with valid config
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_draft_to_validated_succeeds_with_valid_config() -> None:
    """A policy with all valid configuration fields transitions draft→validated.

    Requirements: FR-POLICY-013
    """
    manager = PolicyLifecycleManager()
    policy = _make_policy(lifecycle_state="draft")
    session = _make_session()

    result = await manager.transition(
        session=session,
        policy=policy,
        to_state="validated",
        actor="admin-user",
        reason="configuration looks good",
        correlation_id=_correlation_id(),
    )

    assert result.lifecycle_state == "validated"
    # audit event added in same flush
    session.add.assert_called_once()
    session.flush.assert_awaited_once()


# ---------------------------------------------------------------------------
# 2. draft→validated fails with min_confidence=-0.1
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_draft_to_validated_fails_with_negative_min_confidence() -> None:
    """min_confidence=-0.1 is outside [0.0, 1.0]; transition must be rejected.

    Requirements: FR-POLICY-013
    """
    manager = PolicyLifecycleManager()
    cfg = {
        "min_confidence": -0.1,  # invalid: below 0.0
        "cooldown_seconds": 300,
        "human_approval_threshold": 50000,
        "attribution_window_seconds": 86400,
    }
    policy = _make_policy(lifecycle_state="draft", configuration=cfg)
    session = _make_session()

    with pytest.raises(PolicyLifecycleError) as exc_info:
        await manager.transition(
            session=session,
            policy=policy,
            to_state="validated",
            actor="admin-user",
            reason="test",
            correlation_id=_correlation_id(),
        )

    exc = exc_info.value
    assert exc.errors, "Expected at least one ValidationError"
    fields_with_errors = [e.field for e in exc.errors]
    assert "min_confidence" in fields_with_errors

    # No state change, no audit event
    assert policy.lifecycle_state == "draft"
    session.add.assert_not_called()


# ---------------------------------------------------------------------------
# 3. draft→validated fails with negative cooldown_seconds
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_draft_to_validated_fails_with_negative_cooldown() -> None:
    """cooldown_seconds=-1 is invalid; transition must be rejected.

    Requirements: FR-POLICY-013
    """
    manager = PolicyLifecycleManager()
    cfg = {
        "min_confidence": 0.7,
        "cooldown_seconds": -1,  # invalid: negative
        "human_approval_threshold": 50000,
        "attribution_window_seconds": 86400,
    }
    policy = _make_policy(lifecycle_state="draft", configuration=cfg)
    session = _make_session()

    with pytest.raises(PolicyLifecycleError) as exc_info:
        await manager.transition(
            session=session,
            policy=policy,
            to_state="validated",
            actor="admin-user",
            reason="test",
            correlation_id=_correlation_id(),
        )

    exc = exc_info.value
    assert exc.errors, "Expected at least one ValidationError"
    fields_with_errors = [e.field for e in exc.errors]
    assert "cooldown_seconds" in fields_with_errors

    assert policy.lifecycle_state == "draft"
    session.add.assert_not_called()


# ---------------------------------------------------------------------------
# 4. draft→validated fails with non-integer human_approval_threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_draft_to_validated_fails_with_float_human_approval_threshold() -> None:
    """human_approval_threshold=500.50 (float) is invalid; must be an integer (paise).

    Requirements: FR-POLICY-013
    """
    manager = PolicyLifecycleManager()
    cfg = {
        "min_confidence": 0.7,
        "cooldown_seconds": 300,
        "human_approval_threshold": 500.50,  # invalid: float, must be int paise
        "attribution_window_seconds": 86400,
    }
    policy = _make_policy(lifecycle_state="draft", configuration=cfg)
    session = _make_session()

    with pytest.raises(PolicyLifecycleError) as exc_info:
        await manager.transition(
            session=session,
            policy=policy,
            to_state="validated",
            actor="admin-user",
            reason="test",
            correlation_id=_correlation_id(),
        )

    exc = exc_info.value
    assert exc.errors, "Expected at least one ValidationError"
    fields_with_errors = [e.field for e in exc.errors]
    assert "human_approval_threshold" in fields_with_errors

    assert policy.lifecycle_state == "draft"
    session.add.assert_not_called()


# ---------------------------------------------------------------------------
# 5. validated→active retires previous active policy
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_validated_to_active_retires_previous_active_policy() -> None:
    """When activating a policy, any existing active policy must be retired first.

    The retirement must happen in the same DB transaction (same session).

    Requirements: FR-POLICY-012
    """
    manager = PolicyLifecycleManager()
    merchant_id = uuid.uuid4()

    existing_active = _make_policy(
        lifecycle_state="active",
        merchant_id=merchant_id,
        version=1,
    )
    new_policy = _make_policy(
        lifecycle_state="validated",
        merchant_id=merchant_id,
        version=2,
    )

    session = _make_session()
    # Make session.execute() return the existing active policy
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_active
    session.execute = AsyncMock(return_value=mock_result)

    await manager.transition(
        session=session,
        policy=new_policy,
        to_state="active",
        actor="admin-user",
        reason="new policy activated",
        correlation_id=_correlation_id(),
    )

    # The new policy should be active
    assert new_policy.lifecycle_state == "active"
    assert new_policy.activated_at is not None

    # The previous active policy should be retired
    assert existing_active.lifecycle_state == "retired"
    assert existing_active.retired_at is not None

    # Two AuditEvents: one for the retirement of old, one for new activation
    assert session.add.call_count == 2
    session.flush.assert_awaited_once()


# ---------------------------------------------------------------------------
# 6. retired→active raises PolicyLifecycleError (invalid transition)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retired_to_active_raises_lifecycle_error() -> None:
    """retired is a terminal state; transitioning to active must be rejected.

    Requirements: FR-POLICY-012
    """
    manager = PolicyLifecycleManager()
    policy = _make_policy(lifecycle_state="retired")
    session = _make_session()

    with pytest.raises(PolicyLifecycleError) as exc_info:
        await manager.transition(
            session=session,
            policy=policy,
            to_state="active",
            actor="admin-user",
            reason="re-activate",
            correlation_id=_correlation_id(),
        )

    assert "retired" in str(exc_info.value).lower() or "active" in str(exc_info.value).lower()

    # No state change, no audit event
    assert policy.lifecycle_state == "retired"
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


# ---------------------------------------------------------------------------
# 7. Transition audit event is recorded in same flush call
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_audit_event_recorded_in_same_flush() -> None:
    """An AuditEvent must be added to the session before flush is called.

    This verifies that the audit event and the state change are atomic:
    session.add() is called before session.flush().

    Requirements: FR-POLICY-010, FR-STATE-016
    """
    manager = PolicyLifecycleManager()
    policy = _make_policy(lifecycle_state="draft")
    session = _make_session()

    call_order: list[str] = []
    added_objects: list[object] = []

    def tracking_add(obj: object) -> None:
        call_order.append("add")
        added_objects.append(obj)

    async def tracking_flush() -> None:
        call_order.append("flush")

    session.add = tracking_add
    session.flush = AsyncMock(side_effect=tracking_flush)

    await manager.transition(
        session=session,
        policy=policy,
        to_state="validated",
        actor="SYSTEM",
        reason="auto-validate",
        correlation_id=_correlation_id(),
    )

    # add() must come before flush()
    assert "add" in call_order
    assert "flush" in call_order
    add_idx = call_order.index("add")
    flush_idx = call_order.index("flush")
    assert add_idx < flush_idx, (
        "session.add(audit) must be called before session.flush()"
    )

    # The added object is an AuditEvent with the correct policy_version
    assert len(added_objects) == 1
    added_audit = added_objects[0]
    assert str(policy.version) == added_audit.policy_version


# ---------------------------------------------------------------------------
# 8. At-most-one-active: second activation retires previous one
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_at_most_one_active_policy_per_merchant() -> None:
    """Activating a second policy for the same merchant retires the first one.

    The at-most-one-active invariant must hold: after the second activation,
    the first policy is retired and only one policy is active.

    Requirements: FR-POLICY-012
    """
    manager = PolicyLifecycleManager()
    merchant_id = uuid.uuid4()

    first_policy = _make_policy(
        lifecycle_state="active",
        merchant_id=merchant_id,
        version=1,
    )
    second_policy = _make_policy(
        lifecycle_state="validated",
        merchant_id=merchant_id,
        version=2,
    )

    session = _make_session()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = first_policy
    session.execute = AsyncMock(return_value=mock_result)

    # Activate the second policy
    await manager.transition(
        session=session,
        policy=second_policy,
        to_state="active",
        actor="SYSTEM",
        reason="version 2 going live",
        correlation_id=_correlation_id(),
    )

    # Only second policy is active
    assert second_policy.lifecycle_state == "active"
    assert second_policy.activated_at is not None

    # First policy is now retired — the invariant holds
    assert first_policy.lifecycle_state == "retired"
    assert first_policy.retired_at is not None

    # Both retirement audit and activation audit were added
    assert session.add.call_count == 2
