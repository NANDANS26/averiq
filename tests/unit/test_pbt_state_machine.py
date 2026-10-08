"""Property-Based Tests: State Machine Transition Validity (Property 3).

Property 3: State Machine Transition Validity
  - For any non-terminal state + valid target transition, StateMachine.can_transition()
    returns True.
  - For any state + target not in ALLOWED_TRANSITIONS[state], can_transition() returns False.
  - For any terminal state, all transitions are rejected.
  - Audit atomicity: if audit INSERT fails, state change must NOT be persisted.

Feature: averiq, Property 3: State Machine Transition Validity
Requirements: FR-STATE-015, FR-STATE-017, NFR-TEST-006

**Validates: Requirements FR-STATE-015, FR-STATE-017, NFR-TEST-006**
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.state_machine.machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransitionError,
    StateMachine,
)

# ---------------------------------------------------------------------------
# State lists derived from ALLOWED_TRANSITIONS (single source of truth)
# ---------------------------------------------------------------------------

ALL_STATES: list[str] = list(ALLOWED_TRANSITIONS.keys())
NON_TERMINAL_STATES: list[str] = [s for s in ALL_STATES if s not in TERMINAL_STATES]
TERMINAL_STATES_LIST: list[str] = list(TERMINAL_STATES)


# ---------------------------------------------------------------------------
# P3a — can_transition() returns True for all valid transitions
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(from_state=st.sampled_from(NON_TERMINAL_STATES))
@settings(max_examples=200)
def test_p3a_valid_transitions_are_allowed(from_state: str) -> None:
    """For every non-terminal state, all transitions in ALLOWED_TRANSITIONS are allowed.

    Feature: averiq, Property 3: State Machine Transition Validity

    **Validates: Requirements FR-STATE-015**

    can_transition() must return True for every (from_state, to_state) pair that
    appears as an edge in the ALLOWED_TRANSITIONS map.
    """
    sm = StateMachine()
    for to_state in ALLOWED_TRANSITIONS[from_state]:
        assert sm.can_transition(from_state, to_state), (
            f"Expected {from_state} → {to_state} to be allowed by can_transition(), "
            f"but it returned False.  Check ALLOWED_TRANSITIONS."
        )


# ---------------------------------------------------------------------------
# P3b — can_transition() returns False for invalid targets
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(
    from_state=st.sampled_from(ALL_STATES),
    to_state=st.sampled_from(ALL_STATES),
)
@settings(max_examples=500)
def test_p3b_invalid_transitions_are_rejected(from_state: str, to_state: str) -> None:
    """If to_state is not in ALLOWED_TRANSITIONS[from_state], can_transition() is False.

    Feature: averiq, Property 3: State Machine Transition Validity

    **Validates: Requirements FR-STATE-015, FR-STATE-017**

    For any (from_state, to_state) pair that is NOT an edge in ALLOWED_TRANSITIONS,
    can_transition() must return False.  The test skips pairs that are valid edges
    (those are covered by P3a).
    """
    sm = StateMachine()
    allowed = ALLOWED_TRANSITIONS.get(from_state, frozenset())
    if to_state not in allowed:
        result = sm.can_transition(from_state, to_state)
        assert result is False, (
            f"can_transition({from_state!r}, {to_state!r}) returned {result!r} — "
            f"expected False because {to_state!r} is not in ALLOWED_TRANSITIONS[{from_state!r}]."
        )


# ---------------------------------------------------------------------------
# P3c — Terminal states reject ALL transitions
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(
    terminal_state=st.sampled_from(TERMINAL_STATES_LIST),
    to_state=st.sampled_from(ALL_STATES),
)
@settings(max_examples=200)
def test_p3c_terminal_states_reject_all_transitions(
    terminal_state: str, to_state: str
) -> None:
    """Terminal states must have no valid outbound transitions.

    Feature: averiq, Property 3: State Machine Transition Validity

    **Validates: Requirements FR-STATE-017**

    For every terminal state and every candidate target state:
      1. can_transition() must return False.
      2. is_terminal() must return True.
      3. allowed_transitions() must return an empty frozenset.
    """
    sm = StateMachine()

    assert sm.can_transition(terminal_state, to_state) is False, (
        f"Terminal state {terminal_state!r} must not allow any transition, "
        f"but can_transition({terminal_state!r}, {to_state!r}) returned True."
    )
    assert sm.is_terminal(terminal_state) is True, (
        f"is_terminal({terminal_state!r}) returned False; "
        f"expected True because {terminal_state!r} is in TERMINAL_STATES."
    )
    assert sm.allowed_transitions(terminal_state) == frozenset(), (
        f"allowed_transitions({terminal_state!r}) returned a non-empty set: "
        f"{sm.allowed_transitions(terminal_state)!r}"
    )


# ---------------------------------------------------------------------------
# P3d — Audit atomicity: if audit INSERT fails, state must NOT change
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@pytest.mark.asyncio
@given(from_state=st.sampled_from(NON_TERMINAL_STATES))
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow], deadline=None)
async def test_p3d_audit_insert_failure_rolls_back_state_change(
    from_state: str,
) -> None:
    """If the AuditEvent INSERT fails, the state transition must not be persisted.

    Feature: averiq, Property 3: State Machine Transition Validity

    **Validates: Requirements FR-STATE-015, FR-STATE-017**

    Atomicity invariant: the state UPDATE and AuditEvent INSERT must both
    succeed or both fail (same transaction).  The implementation flushes both
    the UPDATE and the INSERT in a single session.flush() call — so the
    atomicity is enforced by the PostgreSQL transaction, not by Python code.

    This test verifies the code path guarantees: when session.flush() raises
    (simulating a DB-level INSERT or UPDATE constraint failure), the exception
    MUST propagate out of transition() so the caller's transaction rolls back.
    No un-audited state change can silently persist.

    Implementation note on flush() call pattern:
    - For a VALID transition, machine.py calls session.flush() ONCE at the end,
      after both the state UPDATE (via session.execute) and the AuditEvent INSERT
      (via session.add) have been queued in the SQLAlchemy unit of work.
    - For an INVALID transition, session.flush() is called ONCE for the
      INVALID_TRANSITION_ATTEMPTED audit event before raising InvalidTransitionError.
    - We simulate flush failure by making flush() raise unconditionally.
    """
    # Only test states that have at least one valid outbound transition.
    valid_targets = list(ALLOWED_TRANSITIONS.get(from_state, frozenset()))
    if not valid_targets:
        # No valid targets for this state — nothing to test.
        return

    to_state = valid_targets[0]
    case_id = uuid.uuid4()

    # Build a mock case matching from_state
    mock_case = MagicMock()
    mock_case.id = case_id
    mock_case.merchant_id = uuid.uuid4()
    mock_case.state = from_state

    # Session.execute() is called twice in a valid transition:
    #   1. SELECT FOR UPDATE (returns the case)
    #   2. UPDATE revenue_risk_cases (returns a mock result — ignored)
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = mock_case
    update_result = MagicMock()

    execute_call_count: list[int] = [0]

    async def _execute_side_effect(stmt: object, **kwargs: object) -> MagicMock:
        execute_call_count[0] += 1
        if execute_call_count[0] == 1:
            return select_result  # SELECT FOR UPDATE
        return update_result  # UPDATE

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=_execute_side_effect)
    mock_session.add = MagicMock()
    # Simulate DB-level failure on flush: both the UPDATE and the audit INSERT
    # are written together; any failure here must propagate out.
    mock_session.flush = AsyncMock(
        side_effect=RuntimeError("Simulated DB flush failure (UPDATE + audit INSERT)")
    )

    sm = StateMachine()

    # The exception from the failed flush MUST propagate out of transition().
    # This proves the caller's transaction would roll back and no un-audited
    # state change can persist (the PostgreSQL transaction is the true
    # atomicity boundary; this test validates the exception propagation path).
    with pytest.raises(RuntimeError, match="Simulated DB flush failure"):
        await sm.transition(
            session=mock_session,
            case_id=case_id,
            to_state=to_state,
            actor="SYSTEM",
            reason="audit atomicity test",
            correlation_id=str(uuid.uuid4()),
        )

    # Verify that session.add() was called — the audit event was staged before
    # the flush failed, confirming both the state update and the audit INSERT
    # are inside the same flush() call (and therefore the same transaction).
    mock_session.add.assert_called()

    # Verify session.flush() was called (exactly once for a valid transition),
    # confirming the exception came from the flush, not earlier in the code path.
    mock_session.flush.assert_called_once()


# ---------------------------------------------------------------------------
# P3e — ALLOWED_TRANSITIONS completeness: all 14 states present
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_p3e_all_states_in_transition_map() -> None:
    """ALLOWED_TRANSITIONS must cover all 14 expected states.

    Feature: averiq, Property 3: State Machine Transition Validity

    **Validates: Requirements FR-STATE-015**

    Any missing state would leave a gap in the transition map — the machine
    would silently treat it as a state with no valid transitions, which is
    incorrect for non-terminal states.
    """
    expected_states = {
        # Happy-path states
        "OBSERVED",
        "AT_RISK",
        "DIAGNOSED",
        "RECOVERY_ELIGIBLE",
        "ACTION_PENDING",
        "ACTION_APPROVED",
        "ACTION_EXECUTING",
        "VERIFYING",
        "RETRYABLE",
        # Terminal states
        "RECOVERED",
        "STOPPED",
        "ESCALATED",
        "FAILED",
        "UNKNOWN_TERMINAL",
    }
    actual_states = set(ALLOWED_TRANSITIONS.keys())
    assert actual_states == expected_states, (
        f"ALLOWED_TRANSITIONS state coverage mismatch.\n"
        f"Missing from map : {sorted(expected_states - actual_states)}\n"
        f"Unexpected in map: {sorted(actual_states - expected_states)}"
    )


# ---------------------------------------------------------------------------
# P3f — is_terminal() is consistent with TERMINAL_STATES
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(state=st.sampled_from(ALL_STATES))
@settings(max_examples=100)
def test_p3f_is_terminal_consistent_with_terminal_states(state: str) -> None:
    """is_terminal(state) must return True iff state is in TERMINAL_STATES.

    Feature: averiq, Property 3: State Machine Transition Validity

    **Validates: Requirements FR-STATE-017**

    The is_terminal() helper must be in perfect agreement with the
    TERMINAL_STATES frozenset — there must be no divergence between the
    two sources of truth for terminal-state classification.
    """
    sm = StateMachine()
    expected = state in TERMINAL_STATES
    actual = sm.is_terminal(state)
    assert actual == expected, (
        f"is_terminal({state!r}) returned {actual!r} but expected {expected!r}. "
        f"TERMINAL_STATES = {sorted(TERMINAL_STATES)!r}"
    )
