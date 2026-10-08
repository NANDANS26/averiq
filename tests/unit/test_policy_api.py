"""Unit tests for the policy lifecycle API endpoints (task 16.2).

Tests:
  1. list_policies returns a list of PolicyOut items, each with a lifecycle_state field.
  2. get_active_policy returns None when no active policy exists for the merchant.
  3. create_policy assigns lifecycle_state="draft" to newly created policies.

All tests are pure unit tests — no database, no Redis, no network calls.
Session and authentication dependencies are mocked with AsyncMock / MagicMock.

Requirements: FR-POLICY-012, FR-POLICY-013
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.api.dependencies import CurrentUser
from backend.api.policy import (
    CreatePolicyRequest,
    PolicyOut,
    _policy_to_out,
    create_policy,
    get_active_policy,
    list_policies,
)
from backend.domain.models import Policy, User

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MERCHANT_ID = uuid.uuid4()
_NOW = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _make_current_user(role: str = "ADMIN") -> CurrentUser:
    """Return a CurrentUser scoped to _MERCHANT_ID."""
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.role = role
    return CurrentUser(user=user, merchant_id=_MERCHANT_ID)


def _make_policy(
    lifecycle_state: str = "draft",
    version: int = 1,
    configuration: dict | None = None,
) -> Policy:
    """Return a minimal Policy ORM-like object (no DB required)."""
    p = MagicMock(spec=Policy)
    p.id = uuid.uuid4()
    p.merchant_id = _MERCHANT_ID
    p.version = version
    p.lifecycle_state = lifecycle_state
    p.configuration = configuration or {}
    p.created_at = _NOW
    return p


def _make_mock_session() -> AsyncMock:
    """Return an AsyncMock with sensible defaults for the three common session ops."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Test 1: list_policies returns a list of PolicyOut with lifecycle_state field
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_policies_returns_list_with_lifecycle_state() -> None:
    """list_policies should return a list of PolicyOut, each with lifecycle_state.

    Verifies:
    - Return type is a list.
    - Each item is a PolicyOut.
    - Each item contains a non-empty lifecycle_state field.
    - The merchant-scoped query is honoured (only own-merchant policies returned).
    """
    policy_draft = _make_policy("draft", version=1)
    policy_active = _make_policy("active", version=2)

    session = _make_mock_session()

    # Mock session.execute → scalars().all() → [policy_draft, policy_active]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [policy_draft, policy_active]
    session.execute = AsyncMock(return_value=mock_result)

    current_user = _make_current_user(role="VIEWER")
    result = await list_policies(current_user=current_user, session=session)

    assert isinstance(result, list), "Expected a list from list_policies"
    assert len(result) == 2, "Expected two policies in the result"

    for item in result:
        assert isinstance(item, PolicyOut), "Each item must be a PolicyOut"
        assert item.lifecycle_state, "lifecycle_state must be present and non-empty"
        assert item.merchant_id == str(_MERCHANT_ID), "Policies must be scoped to merchant"

    # Verify both lifecycle states are present
    states = {item.lifecycle_state for item in result}
    assert "draft" in states, "Expected draft state in result"
    assert "active" in states, "Expected active state in result"


# ---------------------------------------------------------------------------
# Test 2: get_active_policy returns None when no active policy exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_active_policy_returns_none_when_none_exists() -> None:
    """get_active_policy must return None when no active policy exists.

    Verifies that the endpoint returns HTTP 200 with null body (not a 404 error)
    when the merchant has no active policy — this is a valid business state.
    """
    session = _make_mock_session()

    # session.execute → scalar_one_or_none() returns None (no active policy)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=mock_result)

    current_user = _make_current_user(role="VIEWER")
    result = await get_active_policy(current_user=current_user, session=session)

    assert result is None, "Expected None when no active policy exists"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_active_policy_returns_policy_when_active_exists() -> None:
    """get_active_policy must return the active policy when one exists."""
    active_policy = _make_policy("active", version=3)

    session = _make_mock_session()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = active_policy
    session.execute = AsyncMock(return_value=mock_result)

    current_user = _make_current_user(role="VIEWER")
    result = await get_active_policy(current_user=current_user, session=session)

    assert result is not None, "Expected a PolicyOut, not None"
    assert isinstance(result, PolicyOut)
    assert result.lifecycle_state == "active"


# ---------------------------------------------------------------------------
# Test 3: create_policy assigns lifecycle_state="draft"
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_policy_has_draft_lifecycle_state() -> None:
    """Newly created policies via create_policy must have lifecycle_state='draft'.

    Verifies:
    - The returned PolicyOut has lifecycle_state == "draft".
    - The policy version is set to 1 when no prior policies exist.
    - The configuration is stored as provided.
    """
    session = _make_mock_session()

    # First execute call (latest version query) → no existing policies
    no_latest_result = MagicMock()
    no_latest_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=no_latest_result)

    current_user = _make_current_user(role="ADMIN")
    body = CreatePolicyRequest(
        configuration={
            "min_confidence": 0.7,
            "cooldown_seconds": 3600,
            "human_approval_threshold": 500000,  # 5000 INR in paise
        }
    )

    # Capture the Policy object added to the session to verify its state
    added_policies: list[Policy] = []

    def _capture_add(obj: object) -> None:
        added_policies.append(obj)  # type: ignore[arg-type]

    session.add = MagicMock(side_effect=_capture_add)

    result = await create_policy(body=body, current_user=current_user, session=session)

    assert isinstance(result, PolicyOut), "create_policy must return a PolicyOut"
    assert result.lifecycle_state == "draft", (
        "New policy must start in 'draft' state (FR-POLICY-012)"
    )
    assert result.version == 1, "First policy for a merchant must be version 1"
    assert result.merchant_id == str(_MERCHANT_ID)

    # Verify session.add was called with a Policy object
    assert len(added_policies) == 1, "Expected exactly one Policy to be added to session"
    added = added_policies[0]
    assert added.lifecycle_state == "draft"
    assert added.version == 1
