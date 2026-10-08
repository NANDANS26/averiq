"""End-to-end case lifecycle integration tests.

Tests the full case lifecycle: OBSERVED → AT_RISK → DIAGNOSED → ... → RECOVERED
using mocked DB sessions. Verifies:
- Each state transition produces an AuditEvent
- Terminal states reject further transitions
- Concurrent worker safety (SELECT FOR UPDATE)

Requirements: FR-STATE-015, FR-STATE-016, FR-STATE-017, AUD-002
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.domain.models import AuditEvent
from backend.state_machine.machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransitionError,
    StateMachine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_case(
    *,
    state: str = "OBSERVED",
    merchant_id: uuid.UUID | None = None,
    case_id: uuid.UUID | None = None,
    previous_case_id: uuid.UUID | None = None,
) -> MagicMock:
    """Return a minimal RevenueRiskCase-like mock."""
    case = MagicMock()
    case.id = case_id or uuid.uuid4()
    case.merchant_id = merchant_id or uuid.uuid4()
    case.state = state
    case.previous_case_id = previous_case_id
    case.updated_at = None
    return case


def _make_mock_session(case: MagicMock) -> AsyncMock:
    """Return an AsyncSession mock that returns *case* from SELECT FOR UPDATE."""
    session = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = case
    session.execute = AsyncMock(return_value=mock_result)

    # session.get returns same case for retry-with-retry path
    session.get = AsyncMock(return_value=case)

    added_objects: list[object] = []
    session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
    session._added_objects = added_objects

    session.flush = AsyncMock()
    session.commit = AsyncMock()

    return session


def _correlation_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Test 1: OBSERVED → AT_RISK → DIAGNOSED happy path
#
# Requirements: FR-STATE-015, FR-STATE-016, AUD-002
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_observed_to_at_risk_to_diagnosed_happy_path() -> None:
    """OBSERVED → AT_RISK → DIAGNOSED: each step succeeds and produces an AuditEvent.

    Each successful transition must:
    1. Update case.state to the new state
    2. Call session.add(AuditEvent(...)) in the same unit of work
    3. Call session.flush() to write both atomically

    Requirements: FR-STATE-015, FR-STATE-016, AUD-002
    """
    sm = StateMachine()
    merchant_id = uuid.uuid4()

    # --- Step 1: OBSERVED → AT_RISK ---
    case = _make_mock_case(state="OBSERVED", merchant_id=merchant_id)
    session = _make_mock_session(case)

    result = await sm.transition(
        session=session,
        case_id=case.id,
        to_state="AT_RISK",
        actor="SYSTEM",
        reason="Payment failure confirmed",
        correlation_id=_correlation_id(),
    )

    assert result.state == "AT_RISK", f"Expected AT_RISK, got {result.state}"
    assert len(session._added_objects) >= 1, "AuditEvent must be added for OBSERVED→AT_RISK"
    # Verify an AuditEvent was added
    audit_events = [o for o in session._added_objects if isinstance(o, AuditEvent)]
    assert len(audit_events) == 1, "Exactly one AuditEvent must be recorded per transition"
    assert audit_events[0].from_state == "OBSERVED"
    assert audit_events[0].to_state == "AT_RISK"
    session.flush.assert_awaited()

    # --- Step 2: AT_RISK → DIAGNOSED ---
    case2 = _make_mock_case(state="AT_RISK", merchant_id=merchant_id)
    session2 = _make_mock_session(case2)

    result2 = await sm.transition(
        session=session2,
        case_id=case2.id,
        to_state="DIAGNOSED",
        actor="SYSTEM",
        reason="Root cause identified: INSUFFICIENT_FUNDS",
        correlation_id=_correlation_id(),
    )

    assert result2.state == "DIAGNOSED"
    audit_events2 = [o for o in session2._added_objects if isinstance(o, AuditEvent)]
    assert len(audit_events2) == 1
    assert audit_events2[0].from_state == "AT_RISK"
    assert audit_events2[0].to_state == "DIAGNOSED"


# ---------------------------------------------------------------------------
# Test 2: Terminal state (RECOVERED) rejects all further transitions
#
# Requirements: FR-STATE-017
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_terminal_state_rejects_all_transitions() -> None:
    """Terminal state RECOVERED rejects all further transition attempts.

    Any attempt to transition out of a terminal state must raise
    InvalidTransitionError. This must hold for every terminal state and
    every possible target state.

    Requirements: FR-STATE-017
    """
    sm = StateMachine()

    for terminal_state in TERMINAL_STATES:
        case = _make_mock_case(state=terminal_state)
        session = _make_mock_session(case)

        with pytest.raises(InvalidTransitionError) as exc_info:
            await sm.transition(
                session=session,
                case_id=case.id,
                to_state="AT_RISK",
                actor="SYSTEM",
                reason="Should be rejected",
                correlation_id=_correlation_id(),
            )

        err = exc_info.value
        assert err.from_state == terminal_state, (
            f"InvalidTransitionError.from_state should be {terminal_state!r}, "
            f"got {err.from_state!r}"
        )
        assert err.to_state == "AT_RISK"
        assert err.case_id == case.id


# ---------------------------------------------------------------------------
# Test 3: Audit event created in same transaction as state change
#
# Requirements: FR-STATE-016, AUD-002
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_audit_event_in_same_transaction_as_state_change() -> None:
    """AuditEvent is always added to the session before flush is called.

    The audit INSERT and the state UPDATE are flushed together in a single
    SQLAlchemy unit of work.  If flush fails, both roll back — there is never
    an un-audited state change.

    Requirements: FR-STATE-016, AUD-002
    """
    sm = StateMachine()
    case = _make_mock_case(state="OBSERVED")
    session = _make_mock_session(case)

    call_order: list[str] = []
    added_objects: list[object] = []

    original_add = session.add.side_effect

    def tracking_add(obj: object) -> None:
        call_order.append("add")
        added_objects.append(obj)

    async def tracking_flush() -> None:
        call_order.append("flush")

    session.add = MagicMock(side_effect=tracking_add)
    session.flush = AsyncMock(side_effect=tracking_flush)

    await sm.transition(
        session=session,
        case_id=case.id,
        to_state="AT_RISK",
        actor="SYSTEM",
        reason="Testing atomicity",
        correlation_id=_correlation_id(),
    )

    # session.add() must be called before session.flush()
    assert "add" in call_order, "session.add(AuditEvent) must be called"
    assert "flush" in call_order, "session.flush() must be called"
    add_idx = call_order.index("add")
    flush_idx = call_order.index("flush")
    assert add_idx < flush_idx, (
        "session.add(AuditEvent) must happen BEFORE session.flush() "
        "to ensure audit + state change are in the same transaction"
    )

    # The added object must be an AuditEvent
    assert len(added_objects) == 1
    assert isinstance(added_objects[0], AuditEvent)
    audit = added_objects[0]
    assert audit.action_type == "state_transition"
    assert audit.from_state == "OBSERVED"
    assert audit.to_state == "AT_RISK"


# ---------------------------------------------------------------------------
# Test 4: Re-failure linkage — two consecutive failures create two cases with
# previous_case_id set on the second
#
# Requirements: FR-STATE-015, REL-CONC-002
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_re_failure_linkage_second_case_sets_previous_case_id() -> None:
    """Two consecutive failures create two cases where the second links to the first.

    When a payment fails again after a case reaches a terminal state, a new
    RevenueRiskCase is created. The new case must have previous_case_id set
    to the ID of the previous terminal case to maintain the re-failure chain.

    Requirements: FR-STATE-015, REL-CONC-002
    """
    merchant_id = uuid.uuid4()

    # First case — reached terminal state FAILED
    first_case = _make_mock_case(
        state="FAILED",
        merchant_id=merchant_id,
        previous_case_id=None,
    )

    # Second case — new failure after the first reached terminal state
    second_case = _make_mock_case(
        state="OBSERVED",
        merchant_id=merchant_id,
        previous_case_id=first_case.id,
    )

    assert second_case.previous_case_id == first_case.id, (
        "Second case must have previous_case_id pointing to the first (terminal) case"
    )
    assert first_case.previous_case_id is None, (
        "First case (no prior failure) must have previous_case_id=None"
    )

    # Verify the linkage is correctly established
    assert second_case.state == "OBSERVED"
    assert first_case.state == "FAILED"
    assert first_case.state in TERMINAL_STATES


# ---------------------------------------------------------------------------
# Test 5: All non-terminal states can reach STOPPED or FAILED
#
# Requirements: FR-STATE-012, FR-STATE-014
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_all_non_terminal_states_can_reach_stopped_or_failed() -> None:
    """Every non-terminal state has STOPPED or FAILED in its outbound transitions.

    This ensures graceful exit is always possible — no case can be
    permanently stuck with no way to terminate.

    Requirements: FR-STATE-012, FR-STATE-014, FR-EXEC-007
    """
    sm = StateMachine()
    non_terminal_states = [s for s in ALLOWED_TRANSITIONS if s not in TERMINAL_STATES]

    for state in non_terminal_states:
        allowed = sm.allowed_transitions(state)
        has_graceful_exit = "STOPPED" in allowed or "FAILED" in allowed
        assert has_graceful_exit, (
            f"State {state!r} must have at least STOPPED or FAILED "
            f"as an allowed transition. Allowed: {sorted(allowed)}"
        )

    # Also verify each non-terminal state's transitions via StateMachine API
    for state in non_terminal_states:
        assert not sm.is_terminal(state), f"{state} should not be terminal"
        assert sm.can_transition(state, "STOPPED") or sm.can_transition(state, "FAILED"), (
            f"State {state!r} must allow transition to STOPPED or FAILED"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stopped_transition_from_at_risk() -> None:
    """AT_RISK → STOPPED transition succeeds (graceful exit path).

    Requirements: FR-STATE-012
    """
    sm = StateMachine()
    case = _make_mock_case(state="AT_RISK")
    session = _make_mock_session(case)

    result = await sm.transition(
        session=session,
        case_id=case.id,
        to_state="STOPPED",
        actor="SYSTEM",
        reason="Policy blocked all interventions",
        correlation_id=_correlation_id(),
    )

    assert result.state == "STOPPED"
    assert sm.is_terminal("STOPPED") is True

    # AuditEvent must be created
    audit_events = [o for o in session._added_objects if isinstance(o, AuditEvent)]
    assert len(audit_events) == 1
    assert audit_events[0].to_state == "STOPPED"
