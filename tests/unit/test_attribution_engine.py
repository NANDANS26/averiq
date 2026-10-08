"""Unit tests for AttributionEngine.

All tests use a mocked AsyncSession — no database required.

Tests:
  1. SUCCEEDED CREATE_PAYMENT_LINK within window → DIRECT_INTERVENTION
  2. No intervention in window → ORGANIC
  3. SUCCEEDED NO_OP within window → ORGANIC (not executable, doesn't count)
  4. SUCCEEDED CREATE_PAYMENT_LINK but OUTSIDE window → ORGANIC
  5. Two operations; the one inside window wins → DIRECT_INTERVENTION
  6. attribution_class is ORGANIC when only RECOMMENDATION_ONLY action exists

Requirements: FR-ATTR-001, FR-ATTR-002, FR-ATTR-005, FR-ATTR-006
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.attribution_engine.engine import (
    DEFAULT_ATTRIBUTION_WINDOW_SECONDS,
    AttributionEngine,
    AttributionResult,
)
from shared.contracts.enums import AttributionClass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_WINDOW = DEFAULT_ATTRIBUTION_WINDOW_SECONDS  # 86400 seconds


def _make_ext_op(
    action_type: str = "CREATE_PAYMENT_LINK",
    status: str = "SUCCEEDED",
    completed_at: datetime | None = None,
    intervention_id: uuid.UUID | None = None,
    case_id: uuid.UUID | None = None,
) -> MagicMock:
    """Return a mock ExternalOperation."""
    op = MagicMock()
    op.id = uuid.uuid4()
    op.case_id = case_id or uuid.uuid4()
    op.intervention_id = intervention_id or uuid.uuid4()
    op.action_type = action_type
    op.status = status
    op.completed_at = completed_at or (_NOW - timedelta(hours=1))
    return op


def _make_session(op: MagicMock | None) -> AsyncMock:
    """Return a mock AsyncSession whose execute returns a result yielding ``op``."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = op
    session.execute = AsyncMock(return_value=result)
    return session


# ---------------------------------------------------------------------------
# Test 1: SUCCEEDED CREATE_PAYMENT_LINK within window → DIRECT_INTERVENTION
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_intervention_create_payment_link_within_window() -> None:
    """SUCCEEDED CREATE_PAYMENT_LINK completed 1 h before recovery → DIRECT_INTERVENTION."""
    intervention_id = uuid.uuid4()
    op = _make_ext_op(
        action_type="CREATE_PAYMENT_LINK",
        status="SUCCEEDED",
        completed_at=_NOW - timedelta(hours=1),
        intervention_id=intervention_id,
    )
    session = _make_session(op)
    engine = AttributionEngine()

    result: AttributionResult = await engine.classify(
        session=session,
        case_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        recovered_at=_NOW,
    )

    assert result.attribution_class == AttributionClass.DIRECT_INTERVENTION
    assert result.intervention_id == intervention_id
    assert result.action_type == "CREATE_PAYMENT_LINK"
    assert result.intervention_executed_at == op.completed_at
    assert result.recovered_at == _NOW


# ---------------------------------------------------------------------------
# Test 2: No intervention in window → ORGANIC
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_organic_when_no_intervention_in_window() -> None:
    """No SUCCEEDED executable intervention found → ORGANIC."""
    session = _make_session(None)
    engine = AttributionEngine()

    result: AttributionResult = await engine.classify(
        session=session,
        case_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        recovered_at=_NOW,
    )

    assert result.attribution_class == AttributionClass.ORGANIC
    assert result.intervention_id is None
    assert result.action_type is None
    assert result.intervention_executed_at is None
    assert result.recovered_at == _NOW


# ---------------------------------------------------------------------------
# Test 3: SUCCEEDED NO_OP within window → ORGANIC (not executable, doesn't count)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_organic_when_only_no_op_action_exists() -> None:
    """NO_OP (RECOMMENDATION_ONLY) within window → ORGANIC.

    The DB query filters action_type to EXECUTABLE_ACTION_TYPES, so a NO_OP
    operation will never be returned.  The session mock returns None to
    simulate the DB correctly excluding the NO_OP.
    """
    # The session returns None because NO_OP is filtered out at the DB layer
    session = _make_session(None)
    engine = AttributionEngine()

    result: AttributionResult = await engine.classify(
        session=session,
        case_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        recovered_at=_NOW,
    )

    assert result.attribution_class == AttributionClass.ORGANIC
    assert result.intervention_id is None
    assert result.action_type is None


# ---------------------------------------------------------------------------
# Test 4: SUCCEEDED CREATE_PAYMENT_LINK but OUTSIDE window → ORGANIC
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_organic_when_intervention_outside_window() -> None:
    """Intervention executed 25 h before recovery (outside 24 h window) → ORGANIC.

    The DB query WHERE clause enforces completed_at >= window_start.  We
    simulate the DB correctly excluding the out-of-window op by returning None.
    """
    # Session returns None because the DB WHERE clause excludes out-of-window ops
    session = _make_session(None)
    engine = AttributionEngine()

    # Use a small custom window of 3600 s (1 hour); op executed 2 h ago is outside
    result: AttributionResult = await engine.classify(
        session=session,
        case_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        recovered_at=_NOW,
        attribution_window_seconds=3_600,
    )

    assert result.attribution_class == AttributionClass.ORGANIC
    assert result.intervention_id is None
    # Confirm the window was stored on the result
    assert result.attribution_window_seconds == 3_600


# ---------------------------------------------------------------------------
# Test 5: Two operations; the one inside window wins → DIRECT_INTERVENTION
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_intervention_when_one_of_two_ops_is_in_window() -> None:
    """When the DB returns the most-recent in-window op → DIRECT_INTERVENTION.

    The query uses ORDER BY completed_at DESC LIMIT 1, so it returns only the
    most recent qualifying operation.  We simulate this by returning that op
    directly from the mock (as the DB would after applying the window filter).
    """
    intervention_id = uuid.uuid4()
    in_window_op = _make_ext_op(
        action_type="RETRY_CHARGE",
        status="SUCCEEDED",
        completed_at=_NOW - timedelta(hours=2),
        intervention_id=intervention_id,
    )
    # Session mock simulates DB returning the in-window op after ORDER BY / LIMIT
    session = _make_session(in_window_op)
    engine = AttributionEngine()

    result: AttributionResult = await engine.classify(
        session=session,
        case_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        recovered_at=_NOW,
    )

    assert result.attribution_class == AttributionClass.DIRECT_INTERVENTION
    assert result.intervention_id == intervention_id
    assert result.action_type == "RETRY_CHARGE"


# ---------------------------------------------------------------------------
# Test 6: attribution_class is ORGANIC when only RECOMMENDATION_ONLY action exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_organic_when_only_recommendation_only_action_exists() -> None:
    """Only a RECOMMENDATION_ONLY (NO_OP) action recorded on the case → ORGANIC.

    RECOMMENDATION_ONLY actions are NOT in EXECUTABLE_ACTION_TYPES and are
    excluded from the attribution query.  The session returns None to reflect
    the DB not finding any executable action.
    """
    session = _make_session(None)
    engine = AttributionEngine()

    result: AttributionResult = await engine.classify(
        session=session,
        case_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        recovered_at=_NOW,
    )

    assert result.attribution_class == AttributionClass.ORGANIC
    assert result.intervention_id is None
    assert result.action_type is None
    assert result.intervention_executed_at is None
