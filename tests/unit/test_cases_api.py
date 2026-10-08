"""Unit tests for core case and intervention endpoints (task 16.1).

All tests are pure unit tests — no database, no Redis, no network calls.
Session and authentication dependencies are mocked with AsyncMock / MagicMock.

Tests:
  1. list_cases returns CursorPage with integer payment_amount_at_risk (DATA-001)
  2. get_case returns HTTP 404 when case not found in merchant scope (SEC-ISOL-002)
  3. get_case_audit response items do NOT have a ``payload`` attribute (AUD-006)
  4. get_case_interventions returns list scoped to merchant (SEC-ISOL-001)
  5. get_intervention returns HTTP 404 for cross-merchant access (SEC-ISOL-002)

Requirements: FR-STATE-001, AUD-002, AUD-006, API-001, API-002, SEC-ISOL-001, SEC-ISOL-002
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.api.cases import (
    AuditEventItem,
    CursorPage,
    get_case,
    get_case_audit,
    get_case_interventions,
    get_intervention,
    list_cases,
)
from backend.api.dependencies import CurrentUser
from backend.domain.models import AuditEvent, Intervention, RevenueRiskCase, User

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MERCHANT_ID = uuid.uuid4()
_OTHER_MERCHANT_ID = uuid.uuid4()
_NOW = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _make_current_user(merchant_id: uuid.UUID = _MERCHANT_ID) -> CurrentUser:
    """Return a VIEWER-role CurrentUser scoped to the given merchant."""
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.role = "VIEWER"
    return CurrentUser(user=user, merchant_id=merchant_id)


def _make_case(
    merchant_id: uuid.UUID = _MERCHANT_ID,
    payment_amount_at_risk: int = 500_00,  # 500 INR in paise
) -> MagicMock:
    """Build a minimal RevenueRiskCase mock with realistic field values."""
    c = MagicMock(spec=RevenueRiskCase)
    c.id = uuid.uuid4()
    c.merchant_id = merchant_id
    c.payment_id = uuid.uuid4()
    c.state = "AT_RISK"
    c.payment_amount_at_risk = payment_amount_at_risk
    c.currency = "INR"
    c.projected_subscription_revenue_at_risk = None
    c.risk_calculation_path = "SINGLE_PAYMENT"
    c.exposure_tier = "HIGH"
    c.recovery_risk_tier = None
    c.failure_category = "INSUFFICIENT_FUNDS"
    c.diagnosis_confidence = None
    c.ai_source = None
    c.operating_mode = "SIMULATED"
    c.is_demo_fixture = False
    c.created_at = _NOW
    c.updated_at = _NOW
    return c


def _make_audit_event(
    case_id: uuid.UUID | None = None,
    merchant_id: uuid.UUID = _MERCHANT_ID,
) -> MagicMock:
    """Build a minimal AuditEvent mock.  Intentionally has NO ``payload`` attribute
    on the AuditEventItem response model (AUD-006).
    """
    e = MagicMock(spec=AuditEvent)
    e.id = uuid.uuid4()
    e.merchant_id = merchant_id
    e.case_id = case_id or uuid.uuid4()
    e.actor = "SYSTEM"
    e.action_type = "STATE_TRANSITION"
    e.from_state = "OBSERVED"
    e.to_state = "AT_RISK"
    e.policy_version = None
    e.model_version = None
    e.correlation_id = uuid.uuid4()
    e.created_at = _NOW
    return e


def _make_intervention(
    case_id: uuid.UUID | None = None,
    merchant_id: uuid.UUID = _MERCHANT_ID,
) -> MagicMock:
    """Build a minimal Intervention mock."""
    i = MagicMock(spec=Intervention)
    i.id = uuid.uuid4()
    i.case_id = case_id or uuid.uuid4()
    i.merchant_id = merchant_id
    i.intervention_type = "PAYMENT_LINK"
    i.execution_capability = "EXECUTABLE"
    i.provider_support_status = "VERIFIED"
    i.requires_human_approval = False
    i.expected_recovery_value = 45000
    i.verification_strategy = "payment_link_captured"
    i.policy_decision = "APPROVED"
    i.execution_status = "NOT_STARTED"
    i.recommendation_text = None
    i.created_at = _NOW
    i.updated_at = _NOW
    return i


def _make_scalars_result(rows: list) -> MagicMock:
    """Build an execute() result whose .scalars().all() returns rows."""
    scalars = MagicMock()
    scalars.all.return_value = rows
    result = MagicMock()
    result.scalars.return_value = scalars
    result.scalar_one_or_none.return_value = rows[0] if rows else None
    return result


def _make_session_with_cases(cases: list) -> AsyncMock:
    """Build a mock session that returns cases from execute()."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_make_scalars_result(cases))
    return session


# ---------------------------------------------------------------------------
# Test 1: list_cases returns CursorPage with integer payment_amount_at_risk
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_cases_returns_cursor_page_with_integer_amounts() -> None:
    """list_cases must return a CursorPage where payment_amount_at_risk is int.

    Validates:
      - Response is a CursorPage instance
      - items is a list of CaseListItem dicts / objects
      - payment_amount_at_risk is a Python int, never float (DATA-001)
      - next_cursor is None when result count <= limit
      - total is None (cursor pages do not emit count queries)

    Requirements: FR-STATE-001, DATA-001
    """
    case1 = _make_case(payment_amount_at_risk=100_000)
    case2 = _make_case(payment_amount_at_risk=250_000)
    session = _make_session_with_cases([case1, case2])
    current_user = _make_current_user()

    result = await list_cases(
        current_user=current_user,
        session=session,
        state=None,
        exposure_tier=None,
        limit=50,
        cursor=None,
    )

    assert isinstance(result, CursorPage)
    assert len(result.items) == 2
    assert result.total is None  # cursor pages never emit counts
    assert result.next_cursor is None  # only 2 items, limit=50 → no next page

    for item in result.items:
        assert isinstance(item.payment_amount_at_risk, int), (
            f"payment_amount_at_risk must be int, got {type(item.payment_amount_at_risk)}"
        )

    amounts = {item.payment_amount_at_risk for item in result.items}
    assert amounts == {100_000, 250_000}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_cases_sets_next_cursor_when_more_results_exist() -> None:
    """list_cases next_cursor is set when the DB returns limit+1 rows.

    When the DB returns exactly limit+1 rows, the endpoint trims to limit and
    sets next_cursor to the last item's id — indicating another page exists.
    """
    limit = 3
    # Return limit+1 rows to signal there is a next page
    cases = [_make_case() for _ in range(limit + 1)]
    session = _make_session_with_cases(cases)
    current_user = _make_current_user()

    result = await list_cases(
        current_user=current_user,
        session=session,
        state=None,
        exposure_tier=None,
        limit=limit,
        cursor=None,
    )

    assert len(result.items) == limit  # trimmed to limit
    assert result.next_cursor is not None
    assert result.next_cursor == str(cases[limit - 1].id)


# ---------------------------------------------------------------------------
# Test 2: get_case returns HTTP 404 when case not found in merchant scope
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_case_returns_404_when_not_found_in_merchant_scope() -> None:
    """get_case must return HTTP 404 when the case belongs to a different merchant.

    This prevents merchant A from discovering merchant B's case IDs (SEC-ISOL-002).
    The same 404 response shape is used for "not found" and "wrong merchant"
    to avoid enumeration attacks.

    Requirements: SEC-ISOL-002, API-002
    """
    # DB returns no rows — case exists for another merchant, not this one
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_make_scalars_result([]))

    # Authenticate as merchant A but request case belonging to merchant B
    current_user = _make_current_user(merchant_id=_MERCHANT_ID)
    wrong_case_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc_info:
        await get_case(
            case_id=wrong_case_id,
            current_user=current_user,
            session=session,
        )

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Test 3: get_case_audit response items do NOT have a 'payload' attribute
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_case_audit_items_do_not_expose_payload() -> None:
    """AuditEventItem must NOT include a 'payload' field (AUD-006).

    The AuditEvent ORM model has a ``payload`` JSONB column that may contain
    internal decision context.  The API response model (AuditEventItem) must
    intentionally omit this field so that raw gateway payloads, PII, and
    other sensitive data are never exposed to API consumers.

    Requirements: AUD-006
    """
    case_id = uuid.uuid4()
    case_mock = _make_case()
    case_mock.id = case_id
    audit_event = _make_audit_event(case_id=case_id)

    # First execute call: the _get_case_scoped guard
    # Second execute call: the audit events query
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _make_scalars_result([case_mock]),  # case scope check
            _make_scalars_result([audit_event]),  # audit events
        ]
    )
    current_user = _make_current_user()

    items = await get_case_audit(
        case_id=case_id,
        current_user=current_user,
        session=session,
        limit=100,
    )

    assert len(items) == 1
    audit_item = items[0]

    # Core assertion: AuditEventItem must NOT have a payload attribute
    assert not hasattr(audit_item, "payload"), (
        "AuditEventItem must NOT expose 'payload' — raw gateway data must be "
        "suppressed (AUD-006)"
    )

    # Verify safe fields ARE present
    assert isinstance(audit_item, AuditEventItem)
    assert audit_item.actor == "SYSTEM"
    assert audit_item.action_type == "STATE_TRANSITION"
    assert audit_item.from_state == "OBSERVED"
    assert audit_item.to_state == "AT_RISK"
    assert audit_item.correlation_id == str(audit_event.correlation_id)


# ---------------------------------------------------------------------------
# Test 4: get_case_interventions returns list scoped to merchant
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_case_interventions_scoped_to_merchant() -> None:
    """get_case_interventions returns only this merchant's interventions.

    The endpoint first calls _get_case_scoped (which enforces merchant isolation
    on the case), then queries interventions scoped by (case_id, merchant_id).
    This test verifies that the returned intervention data is correct and
    belongs to the authenticated merchant.

    Requirements: SEC-ISOL-001, API-002
    """
    case_id = uuid.uuid4()
    case_mock = _make_case()
    case_mock.id = case_id

    intervention = _make_intervention(case_id=case_id, merchant_id=_MERCHANT_ID)

    # Two execute calls: case scope check, then intervention list
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _make_scalars_result([case_mock]),      # _get_case_scoped
            _make_scalars_result([intervention]),   # intervention list query
        ]
    )
    current_user = _make_current_user(merchant_id=_MERCHANT_ID)

    result = await get_case_interventions(
        case_id=case_id,
        current_user=current_user,
        session=session,
    )

    assert isinstance(result, list)
    assert len(result) == 1

    item = result[0]
    assert item["id"] == str(intervention.id)
    assert item["case_id"] == str(case_id)
    assert item["intervention_type"] == "PAYMENT_LINK"
    assert item["policy_decision"] == "APPROVED"
    assert item["execution_status"] == "NOT_STARTED"
    # Merchant ID must NOT leak into the response
    assert "merchant_id" not in item


# ---------------------------------------------------------------------------
# Test 5: get_intervention returns HTTP 404 for cross-merchant access
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_intervention_returns_404_for_cross_merchant_access() -> None:
    """get_intervention returns HTTP 404 when intervention belongs to another merchant.

    Merchant A must not be able to read merchant B's interventions.
    The query includes a merchant_id filter, so cross-merchant lookups return
    no rows, and the endpoint responds with 404 (consistent response shape
    to prevent resource enumeration — SEC-ISOL-002).

    Requirements: SEC-ISOL-002
    """
    # DB returns no rows — the merchant_id filter rejects the cross-merchant access
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_make_scalars_result([]))

    # Authenticate as merchant A, but the target intervention belongs to merchant B
    current_user = _make_current_user(merchant_id=_MERCHANT_ID)
    other_merchant_intervention_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc_info:
        await get_intervention(
            intervention_id=other_merchant_intervention_id,
            current_user=current_user,
            session=session,
        )

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()
