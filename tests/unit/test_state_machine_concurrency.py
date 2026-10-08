"""Concurrency safety tests for StateMachine.

Documents and verifies that:
1. SELECT FOR UPDATE (not SKIP LOCKED) is used in transition() — serialises
   concurrent Celery workers on the same case_id (REL-CONC-001, FR-EXEC-007).
2. transition_with_retry() returns the case when it is already at the target
   state (another worker succeeded first) — no exception raised.
3. transition_with_retry() returns the case when the case is already in a
   terminal state (another worker advanced it past the target).
4. transition_with_retry() re-raises InvalidTransitionError after max_retries
   when neither condition above applies.
5. Exponential back-off delays are applied between retry attempts.
6. TERMINAL_STATES contains all five terminal states.

Requirements: FR-EXEC-007, REL-CONC-001
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from backend.state_machine.machine import (
    TERMINAL_STATES,
    CaseNotFoundError,
    InvalidTransitionError,
    StateMachine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_case(state: str, case_id: uuid.UUID | None = None) -> MagicMock:
    """Return a lightweight mock that looks like a ``RevenueRiskCase``."""
    case = MagicMock()
    case.id = case_id or uuid.uuid4()
    case.merchant_id = uuid.uuid4()
    case.state = state
    return case


def _make_mock_session(locked_case: MagicMock) -> AsyncMock:
    """Return a mock AsyncSession whose execute() returns ``locked_case``."""
    session = AsyncMock()

    # session.execute() returns a result whose scalar_one_or_none() gives locked_case
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = locked_case
    session.execute = AsyncMock(return_value=mock_result)
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# 1. SELECT FOR UPDATE — not SKIP LOCKED
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_uses_for_update_not_skip_locked() -> None:
    """transition() must call .with_for_update() without skip_locked=True.

    The SELECT FOR UPDATE serialises concurrent Celery workers on the same
    case_id (case state serialisation semantics, not queue consumption).
    SKIP LOCKED would allow a second worker to proceed past the lock and
    create race conditions in state transitions — it is reserved exclusively
    for the outbox beat queue (task 6.2).

    Requirements: REL-CONC-001, REL-CONC-002
    """
    case_id = uuid.uuid4()
    case = _make_mock_case("OBSERVED", case_id)
    session = _make_mock_session(case)

    sm = StateMachine()

    with patch(
        "backend.state_machine.machine.select"
    ) as mock_select, patch(
        "backend.state_machine.machine.update"
    ) as mock_update:
        # Build a realistic chain: select(...).where(...).with_for_update()
        mock_stmt = MagicMock()
        mock_stmt.where.return_value = mock_stmt
        mock_stmt.with_for_update.return_value = mock_stmt
        mock_select.return_value = mock_stmt

        mock_update_stmt = MagicMock()
        mock_update_stmt.where.return_value = mock_update_stmt
        mock_update_stmt.values.return_value = mock_update_stmt
        mock_update.return_value = mock_update_stmt

        await sm.transition(
            session=session,
            case_id=case_id,
            to_state="AT_RISK",
            actor="SYSTEM",
            reason="test",
            correlation_id=str(uuid.uuid4()),
        )

    # .with_for_update() must have been called on the SELECT statement
    mock_stmt.with_for_update.assert_called_once()

    # Verify it was NOT called with skip_locked=True
    call_kwargs = mock_stmt.with_for_update.call_args
    if call_kwargs is not None:
        _, kwargs = call_kwargs
        assert kwargs.get("skip_locked") is not True, (
            "with_for_update() must not use skip_locked=True for case state transitions"
        )


# ---------------------------------------------------------------------------
# 2. transition_with_retry — succeeds on first attempt
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_with_retry_succeeds_on_first_attempt() -> None:
    """When transition() succeeds immediately, transition_with_retry returns the case.

    No retries should be attempted and no back-off delay should occur.

    Requirements: FR-EXEC-007
    """
    case_id = uuid.uuid4()
    expected_case = _make_mock_case("AT_RISK", case_id)
    session = AsyncMock()

    sm = StateMachine()

    # Patch transition() to succeed on the first call
    sm.transition = AsyncMock(return_value=expected_case)

    result = await sm.transition_with_retry(
        session=session,
        case_id=case_id,
        to_state="AT_RISK",
        actor="SYSTEM",
        reason="test",
        correlation_id=str(uuid.uuid4()),
        max_retries=3,
        base_delay_seconds=0.0,
    )

    assert result is expected_case
    # transition() called exactly once — no retry
    sm.transition.assert_called_once()


# ---------------------------------------------------------------------------
# 3. transition_with_retry — returns case when already at target state
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_with_retry_returns_case_when_already_at_target_state() -> None:
    """When another worker already advanced the case to to_state, return the case.

    Scenario: two concurrent workers both try to transition case from OBSERVED
    to AT_RISK.  Worker 1 succeeds.  Worker 2 gets InvalidTransitionError
    because the case is now at AT_RISK, which is not in
    ALLOWED_TRANSITIONS["AT_RISK"].  transition_with_retry re-reads the case,
    sees current state == to_state, and returns the case without re-raising.

    Requirements: REL-CONC-001, FR-EXEC-007
    """
    case_id = uuid.uuid4()
    # After first worker committed, case is already at target "AT_RISK"
    updated_case = _make_mock_case("AT_RISK", case_id)
    session = AsyncMock()
    # session.get() returns the re-read case (already at target)
    session.get = AsyncMock(return_value=updated_case)

    sm = StateMachine()

    # transition() raises because case is already at AT_RISK
    sm.transition = AsyncMock(
        side_effect=InvalidTransitionError("AT_RISK", "AT_RISK", case_id)
    )

    result = await sm.transition_with_retry(
        session=session,
        case_id=case_id,
        to_state="AT_RISK",
        actor="SYSTEM",
        reason="test",
        correlation_id=str(uuid.uuid4()),
        max_retries=3,
        base_delay_seconds=0.0,
    )

    # Should return the re-read case (already at target)
    assert result is updated_case
    assert result.state == "AT_RISK"
    # No exception raised
    sm.transition.assert_called_once()


# ---------------------------------------------------------------------------
# 4. transition_with_retry — returns case when case is in terminal state
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_with_retry_returns_none_when_case_in_terminal_state() -> None:
    """When case is already in a terminal state after InvalidTransitionError, return case.

    Scenario: worker tries to transition to AT_RISK but another worker already
    moved the case to FAILED (a terminal state).  transition_with_retry
    re-reads, finds FAILED in TERMINAL_STATES, and returns the case — no
    further action needed.

    Requirements: REL-CONC-001
    """
    case_id = uuid.uuid4()

    for terminal_state in TERMINAL_STATES:
        terminal_case = _make_mock_case(terminal_state, case_id)
        session = AsyncMock()
        session.get = AsyncMock(return_value=terminal_case)

        sm = StateMachine()
        sm.transition = AsyncMock(
            side_effect=InvalidTransitionError("AT_RISK", "DIAGNOSED", case_id)
        )

        result = await sm.transition_with_retry(
            session=session,
            case_id=case_id,
            to_state="DIAGNOSED",
            actor="SYSTEM",
            reason="test",
            correlation_id=str(uuid.uuid4()),
            max_retries=3,
            base_delay_seconds=0.0,
        )

        # Must return the case (in terminal state) without raising
        assert result is terminal_case
        assert result.state == terminal_state


# ---------------------------------------------------------------------------
# 5. transition_with_retry — raises after max_retries exhausted
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_with_retry_raises_after_max_retries() -> None:
    """When transition always fails with a non-terminal, non-target state, re-raise.

    If the case is in a state that neither matches to_state nor is terminal,
    the method retries up to max_retries times then re-raises
    InvalidTransitionError.

    Requirements: FR-EXEC-007
    """
    case_id = uuid.uuid4()
    max_retries = 2

    # After each failed transition, case is still in RECOVERY_ELIGIBLE
    # (not terminal, not target state "ACTION_APPROVED")
    stale_case = _make_mock_case("RECOVERY_ELIGIBLE", case_id)
    session = AsyncMock()
    session.get = AsyncMock(return_value=stale_case)

    sm = StateMachine()
    sm.transition = AsyncMock(
        side_effect=InvalidTransitionError("RECOVERY_ELIGIBLE", "ACTION_APPROVED", case_id)
    )

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(InvalidTransitionError) as exc_info:
            await sm.transition_with_retry(
                session=session,
                case_id=case_id,
                to_state="ACTION_APPROVED",
                actor="SYSTEM",
                reason="test",
                correlation_id=str(uuid.uuid4()),
                max_retries=max_retries,
                base_delay_seconds=0.1,
            )

    err = exc_info.value
    assert err.from_state == "RECOVERY_ELIGIBLE"
    assert err.to_state == "ACTION_APPROVED"

    # transition() called max_retries + 1 times total (initial + retries)
    assert sm.transition.call_count == max_retries + 1

    # asyncio.sleep() called max_retries times (not on last attempt)
    assert mock_sleep.call_count == max_retries


# ---------------------------------------------------------------------------
# 6. TERMINAL_STATES completeness
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_terminal_states_are_complete() -> None:
    """TERMINAL_STATES must contain all five terminal/sentinel states.

    Per FR-STATE-017 and the design:
      RECOVERED, STOPPED, ESCALATED, FAILED — the four named terminal states
      UNKNOWN_TERMINAL — the catch-all terminal sentinel

    Requirements: FR-STATE-017
    """
    expected_terminal_states = {
        "RECOVERED",
        "STOPPED",
        "ESCALATED",
        "FAILED",
        "UNKNOWN_TERMINAL",
    }
    assert expected_terminal_states == TERMINAL_STATES, (
        f"TERMINAL_STATES mismatch.\n"
        f"Expected: {sorted(expected_terminal_states)}\n"
        f"Got:      {sorted(TERMINAL_STATES)}"
    )
