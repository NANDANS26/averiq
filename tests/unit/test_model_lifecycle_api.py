"""Unit tests for model lifecycle management API.

POST /api/v1/models/{model_id}/promote — promote model through lifecycle stages.

Tests:
  1. Valid trained → evaluated promotion returns 200 with correct states
  2. candidate → active blocked when brier_score > threshold → HTTP 422 with gate_failures
  3. candidate → active succeeds when brier_score ≤ threshold
  4. Promoting non-existent model returns HTTP 404
  5. Invalid transition (retired → *) returns HTTP 422
  6. candidate → active retires previous active model of same type

All tests use mocked AsyncSession — no database required.
All tests are marked @pytest.mark.unit and @pytest.mark.asyncio.

Requirements: FR-MODEL-009, AI-MODEL-001, AI-MODEL-003
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.models import VALID_PROMOTIONS, PromoteResponse, promote_model


# ---------------------------------------------------------------------------
# Helpers — build lightweight mock ModelArtifact objects
# ---------------------------------------------------------------------------


def _make_model(
    lifecycle_state: str = "trained",
    model_type: str = "lightgbm",
    model_version: str = "v1.0.0",
    brier_score: float | None = None,
    model_id: uuid.UUID | None = None,
) -> MagicMock:
    """Return a mock ModelArtifact with the given attributes."""
    model = MagicMock()
    model.id = model_id or uuid.uuid4()
    model.model_version = model_version
    model.model_type = model_type
    model.lifecycle_state = lifecycle_state
    model.brier_score = brier_score
    return model


def _make_session(
    model: Any = None,
    prev_active: Any = None,
) -> AsyncMock:
    """Return a mock AsyncSession.

    - session.get() returns ``model``
    - session.execute() returns a result whose scalar_one_or_none() is ``prev_active``
    """
    session = AsyncMock()

    async def _get(cls: Any, pk: Any) -> Any:
        return model

    session.get = AsyncMock(side_effect=_get)

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = prev_active
    session.execute = AsyncMock(return_value=execute_result)

    return session


def _make_admin_user() -> MagicMock:
    """Return a mock CurrentUser with ADMIN role."""
    user = MagicMock()
    user.role = "ADMIN"
    user.user_id = uuid.uuid4()
    return user


# ---------------------------------------------------------------------------
# Test 1: Valid trained → evaluated promotion returns 200 with correct states
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_valid_trained_to_evaluated_promotion() -> None:
    """Promoting a trained model to evaluated returns 200 with correct state fields.

    Lifecycle transition: trained → evaluated.
    No quality gates apply at this stage.
    Response must include model_id, model_version, model_type, old_state, new_state.

    Requirements: FR-MODEL-009
    """
    model_id = uuid.uuid4()
    model = _make_model(
        lifecycle_state="trained",
        model_type="lightgbm",
        model_version="v1.0.0",
        model_id=model_id,
    )
    session = _make_session(model=model)
    admin_user = _make_admin_user()

    from backend.api.models import PromoteRequest

    body = PromoteRequest(reason="Initial promotion to evaluated")

    result = await promote_model(
        model_id=model_id,
        body=body,
        current_user=admin_user,
        session=session,
    )

    assert isinstance(result, PromoteResponse)
    assert result.model_id == str(model_id)
    assert result.model_version == "v1.0.0"
    assert result.model_type == "lightgbm"
    assert result.old_state == "trained"
    assert result.new_state == "evaluated"
    assert "trained" in result.message
    assert "evaluated" in result.message
    # Verify the model's state was actually updated
    assert model.lifecycle_state == "evaluated"


# ---------------------------------------------------------------------------
# Test 2: candidate → active blocked when brier_score > threshold → HTTP 422
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_candidate_to_active_blocked_when_brier_score_exceeds_threshold() -> None:
    """Quality gate blocks promotion when brier_score > BRIER_SCORE_WARNING_THRESHOLD.

    When a candidate model has a Brier score that exceeds the configured
    threshold, the promote endpoint must:
      - Return HTTP 422
      - Include 'gate_failures' in the response detail
      - Include the gate name 'brier_score'
      - Include actual_value and threshold
      - NOT change the model's lifecycle_state (stays at 'candidate')

    Requirements: FR-MODEL-009, AI-MODEL-001
    """
    from fastapi import HTTPException

    model_id = uuid.uuid4()
    # Brier score well above the default threshold of 0.25
    model = _make_model(
        lifecycle_state="candidate",
        model_type="lightgbm",
        brier_score=0.42,  # > default threshold 0.25
        model_id=model_id,
    )
    session = _make_session(model=model, prev_active=None)
    admin_user = _make_admin_user()

    from backend.api.models import PromoteRequest

    body = PromoteRequest()

    with patch("backend.api.models.settings") as mock_settings:
        mock_settings.BRIER_SCORE_WARNING_THRESHOLD = 0.25

        with pytest.raises(HTTPException) as exc_info:
            await promote_model(
                model_id=model_id,
                body=body,
                current_user=admin_user,
                session=session,
            )

    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert "gate_failures" in detail
    gate_failures = detail["gate_failures"]
    assert len(gate_failures) >= 1

    gate_names = [gf["gate_name"] for gf in gate_failures]
    assert "brier_score" in gate_names

    # Verify the gate failure includes structured data
    brier_gate = next(gf for gf in gate_failures if gf["gate_name"] == "brier_score")
    assert brier_gate["actual_value"] == pytest.approx(0.42)
    assert brier_gate["threshold"] == pytest.approx(0.25)

    # Model must remain at CANDIDATE — no state change on gate failure
    assert model.lifecycle_state == "candidate"


# ---------------------------------------------------------------------------
# Test 3: candidate → active succeeds when brier_score ≤ threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_candidate_to_active_succeeds_when_brier_score_within_threshold() -> None:
    """Promotion to active succeeds when brier_score ≤ threshold.

    When a candidate model's Brier score is at or below the configured
    threshold, quality gates pass and the model is promoted to 'active'.
    No previous active model in this test.

    Requirements: FR-MODEL-009, AI-MODEL-001, AI-MODEL-003
    """
    model_id = uuid.uuid4()
    model = _make_model(
        lifecycle_state="candidate",
        model_type="lightgbm",
        model_version="v2.0.0",
        brier_score=0.20,  # ≤ default threshold 0.25 → gate passes
        model_id=model_id,
    )
    # No previous active model of this type
    session = _make_session(model=model, prev_active=None)
    admin_user = _make_admin_user()

    from backend.api.models import PromoteRequest

    body = PromoteRequest(reason="Evaluation passed Q4 quality gates")

    with patch("backend.api.models.settings") as mock_settings:
        mock_settings.BRIER_SCORE_WARNING_THRESHOLD = 0.25

        result = await promote_model(
            model_id=model_id,
            body=body,
            current_user=admin_user,
            session=session,
        )

    assert isinstance(result, PromoteResponse)
    assert result.old_state == "candidate"
    assert result.new_state == "active"
    assert model.lifecycle_state == "active"


# ---------------------------------------------------------------------------
# Test 4: Promoting non-existent model returns HTTP 404
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_promoting_nonexistent_model_returns_404() -> None:
    """Returns HTTP 404 when the model_id does not exist in the database.

    Requirements: FR-MODEL-009
    """
    from fastapi import HTTPException

    model_id = uuid.uuid4()
    # session.get returns None → model not found
    session = _make_session(model=None)
    admin_user = _make_admin_user()

    from backend.api.models import PromoteRequest

    body = PromoteRequest()

    with pytest.raises(HTTPException) as exc_info:
        await promote_model(
            model_id=model_id,
            body=body,
            current_user=admin_user,
            session=session,
        )

    assert exc_info.value.status_code == 404
    assert str(model_id) in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# Test 5: Invalid transition (retired → *) returns HTTP 422
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_transition_from_retired_state_returns_422() -> None:
    """Returns HTTP 422 when attempting to promote a model in 'retired' state.

    The only valid source states for promotion are: trained, evaluated, candidate.
    'retired' and 'active' are not in VALID_PROMOTIONS and must be rejected.
    The error detail must include the current_state and valid_source_states.

    Requirements: FR-MODEL-009
    """
    from fastapi import HTTPException

    model_id = uuid.uuid4()
    model = _make_model(
        lifecycle_state="retired",
        model_id=model_id,
    )
    session = _make_session(model=model)
    admin_user = _make_admin_user()

    from backend.api.models import PromoteRequest

    body = PromoteRequest()

    with pytest.raises(HTTPException) as exc_info:
        await promote_model(
            model_id=model_id,
            body=body,
            current_user=admin_user,
            session=session,
        )

    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    # The error should clearly state what went wrong
    assert "retired" in str(detail)
    # Valid source states should be listed
    assert "current_state" in detail
    assert detail["current_state"] == "retired"
    # Verify same behaviour for 'active' state (cannot promote beyond active)
    model_active = _make_model(lifecycle_state="active", model_id=uuid.uuid4())
    session_active = _make_session(model=model_active)

    with pytest.raises(HTTPException) as exc_active:
        await promote_model(
            model_id=model_active.id,
            body=body,
            current_user=admin_user,
            session=session_active,
        )

    assert exc_active.value.status_code == 422


# ---------------------------------------------------------------------------
# Test 6: candidate → active retires previous active model of same type
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_activating_new_model_retires_previous_active_model() -> None:
    """On candidate → active promotion, the previous active model is retired.

    AI-MODEL-003: at most one active model per model_type at any time.
    When a new model is activated, the previous active model must transition
    to 'retired'. Artifact and metrics must be retained (lifecycle_state
    updated, no deletion).

    Requirements: AI-MODEL-001, AI-MODEL-003
    """
    model_id = uuid.uuid4()
    new_model = _make_model(
        lifecycle_state="candidate",
        model_type="lightgbm",
        model_version="v3.0.0",
        brier_score=0.18,  # passes gate
        model_id=model_id,
    )

    # Previous active model of the same type with a different ID
    prev_active_id = uuid.uuid4()
    prev_active_model = _make_model(
        lifecycle_state="active",
        model_type="lightgbm",
        model_version="v2.0.0",
        brier_score=0.22,
        model_id=prev_active_id,
    )

    session = _make_session(model=new_model, prev_active=prev_active_model)
    admin_user = _make_admin_user()

    from backend.api.models import PromoteRequest

    body = PromoteRequest(reason="Promoting v3.0.0 to active")

    with patch("backend.api.models.settings") as mock_settings:
        mock_settings.BRIER_SCORE_WARNING_THRESHOLD = 0.25

        result = await promote_model(
            model_id=model_id,
            body=body,
            current_user=admin_user,
            session=session,
        )

    # New model is now active
    assert result.new_state == "active"
    assert new_model.lifecycle_state == "active"

    # Previous active model is now retired (not deleted — artifact retained)
    assert prev_active_model.lifecycle_state == "retired"
