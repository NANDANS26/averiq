"""TIMEOUT reconciliation flow — handles all three outcome cases.

TIMEOUT ≠ FAILED. A timeout means no definitive response was received.
The outcome is UNKNOWN and may be either success or failure.

Never transition to FAILED on timeout alone. Always reconcile first.

Three outcome cases after reconciliation:
  Case A (provider succeeded):
    ReconciliationStrategy returns RECOVERED →
    Update external_operations.status to SUCCEEDED →
    Transition case to VERIFYING →
    Run verification to confirm payment captured → RECOVERED

  Case B (provider failed):
    ReconciliationStrategy returns STILL_FAILED →
    Update external_operations.status to FAILED →
    No second action; transition case accordingly

  Case C (provider still unresolved):
    ReconciliationStrategy returns TIMEOUT or PARTIAL →
    Keep external_operations.status = UNKNOWN →
    Schedule re-reconciliation at configured delay
    NEVER blindly replay the financial action

Block all re-execution via CanExecuteAction in all three cases
until reconciliation determines a definitive outcome.

Crash recovery:
  On worker restart, check external_operations.status.
  TIMEOUT/UNKNOWN → run reconciliation before any further action.
  Never assume a TIMEOUT was a failure without checking the gateway.

Requirements: FR-EXEC-004, FR-EXEC-008, REL-IDEMP-003, REL-FAIL-002, REL-FAIL-003
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.models import AuditEvent, ExternalOperation
from backend.gateway.abstract import GatewayAbstraction
from backend.verification_engine.engine import VerificationEngine
from shared.contracts.enums import VerificationOutcome

logger = structlog.get_logger(__name__)

# Statuses that require reconciliation before any re-execution
STATUSES_REQUIRING_RECONCILIATION: frozenset[str] = frozenset(
    {"TIMEOUT", "UNKNOWN", "IN_PROGRESS"}
)


class ReconciliationOutcome(str, Enum):
    CASE_A_SUCCEEDED = "CASE_A_SUCCEEDED"    # Provider actually succeeded
    CASE_B_FAILED = "CASE_B_FAILED"          # Provider definitely failed
    CASE_C_UNRESOLVED = "CASE_C_UNRESOLVED"  # Still unknown — reschedule


@dataclass(frozen=True)
class ReconciliationResult:
    outcome: ReconciliationOutcome
    verification_outcome: VerificationOutcome
    message: str


class TimeoutReconciliationFlow:
    """Handles all three outcome cases after a TIMEOUT/UNKNOWN external operation.

    CRITICAL: This class NEVER replays the financial action. It only:
    1. Queries the gateway to determine what actually happened.
    2. Updates external_operations.status to the definitive state.
    3. Returns ReconciliationOutcome so the caller can transition the case.
    """

    def __init__(
        self,
        gateway: GatewayAbstraction,
        gateway_timeout_seconds: float = 30.0,
    ) -> None:
        self._verification_engine = VerificationEngine(
            gateway=gateway,
            timeout_seconds=gateway_timeout_seconds,
        )

    async def reconcile(
        self,
        session: AsyncSession,
        external_op: ExternalOperation,
        actor: str,
        correlation_id: str,
    ) -> ReconciliationResult:
        """Reconcile a TIMEOUT/UNKNOWN external operation.

        Steps:
          1. Validate the operation is in a reconcilable state
          2. Query the gateway using the appropriate strategy
          3. Map VerificationOutcome to one of three cases
          4. Update external_op.status accordingly
          5. Write AuditEvent in the same transaction
          6. Return ReconciliationResult for the caller to act on

        Args:
            session:           Open async session with an active transaction.
            external_op:       The ExternalOperation in TIMEOUT/UNKNOWN state.
            actor:             Who/what is triggering reconciliation ("SYSTEM").
            correlation_id:    End-to-end correlation ID for tracing.

        Returns:
            ReconciliationResult indicating which of the three cases applies.
        """
        if external_op.status not in STATUSES_REQUIRING_RECONCILIATION:
            logger.warning(
                "reconciliation.skipped_not_required",
                external_op_id=str(external_op.id),
                status=external_op.status,
            )
            return ReconciliationResult(
                outcome=ReconciliationOutcome.CASE_C_UNRESOLVED,
                verification_outcome=VerificationOutcome.PARTIAL,
                message=f"Status {external_op.status!r} does not require reconciliation",
            )

        # Capture the previous status BEFORE any mutation (for the AuditEvent payload)
        previous_status = external_op.status

        external_op_id = str(external_op.external_operation_id or external_op.id)
        now = datetime.now(timezone.utc)

        logger.info(
            "reconciliation.started",
            external_op_id=str(external_op.id),
            action_type=external_op.action_type,
            current_status=previous_status,
            actor=actor,
        )

        # Query the gateway to determine what actually happened
        verification_outcome = await self._verification_engine.verify(
            action_type=external_op.action_type,
            external_operation_id=external_op_id,
        )

        # Map VerificationOutcome to one of three reconciliation cases
        if verification_outcome == VerificationOutcome.RECOVERED:
            # Case A: provider actually succeeded
            external_op.status = "SUCCEEDED"
            external_op.completed_at = now
            external_op.updated_at = now
            result = ReconciliationResult(
                outcome=ReconciliationOutcome.CASE_A_SUCCEEDED,
                verification_outcome=verification_outcome,
                message="Provider confirmed success — external operation updated to SUCCEEDED",
            )

        elif verification_outcome == VerificationOutcome.STILL_FAILED:
            # Case B: provider definitely failed
            external_op.status = "FAILED"
            external_op.updated_at = now
            result = ReconciliationResult(
                outcome=ReconciliationOutcome.CASE_B_FAILED,
                verification_outcome=verification_outcome,
                message="Provider confirmed failure — external operation updated to FAILED",
            )

        else:
            # Case C: TIMEOUT, PARTIAL, or other unresolved outcome
            # NEVER transition to FAILED — keep as UNKNOWN for re-reconciliation
            # NEVER replay the financial action
            external_op.status = "UNKNOWN"
            external_op.updated_at = now
            result = ReconciliationResult(
                outcome=ReconciliationOutcome.CASE_C_UNRESOLVED,
                verification_outcome=verification_outcome,
                message=(
                    f"Provider outcome still unresolved ({verification_outcome.value}) — "
                    "status set to UNKNOWN; schedule re-reconciliation. "
                    "NEVER replay the financial action until outcome is definitive."
                ),
            )

        # Audit event (same transaction — atomicity invariant)
        try:
            corr_uuid = uuid.UUID(correlation_id)
        except (ValueError, AttributeError):
            corr_uuid = uuid.uuid4()

        audit = AuditEvent(
            id=uuid.uuid4(),
            merchant_id=external_op.merchant_id,
            case_id=external_op.case_id,
            actor=actor,
            action_type="timeout_reconciliation",
            affected_entity_type="ExternalOperation",
            affected_entity_id=external_op.id,
            correlation_id=corr_uuid,
            payload={
                "reconciliation_outcome": result.outcome.value,
                "verification_outcome": verification_outcome.value,
                "message": result.message,
                # previous_status captured BEFORE mutation — records the state
                # at the time reconciliation was triggered (AUD-003)
                "previous_status": previous_status,
                # NEVER log the raw gateway response body
            },
            created_at=now,
        )
        session.add(audit)
        await session.flush()

        logger.info(
            "reconciliation.completed",
            external_op_id=str(external_op.id),
            outcome=result.outcome.value,
            new_status=external_op.status,
        )

        return result
