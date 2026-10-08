"""State Machine for RevenueRiskCase lifecycle.

CRITICAL INVARIANTS
-------------------
1. **Audit atomicity**: the state-transition UPDATE + AuditEvent INSERT always
   execute inside the *same* database transaction.  If the audit INSERT fails,
   the whole transaction rolls back — there is no un-audited state change.

2. **FOR UPDATE (not SKIP LOCKED)**: ``SELECT … FOR UPDATE`` serialises
   concurrent Celery workers on the same ``case_id``.  The second worker
   blocks until the first commits, then re-reads the updated state.
   ``SKIP LOCKED`` is reserved exclusively for outbox beat queue consumption
   (task 6.2) where we intentionally skip locked rows.

3. **Terminal states are final**: RECOVERED, STOPPED, ESCALATED, FAILED, and
   UNKNOWN_TERMINAL have empty outgoing transition sets.  All transition
   attempts from a terminal state are rejected and audited.

4. **Concurrency safety with back-off**: When two concurrent Celery workers race
   to transition the same case, ``SELECT FOR UPDATE`` serialises their DB access.
   The second worker re-reads the post-commit state.  If the case has already
   advanced to the target state (or a terminal state), ``transition_with_retry``
   returns without raising — signalling "no action needed".  If the transition
   remains invalid after all retries, ``InvalidTransitionError`` is re-raised.

Requirements: FR-STATE-015, FR-STATE-016, FR-STATE-017, REL-CONC-001, REL-CONC-002,
              FR-EXEC-007
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import structlog

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.models import AuditEvent, RevenueRiskCase

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Transition map
# ---------------------------------------------------------------------------

#: All allowed outbound transitions per source state (string keys so the map
#: can be used without importing CaseState everywhere).  States that are
#: absent from the map are treated identically to those mapped to an empty
#: frozenset — they have no valid outbound transitions.
#:
#: This map is the single authoritative source of truth for state machine
#: topology.  It is intentionally more permissive on STOPPED / FAILED paths
#: than the "happy path" to allow graceful termination from any non-terminal
#: state (FR-STATE-012, FR-STATE-014, REL-FAIL-001).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "OBSERVED": frozenset({"AT_RISK", "STOPPED", "FAILED"}),
    "AT_RISK": frozenset({"DIAGNOSED", "STOPPED", "FAILED"}),
    "DIAGNOSED": frozenset({"RECOVERY_ELIGIBLE", "STOPPED", "FAILED"}),
    "RECOVERY_ELIGIBLE": frozenset({"ACTION_PENDING", "STOPPED", "FAILED"}),
    "ACTION_PENDING": frozenset({"ACTION_APPROVED", "STOPPED", "FAILED"}),
    "ACTION_APPROVED": frozenset({"ACTION_EXECUTING", "STOPPED", "FAILED"}),
    "ACTION_EXECUTING": frozenset({"VERIFYING", "STOPPED", "FAILED"}),
    "VERIFYING": frozenset({"RECOVERED", "RETRYABLE", "ESCALATED", "FAILED", "STOPPED"}),
    "RETRYABLE": frozenset({"RECOVERY_ELIGIBLE", "ESCALATED", "FAILED", "STOPPED"}),
    # Terminal states — empty frozenset means no valid outbound transitions
    "RECOVERED": frozenset(),
    "STOPPED": frozenset(),
    "ESCALATED": frozenset(),
    "FAILED": frozenset(),
    "UNKNOWN_TERMINAL": frozenset(),
}

#: The four canonical terminal states defined by FR-STATE-017.
#: UNKNOWN_TERMINAL is included as a catch-all terminal sentinel.
TERMINAL_STATES: frozenset[str] = frozenset(
    {"RECOVERED", "STOPPED", "ESCALATED", "FAILED", "UNKNOWN_TERMINAL"}
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InvalidTransitionError(Exception):
    """Raised when a caller requests a transition that is not in ALLOWED_TRANSITIONS.

    The exception is raised *after* the ``INVALID_TRANSITION_ATTEMPTED``
    AuditEvent has already been inserted (and committed in its own
    transaction step), so there is always an audit trail for rejected
    transitions.

    Attributes
    ----------
    from_state : str
        The state the case was in when the invalid transition was attempted.
    to_state : str
        The target state that was rejected.
    case_id : uuid.UUID
        The case whose transition was rejected.
    """

    def __init__(self, from_state: str, to_state: str, case_id: uuid.UUID) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.case_id = case_id
        super().__init__(
            f"Invalid transition: {from_state} → {to_state} for case {case_id}"
        )


class CaseNotFoundError(Exception):
    """Raised when the requested ``case_id`` does not exist in the database."""

    def __init__(self, case_id: uuid.UUID) -> None:
        self.case_id = case_id
        super().__init__(f"RevenueRiskCase not found: {case_id}")


# ---------------------------------------------------------------------------
# StateMachine
# ---------------------------------------------------------------------------


class StateMachine:
    """Manages ``RevenueRiskCase`` lifecycle transitions.

    The instance is **stateless** — all mutable context (session, case state)
    is passed in per call.  This makes the class safe to share across Celery
    workers and FastAPI requests without any locking at the Python object level.

    Concurrency safety is provided at the database level via
    ``SELECT … FOR UPDATE`` (see ``transition()`` docstring).

    Usage::

        sm = StateMachine()

        async with async_session_factory() as session:
            async with session.begin():
                case = await sm.transition(
                    session=session,
                    case_id=case_id,
                    to_state="AT_RISK",
                    actor="SYSTEM",
                    reason="Payment failure confirmed; beginning risk assessment.",
                    correlation_id=str(correlation_id),
                )
    """

    def __init__(self) -> None:
        # Intentionally empty — the instance carries no mutable state.
        pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def transition(
        self,
        session: AsyncSession,
        case_id: uuid.UUID,
        to_state: str,
        actor: str,
        reason: str,
        correlation_id: str,
    ) -> RevenueRiskCase:
        """Perform a validated, audited state transition atomically.

        The method performs the following steps **inside the caller's
        transaction** (the caller must have called ``session.begin()`` or be
        using an autobegin session):

        1. ``SELECT * FROM revenue_risk_cases WHERE id = :case_id FOR UPDATE``
           — acquires a row-level exclusive lock, serialising concurrent workers
           on the same case.  Workers that arrive second will block here until
           the first commits.

        2. Validate that ``to_state`` is in ``ALLOWED_TRANSITIONS[current_state]``.
           If not:

           a. Insert an ``INVALID_TRANSITION_ATTEMPTED`` AuditEvent.
           b. Raise ``InvalidTransitionError`` — the caller's transaction will
              roll back, but the AuditEvent INSERT was already flushed
              (SQLAlchemy flush is not commit; if the transaction rolls back
              the audit row also disappears).  The audit row is thus inside the
              same atomic transaction: if the caller chooses to handle the
              exception and commit, the audit row persists; if it rolls back,
              nothing persists.

        3. ``UPDATE revenue_risk_cases SET state = :to_state, updated_at = NOW()``

        4. ``INSERT audit_events`` with full metadata.

        Steps 3 and 4 are flushed together inside the same SQLAlchemy unit of
        work.  ``session.flush()`` writes both to the DB inside the open
        transaction without committing — the caller's ``session.commit()`` (or
        ``async with session.begin()`` context manager commit) finalises both
        atomically.

        Parameters
        ----------
        session:
            An open ``AsyncSession`` with an active transaction.  The session
            must NOT be in autocommit mode.
        case_id:
            The UUID of the ``RevenueRiskCase`` to transition.
        to_state:
            The target state string (must be a valid ``CaseState`` value).
        actor:
            Who or what is triggering the transition: ``"SYSTEM"`` for
            automated steps, or a user ID string for human-initiated actions.
        reason:
            A human-readable explanation for the transition.  Persisted in the
            AuditEvent payload and shown in the decision trace.
        correlation_id:
            The end-to-end correlation identifier for the current processing
            chain.  Stored on the AuditEvent so that all log/audit entries for
            one workflow can be found with a single query.

        Returns
        -------
        RevenueRiskCase
            The updated case ORM object (in the session's identity map) with
            ``state`` set to ``to_state`` and ``updated_at`` refreshed.

        Raises
        ------
        CaseNotFoundError
            If no row with ``id = case_id`` exists.
        InvalidTransitionError
            If ``to_state`` is not allowed from the current state.
        """
        # ------------------------------------------------------------------
        # Step 1: SELECT FOR UPDATE — serialise concurrent workers
        # ------------------------------------------------------------------
        stmt = (
            select(RevenueRiskCase)
            .where(RevenueRiskCase.id == case_id)
            .with_for_update()  # NOT SKIP LOCKED — state serialisation semantics
        )
        result = await session.execute(stmt)
        case = result.scalar_one_or_none()

        if case is None:
            raise CaseNotFoundError(case_id)

        from_state = case.state

        # ------------------------------------------------------------------
        # Step 2: Validate the requested transition
        # ------------------------------------------------------------------
        allowed = ALLOWED_TRANSITIONS.get(from_state, frozenset())
        if to_state not in allowed:
            # Insert audit record for the rejected attempt — this is in the
            # same open transaction.  Whether it ultimately persists depends
            # on whether the caller commits or rolls back after catching the
            # exception.
            invalid_audit = AuditEvent(
                id=uuid.uuid4(),
                merchant_id=case.merchant_id,
                case_id=case.id,
                actor=actor,
                action_type="INVALID_TRANSITION_ATTEMPTED",
                affected_entity_type="RevenueRiskCase",
                affected_entity_id=case.id,
                from_state=from_state,
                to_state=to_state,
                correlation_id=_parse_correlation_id(correlation_id),
                payload={
                    "reason": reason,
                    "rejection_reason": (
                        f"Transition {from_state!r} → {to_state!r} is not in "
                        f"ALLOWED_TRANSITIONS."
                        + (
                            f"  {from_state!r} is a terminal state."
                            if from_state in TERMINAL_STATES
                            else f"  Allowed from {from_state!r}: "
                            + repr(sorted(allowed))
                        )
                    ),
                },
                created_at=_utcnow(),
            )
            session.add(invalid_audit)
            await session.flush()

            raise InvalidTransitionError(from_state, to_state, case_id)

        # ------------------------------------------------------------------
        # Step 3: Update case state
        # Updating the ORM object directly keeps it in the session's identity
        # map so the returned object reflects the new state without a reload.
        # ------------------------------------------------------------------
        now = _utcnow()
        case.state = to_state
        case.updated_at = now

        # Also issue an explicit UPDATE to avoid relying solely on ORM dirty
        # tracking in case the object was loaded in a read-only way.
        await session.execute(
            update(RevenueRiskCase)
            .where(RevenueRiskCase.id == case_id)
            .values(state=to_state, updated_at=now)
        )

        # ------------------------------------------------------------------
        # Step 4: INSERT AuditEvent — same transaction as the state update
        # CRITICAL: if this INSERT fails, the transaction rolls back and the
        # state change is undone.  There is no un-audited state change.
        # ------------------------------------------------------------------
        audit_event = AuditEvent(
            id=uuid.uuid4(),
            merchant_id=case.merchant_id,
            case_id=case.id,
            actor=actor,
            action_type="state_transition",
            affected_entity_type="RevenueRiskCase",
            affected_entity_id=case.id,
            from_state=from_state,
            to_state=to_state,
            correlation_id=_parse_correlation_id(correlation_id),
            payload={
                "reason": reason,
                "from_state": from_state,
                "to_state": to_state,
            },
            created_at=now,
        )
        session.add(audit_event)

        # Flush both the UPDATE and the INSERT to the database within the open
        # transaction.  The caller's commit() finalises both atomically.
        await session.flush()

        return case

    async def transition_with_retry(
        self,
        session: AsyncSession,
        case_id: uuid.UUID,
        to_state: str,
        actor: str,
        reason: str,
        correlation_id: str,
        max_retries: int = 3,
        base_delay_seconds: float = 0.1,
    ) -> RevenueRiskCase | None:
        """Transition with exponential back-off on ``InvalidTransitionError``.

        Concurrency safety
        ------------------
        When two Celery workers race to transition the same case:

        1. ``SELECT FOR UPDATE`` in ``transition()`` serialises DB access —
           the second worker blocks until the first commits.
        2. The second worker re-reads the now-updated state.
        3. If the case is already at ``to_state`` (first worker succeeded),
           this method returns the case object immediately — no duplicate
           transition, no exception.
        4. If the case is in a terminal state (first worker advanced it past
           the target), this method returns the case object — no action needed.
        5. If the transition remains invalid after ``max_retries``, the final
           ``InvalidTransitionError`` is re-raised so the caller can handle it.

        The exponential back-off between retries (``base_delay_seconds * 2**attempt``)
        reduces thundering-herd contention when many workers compete on the same case.

        Mechanism documented per FR-EXEC-007 / REL-CONC-001:
          "SELECT FOR UPDATE serialises concurrent Celery workers on the same
           case_id.  The second worker finds the case already transitioned,
           re-reads the current state, and backs off if needed."

        Parameters
        ----------
        session:
            An open ``AsyncSession`` with an active transaction.
        case_id:
            UUID of the ``RevenueRiskCase`` to transition.
        to_state:
            The target state string.
        actor:
            "SYSTEM" or a user ID string.
        reason:
            Human-readable explanation for the transition.
        correlation_id:
            End-to-end correlation identifier for audit / logging.
        max_retries:
            Maximum number of retry attempts after the first failure.
            Default 3 (so total attempts = 4).
        base_delay_seconds:
            Base delay for exponential back-off.  Actual delay on attempt *n*
            is ``base_delay_seconds * 2**n``.  Default 0.1 s (100 ms).

        Returns
        -------
        RevenueRiskCase
            The case object after a successful transition.
        None
            Never actually returned; kept for type symmetry.  The method either
            returns a ``RevenueRiskCase`` or raises.

        Raises
        ------
        InvalidTransitionError
            If the transition is invalid and the case is not at the target state
            or a terminal state after all retries.
        CaseNotFoundError
            If the case does not exist.
        """
        for attempt in range(max_retries + 1):
            try:
                return await self.transition(
                    session, case_id, to_state, actor, reason, correlation_id
                )
            except InvalidTransitionError as exc:
                # Re-read the case to understand the current state after the
                # first worker may have committed.
                case = await session.get(RevenueRiskCase, case_id)
                if case is None:
                    # Case was deleted between the lock and the re-read.
                    raise

                current = case.state

                # -------------------------------------------------------
                # Already at target state — another worker succeeded.
                # No action needed; return the current case object.
                # -------------------------------------------------------
                if current == to_state:
                    logger.info(
                        "state_machine.transition_already_at_target",
                        case_id=str(case_id),
                        to_state=to_state,
                        actor=actor,
                    )
                    return case

                # -------------------------------------------------------
                # Case is in a terminal state — another worker advanced it
                # beyond the intended target.  No further transition is
                # possible or needed; return the current case object so the
                # caller can observe the final state.
                # -------------------------------------------------------
                if current in TERMINAL_STATES:
                    logger.info(
                        "state_machine.transition_skipped_terminal",
                        case_id=str(case_id),
                        terminal_state=current,
                        intended_to_state=to_state,
                        actor=actor,
                    )
                    return case

                # -------------------------------------------------------
                # Transition is still invalid and case is not terminal.
                # Apply exponential back-off before the next attempt.
                # -------------------------------------------------------
                if attempt < max_retries:
                    delay = base_delay_seconds * (2**attempt)
                    logger.info(
                        "state_machine.transition_retry",
                        case_id=str(case_id),
                        from_state=exc.from_state,
                        to_state=to_state,
                        current_state=current,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        delay_seconds=delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    # All retries exhausted — re-raise the original error.
                    raise

        # Unreachable — the loop always returns or raises.
        return None  # pragma: no cover

    # ------------------------------------------------------------------
    # Query helpers (no DB writes; safe to call outside a transaction)
    # ------------------------------------------------------------------

    def can_transition(self, from_state: str, to_state: str) -> bool:
        """Return ``True`` if a transition from ``from_state`` to ``to_state``
        is permitted according to ``ALLOWED_TRANSITIONS``.

        This is a pure in-memory check — it does not acquire any database
        locks or perform any I/O.  Use it for pre-flight validation or
        building UI state.

        Parameters
        ----------
        from_state:
            The current state string.
        to_state:
            The proposed target state string.
        """
        return to_state in ALLOWED_TRANSITIONS.get(from_state, frozenset())

    def is_terminal(self, state: str) -> bool:
        """Return ``True`` if ``state`` is a terminal state.

        Terminal states have no valid outbound transitions (FR-STATE-017).
        """
        return state in TERMINAL_STATES

    def allowed_transitions(self, from_state: str) -> frozenset[str]:
        """Return the set of states reachable from ``from_state``.

        Returns an empty frozenset for terminal states or unknown states.
        """
        return ALLOWED_TRANSITIONS.get(from_state, frozenset())


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC datetime as a timezone-aware value (DATA-004)."""
    return datetime.now(tz=timezone.utc)


def _parse_correlation_id(correlation_id: str) -> uuid.UUID:
    """Parse ``correlation_id`` string to UUID, generating a new one if invalid.

    The AuditEvent ORM model stores ``correlation_id`` as a UUID column.
    Callers that pass a pre-formatted UUID string are handled gracefully;
    non-UUID strings (e.g. from legacy or test callers) get a fresh UUID so
    that the INSERT never fails on a type mismatch.
    """
    try:
        return uuid.UUID(correlation_id)
    except (ValueError, AttributeError):
        return uuid.uuid4()
