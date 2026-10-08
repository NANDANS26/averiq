"""Execution Engine — executes EXECUTABLE interventions via Gateway_Abstraction.

Idempotency invariant: for any action_id previously completed SUCCEEDED,
resubmitting returns the cached result with no new external API call. The count
of external API calls for a given action_id is at most 1.

TIMEOUT ≠ FAILED: a timeout means the outcome is unknown. The engine never
transitions to FAILED on timeout; it blocks replay until reconciliation
confirms the definitive outcome.

Modules:
- engine.py — ExecutionEngine with full idempotency and TIMEOUT reconciliation
- guards.py  — CanExecuteAction pre-condition guard (4 checks)
"""
