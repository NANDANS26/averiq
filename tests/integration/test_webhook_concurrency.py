"""Concurrent re-failure integration tests.

Tests the concurrent-case-creation guard in the webhook processor.
Uses mocked AsyncSession to simulate concurrent worker scenarios without
a real database.

The tests exercise ``_find_or_create_case()`` directly, which implements
the SELECT FOR UPDATE + IntegrityError catch guard documented in
``backend/webhook_processor/processor.py``.

Requirements: REL-CONC-001, REL-CONC-002
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from sqlalchemy.exc import IntegrityError

from backend.webhook_processor.processor import _find_or_create_case
from backend.state_machine.machine import TERMINAL_STATES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_payment(
    *,
    merchant_id: uuid.UUID,
    amount: int = 10000,
    currency: str = "INR",
    customer_id: uuid.UUID | None = None,
    subscription_id: uuid.UUID | None = None,
) -> MagicMock:
    """Return a minimal Payment-like mock."""
    payment = MagicMock()
    payment.id = uuid.uuid4()
    payment.merchant_id = merchant_id
    payment.gateway_payment_id = "pay_test_001"
    payment.amount = amount
    payment.currency = currency
    payment.customer_id = customer_id
    payment.subscription_id = subscription_id
    return payment


def _make_webhook_event(merchant_id: uuid.UUID) -> MagicMock:
    """Return a minimal WebhookEvent-like mock."""
    event = MagicMock()
    event.id = uuid.uuid4()
    event.merchant_id = merchant_id
    event.event_type = "payment.failed"
    event.received_at = _utcnow()
    return event


def _make_mock_case(
    *,
    merchant_id: uuid.UUID,
    payment_id: uuid.UUID,
    state: str = "OBSERVED",
    previous_case_id: uuid.UUID | None = None,
) -> MagicMock:
    """Return a minimal RevenueRiskCase-like mock."""
    case = MagicMock()
    case.id = uuid.uuid4()
    case.merchant_id = merchant_id
    case.payment_id = payment_id
    case.state = state
    case.previous_case_id = previous_case_id
    case.updated_at = _utcnow()
    return case


def _make_session_returning(case_or_none: MagicMock | None) -> AsyncMock:
    """Return a mock AsyncSession whose execute() yields ``case_or_none``.

    The session mocks ``session.begin()`` as an async context manager that
    commits without doing anything, matching how ``_find_or_create_case``
    uses ``async with session.begin():``.
    """
    session = AsyncMock()

    # Mock execute() result
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = case_or_none
    session.execute = AsyncMock(return_value=mock_result)

    # session.add() records the object so tests can inspect it
    added_objects: list[object] = []
    session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
    session._added_objects = added_objects  # expose for assertions

    session.flush = AsyncMock()

    # begin() must act as an async context manager (async with session.begin())
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)

    return session


# ---------------------------------------------------------------------------
# Test 1: No active case → new RevenueRiskCase created in OBSERVED state
#
# Requirements: REL-CONC-001
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_active_case_creates_new_case_in_observed_state() -> None:
    """When no active case exists, _find_or_create_case creates one in OBSERVED state.

    Worker arrives first — SELECT FOR UPDATE finds nothing → INSERT a new
    RevenueRiskCase in OBSERVED state.

    Requirements: REL-CONC-001
    """
    merchant_id = uuid.uuid4()
    payment = _make_payment(merchant_id=merchant_id, amount=25000, currency="INR")
    webhook_event = _make_webhook_event(merchant_id)

    # execute() returns None — no active case in the DB
    session = _make_session_returning(None)

    log = MagicMock()

    result = await _find_or_create_case(
        session=session,
        merchant_id=merchant_id,
        payment=payment,
        webhook_event=webhook_event,
        log=log,
    )

    # A new case must have been created in OBSERVED state
    assert result.state == "OBSERVED", (
        f"Expected state='OBSERVED', got state={result.state!r}"
    )

    # session.add() must have been called — INSERT path
    assert session.add.called, "session.add() must be called to INSERT the new case"

    # The added object is the new case
    assert len(session._added_objects) == 1
    new_case = session._added_objects[0]
    assert new_case.state == "OBSERVED"
    assert new_case.merchant_id == merchant_id
    assert new_case.payment_id == payment.id


# ---------------------------------------------------------------------------
# Test 2: Active case already exists → returned as-is, no INSERT
#
# Requirements: REL-CONC-001
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_active_case_exists_returns_existing_case_no_insert() -> None:
    """When an active case already exists, the existing case is returned and no INSERT occurs.

    Worker arrives second — SELECT FOR UPDATE finds the case created by the
    first worker → update updated_at, return existing case without INSERT.

    Requirements: REL-CONC-001
    """
    merchant_id = uuid.uuid4()
    payment = _make_payment(merchant_id=merchant_id)
    webhook_event = _make_webhook_event(merchant_id)

    existing_case = _make_mock_case(
        merchant_id=merchant_id,
        payment_id=payment.id,
        state="AT_RISK",
    )

    session = _make_session_returning(existing_case)
    log = MagicMock()

    result = await _find_or_create_case(
        session=session,
        merchant_id=merchant_id,
        payment=payment,
        webhook_event=webhook_event,
        log=log,
    )

    # Must return the existing case unchanged
    assert result is existing_case

    # session.add() must NOT have been called — no INSERT
    session.add.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Concurrent INSERT race — IntegrityError → re-fetch winning case
#
# Requirements: REL-CONC-001
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_integrity_error_on_insert_refetches_winning_case() -> None:
    """IntegrityError on INSERT triggers fallback re-fetch of the winning case.

    Scenario: both workers read None (no active case), both try to INSERT.
    The second INSERT hits the partial unique index idx_cases_active_payment
    and raises IntegrityError.  The guard catches this and re-fetches the
    case already committed by the winning worker.

    Requirements: REL-CONC-001
    """
    merchant_id = uuid.uuid4()
    payment = _make_payment(merchant_id=merchant_id)
    webhook_event = _make_webhook_event(merchant_id)

    # The case the winning worker committed
    winning_case = _make_mock_case(
        merchant_id=merchant_id,
        payment_id=payment.id,
        state="OBSERVED",
    )

    session = AsyncMock()

    # begin() async context manager
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)

    # First execute() (inside session.begin): returns None — no case yet
    # Second execute() (fallback read after IntegrityError): returns winning_case
    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = None

    second_result = MagicMock()
    second_result.scalar_one_or_none.return_value = winning_case

    session.execute = AsyncMock(side_effect=[first_result, second_result])

    added_objects: list[object] = []
    session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    # flush() raises IntegrityError — simulates the partial unique index rejecting
    # the duplicate INSERT
    session.flush = AsyncMock(
        side_effect=IntegrityError(
            statement="INSERT INTO revenue_risk_cases ...",
            params={},
            orig=Exception("duplicate key value violates unique constraint"),
        )
    )

    log = MagicMock()

    result = await _find_or_create_case(
        session=session,
        merchant_id=merchant_id,
        payment=payment,
        webhook_event=webhook_event,
        log=log,
    )

    # Must return the case created by the winning worker
    assert result is winning_case

    # session.add() was called (we attempted INSERT before the race was detected)
    assert session.add.called

    # execute() was called twice — once for the initial lock query, once for
    # the fallback re-fetch after IntegrityError
    assert session.execute.call_count == 2


# ---------------------------------------------------------------------------
# Test 4: TERMINAL_STATES completeness check
#
# Requirements: REL-CONC-002 (terminal state linkage), FR-STATE-017
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_terminal_states_contains_all_expected_values() -> None:
    """TERMINAL_STATES must contain all five expected terminal state values.

    The concurrent re-failure guard excludes terminal states from the
    active-case lookup.  Missing terminal states would allow a new case to be
    linked against a case that is already closed, breaking REL-CONC-002.

    Requirements: REL-CONC-002, FR-STATE-017
    """
    expected = {"RECOVERED", "STOPPED", "ESCALATED", "FAILED", "UNKNOWN_TERMINAL"}

    assert expected == TERMINAL_STATES, (
        f"TERMINAL_STATES is incomplete.\n"
        f"Expected: {sorted(expected)}\n"
        f"Got:      {sorted(TERMINAL_STATES)}"
    )


# ---------------------------------------------------------------------------
# Test 5: New case inherits payment financial data
#
# Requirements: REL-CONC-001, DATA-001
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_new_case_inherits_payment_financial_data() -> None:
    """A newly created RevenueRiskCase copies amount and currency from Payment.

    Financial data must flow from the Payment record (which holds the
    authoritative paise amount) to the new case so that
    payment_amount_at_risk and currency are always consistent.

    Requirements: REL-CONC-001, DATA-001, DATA-005
    """
    merchant_id = uuid.uuid4()
    payment = _make_payment(
        merchant_id=merchant_id,
        amount=50000,   # 500.00 INR in paise
        currency="INR",
    )
    webhook_event = _make_webhook_event(merchant_id)

    # No active case — INSERT path
    session = _make_session_returning(None)
    log = MagicMock()

    result = await _find_or_create_case(
        session=session,
        merchant_id=merchant_id,
        payment=payment,
        webhook_event=webhook_event,
        log=log,
    )

    # Verify financial data was copied from Payment
    assert result.payment_amount_at_risk == 50000, (
        f"Expected payment_amount_at_risk=50000, got {result.payment_amount_at_risk}"
    )
    assert result.currency == "INR", (
        f"Expected currency='INR', got {result.currency!r}"
    )

    # Verify financial amount is an integer (DATA-001 — never float)
    assert isinstance(result.payment_amount_at_risk, int), (
        "payment_amount_at_risk must be an int (integer paise), not "
        f"{type(result.payment_amount_at_risk).__name__}"
    )
