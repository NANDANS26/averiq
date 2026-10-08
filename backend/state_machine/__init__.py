"""State machine — manages RevenueRiskCase lifecycle transitions.

The state machine enforces the allowed transition map, records AuditEvents in
the same DB transaction as every state change, and serialises concurrent
workers via SELECT FOR UPDATE (never SKIP LOCKED).

Modules:
- machine.py — StateMachine class with full ALLOWED_TRANSITIONS map

Public exports
--------------
StateMachine       — main class; stateless; session injected per call
InvalidTransitionError — raised when a caller attempts a disallowed transition
CaseNotFoundError  — raised when the target case_id does not exist
ALLOWED_TRANSITIONS — the canonical transition map (dict[str, frozenset[str]])
TERMINAL_STATES    — frozenset of terminal state strings
"""

from backend.state_machine.machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    CaseNotFoundError,
    InvalidTransitionError,
    StateMachine,
)

__all__ = [
    "StateMachine",
    "InvalidTransitionError",
    "CaseNotFoundError",
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATES",
]
