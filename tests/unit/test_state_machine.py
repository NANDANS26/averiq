"""Unit tests for state machine — invalid transitions and terminal state rejection.

Tests verify:
1. Every terminal state rejects every possible transition (parameterized)
2. OBSERVED → RECOVERED is invalid (not a direct path)
3. AT_RISK → OBSERVED is invalid (backward)
4. DIAGNOSED → VERIFYING is invalid (skips states)
5. Valid OBSERVED → AT_RISK is in ALLOWED_TRANSITIONS
6. All 5 terminal states have empty transition sets
7. can_transition(terminal, anything) always returns False
8. is_terminal("RECOVERED") returns True; is_terminal("OBSERVED") returns False

Requirements: FR-STATE-015, FR-STATE-016, FR-STATE-017
"""
from __future__ import annotations

import pytest

from backend.state_machine.machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransitionError,
    StateMachine,
)

# All non-terminal states (potential transition targets for testing)
_ALL_KNOWN_STATES = set(ALLOWED_TRANSITIONS.keys())
_NON_TERMINAL_STATES = _ALL_KNOWN_STATES - TERMINAL_STATES

# ---------------------------------------------------------------------------
# Test 1: Every terminal state rejects every possible transition (parameterized)
# ---------------------------------------------------------------------------

# Build parameterized cases: (terminal_state, target_state) for all combinations
_terminal_transition_cases = [
    (terminal, target)
    for terminal in sorted(TERMINAL_STATES)
    for target in sorted(_ALL_KNOWN_STATES)
]


@pytest.mark.unit
@pytest.mark.parametrize("terminal_state,target_state", _terminal_transition_cases)
def test_terminal_state_rejects_all_transitions(
    terminal_state: str, target_state: str
) -> None:
    """Every terminal state must reject every possible transition attempt.

    ALLOWED_TRANSITIONS for terminal states must be an empty frozenset.
    can_transition(terminal, anything) must always return False.

    Requirements: FR-STATE-017
    """
    sm = StateMachine()
    assert not sm.can_transition(terminal_state, target_state), (
        f"Terminal state {terminal_state!r} must not allow transition to {target_state!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: OBSERVED → RECOVERED is invalid (not a direct path)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_observed_to_recovered_is_invalid() -> None:
    """OBSERVED → RECOVERED is not a valid direct transition.

    Recovery requires passing through AT_RISK → DIAGNOSED → ... → VERIFYING first.
    Direct jumps to terminal states from OBSERVED are not permitted.

    Requirements: FR-STATE-015, FR-STATE-016
    """
    sm = StateMachine()
    assert not sm.can_transition("OBSERVED", "RECOVERED"), (
        "OBSERVED → RECOVERED must not be a valid transition"
    )
    assert "RECOVERED" not in ALLOWED_TRANSITIONS.get("OBSERVED", frozenset())


# ---------------------------------------------------------------------------
# Test 3: AT_RISK → OBSERVED is invalid (backward)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_at_risk_to_observed_is_invalid_backward() -> None:
    """AT_RISK → OBSERVED is an invalid backward transition.

    State machine transitions are one-directional; backward transitions are
    explicitly prohibited.

    Requirements: FR-STATE-016
    """
    sm = StateMachine()
    assert not sm.can_transition("AT_RISK", "OBSERVED"), (
        "AT_RISK → OBSERVED must not be allowed (backward transition)"
    )
    assert "OBSERVED" not in ALLOWED_TRANSITIONS.get("AT_RISK", frozenset())


# ---------------------------------------------------------------------------
# Test 4: DIAGNOSED → VERIFYING is invalid (skips states)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_diagnosed_to_verifying_is_invalid_skips_states() -> None:
    """DIAGNOSED → VERIFYING is invalid because it skips intermediate states.

    The required path is DIAGNOSED → RECOVERY_ELIGIBLE → ACTION_PENDING →
    ACTION_APPROVED → ACTION_EXECUTING → VERIFYING.

    Requirements: FR-STATE-015
    """
    sm = StateMachine()
    assert not sm.can_transition("DIAGNOSED", "VERIFYING"), (
        "DIAGNOSED → VERIFYING must not be allowed (skips intermediate states)"
    )
    assert "VERIFYING" not in ALLOWED_TRANSITIONS.get("DIAGNOSED", frozenset())


# ---------------------------------------------------------------------------
# Test 5: Valid OBSERVED → AT_RISK is in ALLOWED_TRANSITIONS
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_observed_to_at_risk_is_valid() -> None:
    """OBSERVED → AT_RISK is the first valid transition in the happy path.

    This transition confirms revenue is at risk and begins the diagnosis flow.

    Requirements: FR-STATE-015
    """
    sm = StateMachine()
    assert sm.can_transition("OBSERVED", "AT_RISK"), (
        "OBSERVED → AT_RISK must be a valid transition"
    )
    assert "AT_RISK" in ALLOWED_TRANSITIONS.get("OBSERVED", frozenset())


# ---------------------------------------------------------------------------
# Test 6: All 5 terminal states have empty transition sets
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_terminal_states_have_empty_transition_sets() -> None:
    """All 5 terminal states must have empty frozenset() in ALLOWED_TRANSITIONS.

    Terminal states: RECOVERED, STOPPED, ESCALATED, FAILED, UNKNOWN_TERMINAL.

    Requirements: FR-STATE-017
    """
    expected_terminals = {"RECOVERED", "STOPPED", "ESCALATED", "FAILED", "UNKNOWN_TERMINAL"}
    assert expected_terminals == TERMINAL_STATES

    for terminal in TERMINAL_STATES:
        allowed = ALLOWED_TRANSITIONS.get(terminal, frozenset())
        assert len(allowed) == 0, (
            f"Terminal state {terminal!r} must have empty transition set, "
            f"but has: {allowed}"
        )


# ---------------------------------------------------------------------------
# Test 7: can_transition(terminal, anything) always returns False
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_can_transition_returns_false_for_all_terminal_from_states() -> None:
    """can_transition(terminal_state, any_state) always returns False.

    Exhaustively tests all (terminal, target) pairs across all known states.

    Requirements: FR-STATE-017
    """
    sm = StateMachine()
    for terminal in TERMINAL_STATES:
        for target in _ALL_KNOWN_STATES:
            assert not sm.can_transition(terminal, target), (
                f"can_transition({terminal!r}, {target!r}) returned True — "
                "terminal states must block all transitions"
            )
        # Also test with a completely unknown state name
        assert not sm.can_transition(terminal, "SOME_UNKNOWN_STATE")


# ---------------------------------------------------------------------------
# Test 8: is_terminal() returns True for RECOVERED, False for OBSERVED
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_terminal_returns_true_for_recovered() -> None:
    """is_terminal('RECOVERED') must return True.

    RECOVERED is a canonical terminal state per FR-STATE-017.

    Requirements: FR-STATE-017
    """
    sm = StateMachine()
    assert sm.is_terminal("RECOVERED") is True


@pytest.mark.unit
def test_is_terminal_returns_false_for_observed() -> None:
    """is_terminal('OBSERVED') must return False.

    OBSERVED is the initial state — not a terminal state.

    Requirements: FR-STATE-017
    """
    sm = StateMachine()
    assert sm.is_terminal("OBSERVED") is False


@pytest.mark.unit
def test_is_terminal_returns_true_for_all_five_terminal_states() -> None:
    """is_terminal() returns True for all five defined terminal states.

    Requirements: FR-STATE-017
    """
    sm = StateMachine()
    for terminal in {"RECOVERED", "STOPPED", "ESCALATED", "FAILED", "UNKNOWN_TERMINAL"}:
        assert sm.is_terminal(terminal) is True, (
            f"is_terminal({terminal!r}) must return True"
        )


@pytest.mark.unit
def test_is_terminal_returns_false_for_all_non_terminal_states() -> None:
    """is_terminal() returns False for all non-terminal states."""
    sm = StateMachine()
    for state in _NON_TERMINAL_STATES:
        assert sm.is_terminal(state) is False, (
            f"is_terminal({state!r}) must return False"
        )
