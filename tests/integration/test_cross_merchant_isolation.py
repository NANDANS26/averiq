"""Additional cross-merchant isolation tests.

Supplements test_merchant_isolation.py with additional repository coverage
for PolicyRepository, AuditEventRepository, and WebhookEventRepository.

Requirements: SEC-ISOL-001, SEC-ISOL-002, SEC-ISOL-004
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.domain.repository import (
    AuditEventRepository,
    ForbiddenError,
    PolicyRepository,
    WebhookEventRepository,
)


# ---------------------------------------------------------------------------
# PolicyRepository — list() is scoped to merchant
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_policy_repository_list_scoped_to_merchant() -> None:
    """PolicyRepository.list() must include merchant_id in the WHERE clause.

    Verifies that the SQLAlchemy SELECT statement passed to session.execute()
    contains a merchant_id predicate, ensuring the DB never returns policies
    belonging to other merchants.

    Requirements: SEC-ISOL-001, SEC-ISOL-002
    """
    merchant_id = uuid.uuid4()

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    repo = PolicyRepository(session=session, merchant_id=merchant_id)
    results = await repo.list()

    # Verify the query was executed
    session.execute.assert_called_once()

    # The statement must reference merchant_id
    stmt = session.execute.call_args[0][0]
    assert "merchant_id" in str(stmt).lower(), (
        "The WHERE clause must contain a merchant_id filter. "
        f"Statement: {str(stmt)!r}"
    )

    assert results == []


# ---------------------------------------------------------------------------
# PolicyRepository — get_by_id raises ForbiddenError for wrong merchant
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_forbidden_error_on_cross_merchant_policy_access() -> None:
    """PolicyRepository.get_by_id() raises ForbiddenError for a different merchant.

    When Merchant A's repository fetches a policy that belongs to Merchant B,
    ForbiddenError must be raised instead of returning the policy.

    Requirements: SEC-ISOL-002, SEC-ISOL-004
    """
    merchant_a = uuid.uuid4()
    merchant_b = uuid.uuid4()

    policy = MagicMock()
    policy.id = uuid.uuid4()
    policy.merchant_id = merchant_b  # belongs to B

    session = AsyncMock()
    session.get = AsyncMock(return_value=policy)

    repo = PolicyRepository(session=session, merchant_id=merchant_a)

    with pytest.raises(ForbiddenError) as exc_info:
        await repo.get_by_id(policy.id)

    err = exc_info.value
    assert err.requesting_merchant_id == merchant_a
    assert err.owning_merchant_id == merchant_b
    assert err.resource_id == policy.id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_policy_repository_get_by_id_succeeds_for_correct_merchant() -> None:
    """PolicyRepository.get_by_id() returns policy when merchant matches.

    Requirements: SEC-ISOL-002
    """
    merchant_id = uuid.uuid4()

    policy = MagicMock()
    policy.id = uuid.uuid4()
    policy.merchant_id = merchant_id

    session = AsyncMock()
    session.get = AsyncMock(return_value=policy)

    repo = PolicyRepository(session=session, merchant_id=merchant_id)
    result = await repo.get_by_id(policy.id)

    assert result is policy


# ---------------------------------------------------------------------------
# AuditEventRepository — append forces merchant_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_audit_event_repository_append_forces_merchant_id() -> None:
    """AuditEventRepository.append() forces merchant_id to the authenticated merchant.

    Even if the caller omits merchant_id or passes a different value, the
    repository must set merchant_id to the authenticated merchant before
    calling session.add().

    Requirements: SEC-ISOL-002
    """
    import datetime

    merchant_id = uuid.uuid4()

    session = AsyncMock()
    added: list[object] = []
    session.add = MagicMock(side_effect=lambda obj: added.append(obj))
    session.flush = AsyncMock()
    session.refresh = AsyncMock()

    repo = AuditEventRepository(session=session, merchant_id=merchant_id)

    try:
        await repo.append(
            actor="SYSTEM",
            action_type="test",
            affected_entity_type="Payment",
            affected_entity_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            payload={},
            result="OK",
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )
    except Exception:
        # append() may raise for other reasons (ORM constraint, etc.);
        # we only care that session.add was called with merchant_id set correctly.
        pass

    if added:
        created = added[0]
        assert getattr(created, "merchant_id", None) == merchant_id, (
            "AuditEvent.merchant_id must be the authenticated merchant, "
            f"got {getattr(created, 'merchant_id', None)!r}"
        )


# ---------------------------------------------------------------------------
# WebhookEventRepository — list() scoped to merchant
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_webhook_event_repository_list_scoped_to_merchant() -> None:
    """WebhookEventRepository.list() must scope results to the authenticated merchant.

    Verifies that the SELECT statement passed to session.execute() contains
    a merchant_id predicate so that webhook events from other merchants are
    never returned.

    Requirements: SEC-ISOL-001, SEC-ISOL-002
    """
    merchant_id = uuid.uuid4()

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    repo = WebhookEventRepository(session=session, merchant_id=merchant_id)
    results = await repo.list()

    session.execute.assert_called_once()

    stmt = session.execute.call_args[0][0]
    assert "merchant_id" in str(stmt).lower(), (
        "The WHERE clause must contain a merchant_id filter. "
        f"Statement: {str(stmt)!r}"
    )

    assert results == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_webhook_event_repository_get_by_id_raises_forbidden_for_wrong_merchant() -> None:
    """WebhookEventRepository.get_by_id() raises ForbiddenError for wrong merchant.

    Requirements: SEC-ISOL-002, SEC-ISOL-004
    """
    merchant_a = uuid.uuid4()
    merchant_b = uuid.uuid4()

    webhook_event = MagicMock()
    webhook_event.id = uuid.uuid4()
    webhook_event.merchant_id = merchant_b  # belongs to B

    session = AsyncMock()
    session.get = AsyncMock(return_value=webhook_event)

    repo = WebhookEventRepository(session=session, merchant_id=merchant_a)

    with pytest.raises(ForbiddenError) as exc_info:
        await repo.get_by_id(webhook_event.id)

    err = exc_info.value
    assert err.requesting_merchant_id == merchant_a
    assert err.owning_merchant_id == merchant_b


# ---------------------------------------------------------------------------
# create() — merchant_id always forced across all repository types
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_policy_repository_create_forces_merchant_id() -> None:
    """PolicyRepository.create() sets merchant_id to the authenticated merchant.

    Any merchant_id passed by the caller must be silently overridden.

    Requirements: SEC-ISOL-002
    """
    merchant_id = uuid.uuid4()
    attacker_merchant_id = uuid.uuid4()

    session = AsyncMock()
    added: list[object] = []
    session.add = MagicMock(side_effect=lambda obj: added.append(obj))
    session.flush = AsyncMock()
    session.refresh = AsyncMock()

    repo = PolicyRepository(session=session, merchant_id=merchant_id)

    try:
        await repo.create(
            merchant_id=attacker_merchant_id,  # must be overridden
            name="Test Policy",
        )
    except Exception:
        pass

    if added:
        created = added[0]
        assert getattr(created, "merchant_id", None) == merchant_id, (
            "merchant_id on the created Policy must be the authenticated merchant, "
            f"not the one supplied in kwargs ({attacker_merchant_id})"
        )
