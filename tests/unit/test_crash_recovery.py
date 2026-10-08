"""Unit tests for crash recovery — STATUSES_REQUIRING_RECONCILIATION set.

These tests verify that the correct statuses trigger reconciliation on restart,
implementing the crash recovery requirement:

  On worker restart, read external_operations.status.
  TIMEOUT/UNKNOWN → run reconciliation before any further action.
  SUCCEEDED/FAILED → no reconciliation needed.

Tests:
  1. "TIMEOUT" status is in STATUSES_REQUIRING_RECONCILIATION
  2. "UNKNOWN" status is in STATUSES_REQUIRING_RECONCILIATION
  3. "SUCCEEDED" status is NOT in STATUSES_REQUIRING_RECONCILIATION

Requirements: FR-EXEC-008, REL-FAIL-002, REL-FAIL-003
"""
from __future__ import annotations

import pytest

from backend.execution_engine.reconciliation import STATUSES_REQUIRING_RECONCILIATION


# ---------------------------------------------------------------------------
# Test 1: TIMEOUT requires reconciliation on crash recovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_timeout_status_requires_reconciliation() -> None:
    """TIMEOUT must be in STATUSES_REQUIRING_RECONCILIATION.

    When a worker restarts and finds an operation in TIMEOUT state, it must
    run reconciliation before any further action. A TIMEOUT means the outcome
    is unknown — it may have succeeded or failed. Blindly replaying would risk
    a duplicate financial action.

    Requirements: FR-EXEC-008, REL-FAIL-003
    """
    assert "TIMEOUT" in STATUSES_REQUIRING_RECONCILIATION, (
        "TIMEOUT must trigger reconciliation on crash recovery — "
        "TIMEOUT ≠ FAILED; outcome is unknown"
    )


# ---------------------------------------------------------------------------
# Test 2: UNKNOWN requires reconciliation on crash recovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unknown_status_requires_reconciliation() -> None:
    """UNKNOWN must be in STATUSES_REQUIRING_RECONCILIATION.

    When a worker restarts and finds an operation in UNKNOWN state, it must
    run reconciliation before any further action. UNKNOWN means a prior
    reconciliation attempt itself timed out — still no definitive answer.
    Re-execution is blocked until the outcome is resolved.

    Requirements: FR-EXEC-008, REL-IDEMP-003
    """
    assert "UNKNOWN" in STATUSES_REQUIRING_RECONCILIATION, (
        "UNKNOWN must trigger reconciliation on crash recovery — "
        "outcome is still unresolved; never replay without confirming"
    )


# ---------------------------------------------------------------------------
# Test 3: SUCCEEDED does NOT require reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_succeeded_status_does_not_require_reconciliation() -> None:
    """SUCCEEDED must NOT be in STATUSES_REQUIRING_RECONCILIATION.

    If an operation has already SUCCEEDED, there is no ambiguity and no
    reconciliation is needed. Attempting to reconcile a SUCCEEDED operation
    could erroneously re-execute a completed financial action.

    Requirements: FR-EXEC-008, REL-IDEMP-002
    """
    assert "SUCCEEDED" not in STATUSES_REQUIRING_RECONCILIATION, (
        "SUCCEEDED must NOT trigger reconciliation — outcome is definitively known"
    )
