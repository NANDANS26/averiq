"""Policy lifecycle integration tests (mocked).

Tests PolicyLifecycleManager transitions: draft → validated → active → retired,
including validation failure paths, at-most-one-active enforcement, and
audit atomicity guarantees.

Requirements: FR-POLICY-012, FR-POLICY-013
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.policy_engine.lifecycle import PolicyLifecycleError, PolicyLifecycleManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy(state: str = "draft", version: int = 1) -> MagicMock:
    """Return a minimal Policy-like mock in the given lifecycle state."""
    p = MagicMock()
    p.id = uuid.uuid4()
    p.merchant_id = uuid.uuid4()
    p.version = version
    p.lifecycle_state = state
    p.activated_at = None
    p.retired_at = None
    p.configuration = {
        "min_confidence": 0.7,
        "cooldown_seconds": 300,
        "human_approval_threshold": 50000,
        "attribution_window_seconds": 86400,
        "max_amount": {"INR": 1000000},
        "max_retries": {"RETRY": 3},
    }
    return p


def _make_session(prev_active: MagicMock | None = None) -> AsyncMock:
    """Return a minimal AsyncSession mock.

    If *prev_active* is provided it is returned by the SELECT that looks for
    the currently active policy (used when testing at-most-one-active logic).
    """
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = prev_active
    session.execute = AsyncMock(return_value=mock_result)

    return session


manager = PolicyLifecycleManager()


# ---------------------------------------------------------------------------
# Test 1: draft → validated succeeds with valid configuration
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_draft_to_validated_succeeds() -> None:
    """draft → validated succeeds when the policy configuration is valid.

    Requirements: FR-POLICY-013
    """
    policy = _make_policy("draft")
    session = _make_session()

    result = await manager.transition(
        session, policy, "validated", "admin", "test transition", str(uuid.uuid4())
    )

    assert result.lifecycle_state == "validated"
    # AuditEvent must have been added
    session.add.assert_called()
    session.flush.assert_awaited()


# ---------------------------------------------------------------------------
# Test 2: draft → validated fails when configuration is invalid
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_draft_to_validated_fails_invalid_config() -> None:
    """draft → validated raises PolicyLifecycleError when min_confidence is invalid.

    min_confidence must be in [0.0, 1.0]; a negative value fails validation.

    Requirements: FR-POLICY-013
    """
    policy = _make_policy("draft")
    policy.configuration = {"min_confidence": -0.5}
    session = _make_session()

    with pytest.raises(PolicyLifecycleError) as exc_info:
        await manager.transition(
            session, policy, "validated", "admin", "bad config", str(uuid.uuid4())
        )

    err = exc_info.value
    assert len(err.errors) >= 1, "PolicyLifecycleError must carry a list of validation errors"


# ---------------------------------------------------------------------------
# Test 3: validated → active retires the previously active policy
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_validated_to_active_retires_previous() -> None:
    """validated → active retires any existing active policy in the same transaction.

    At-most-one-active invariant: activating a new policy must retire the
    previous one atomically (FR-POLICY-012).

    Requirements: FR-POLICY-012
    """
    previous = _make_policy("active", version=1)
    new_policy = _make_policy("validated", version=2)
    new_policy.merchant_id = previous.merchant_id  # same merchant

    session = _make_session(prev_active=previous)

    await manager.transition(
        session, new_policy, "active", "admin", "new policy activation", str(uuid.uuid4())
    )

    assert new_policy.lifecycle_state == "active"
    assert previous.lifecycle_state == "retired", (
        "Previously active policy must be retired when a new one is activated"
    )
    assert previous.retired_at is not None, "retired_at must be set on the retired policy"


# ---------------------------------------------------------------------------
# Test 4: retired → active raises PolicyLifecycleError
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retired_to_active_raises() -> None:
    """retired → active is an invalid transition and must raise PolicyLifecycleError.

    A retired policy can never be reactivated; a new version must be created.

    Requirements: FR-POLICY-012
    """
    policy = _make_policy("retired")
    session = _make_session()

    with pytest.raises(PolicyLifecycleError):
        await manager.transition(
            session, policy, "active", "admin", "attempt to reactivate", str(uuid.uuid4())
        )


# ---------------------------------------------------------------------------
# Test 5: Audit event is added before flush (atomicity guarantee)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_audit_event_added_before_flush() -> None:
    """session.add(AuditEvent) must occur before session.flush() on every transition.

    This ensures the policy state change and its audit record are always
    committed in the same DB transaction.

    Requirements: FR-POLICY-010
    """
    policy = _make_policy("draft")
    session = _make_session()

    call_order: list[str] = []
    session.add = MagicMock(side_effect=lambda _: call_order.append("add"))
    session.flush = AsyncMock(side_effect=lambda: call_order.append("flush"))

    await manager.transition(
        session, policy, "validated", "admin", "atomicity test", str(uuid.uuid4())
    )

    assert "add" in call_order, "session.add must be called (for the AuditEvent)"
    assert "flush" in call_order, "session.flush must be called"
    assert call_order.index("add") < call_order.index("flush"), (
        "session.add must precede session.flush to guarantee atomicity"
    )


# ---------------------------------------------------------------------------
# Test 6: active → retired succeeds (manual retirement)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_active_to_retired_succeeds() -> None:
    """active → retired succeeds and sets retired_at.

    Requirements: FR-POLICY-012
    """
    policy = _make_policy("active")
    session = _make_session()

    result = await manager.transition(
        session, policy, "retired", "admin", "manual retirement", str(uuid.uuid4())
    )

    assert result.lifecycle_state == "retired"
    assert result.retired_at is not None, "retired_at must be set when policy is retired"


# ---------------------------------------------------------------------------
# Test 7: draft → retired shortcut bypasses validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_draft_to_retired_bypasses_validation() -> None:
    """draft → retired must succeed even when the policy configuration is invalid.

    Discarding a draft policy should not require it to pass validation.

    Requirements: FR-POLICY-012
    """
    policy = _make_policy("draft")
    policy.configuration = {}  # empty / invalid configuration
    session = _make_session()

    result = await manager.transition(
        session, policy, "retired", "admin", "discard draft", str(uuid.uuid4())
    )

    assert result.lifecycle_state == "retired"


# ---------------------------------------------------------------------------
# Test 8: validated → retired is allowed
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_validated_to_retired_is_allowed() -> None:
    """validated → retired is a valid transition.

    A validated policy can be retired without ever being activated.

    Requirements: FR-POLICY-012
    """
    policy = _make_policy("validated")
    session = _make_session()

    result = await manager.transition(
        session, policy, "retired", "admin", "retire before activation", str(uuid.uuid4())
    )

    assert result.lifecycle_state == "retired"
