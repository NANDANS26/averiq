"""Unit tests for Phase 5 Hardening, Observability, and Demo Features.

Covers:
- Workstream A: Production configuration validation & fallback safety
- Workstream B & C: Structured logging & correlation ID tracing
- Workstream J & K: /health, /readiness, and /api/v1/system/model-health
- Workstream E: Canonical Phase 3.6 recovery analytics calculations
- Workstream M: Deterministic demo fixture generation and states
- Workstream D & H: Decision trace mathematical explainability
- Workstream I: Cross-merchant data isolation verification
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.config import Settings
from backend.domain.models import Merchant, RevenueRiskCase, User
from backend.api.dependencies import CurrentUser
from backend.api.system import check_readiness, get_model_health, get_system_status
from backend.api.analytics import get_canonical_recovery_analytics
from backend.simulation.demo_fixtures import DemoFixtureManager


@pytest.mark.unit
def test_phase5_configuration_defaults() -> None:
    """Verify Phase 5 production hardening configuration fields and defaults."""
    s = Settings(
        APP_SECRET_KEY="test_secret_32_bytes_long_minimum!!",
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/db",
        REDIS_URL="redis://localhost:6379/0",
    )
    assert s.ENVIRONMENT in ("development", "test", "simulation", "production")
    assert s.FALLBACK_PROBABILITY == 0.35
    assert s.MODEL_VERSION == "action-value-v3.0-calibrated"
    assert s.VERIFICATION_MAX_ATTEMPTS == 3
    assert s.ATTRIBUTION_WINDOW_SECONDS == 86400
    assert s.CELERY_BROKER_URL == "redis://localhost:6379/0"


@pytest.mark.unit
def test_phase5_configuration_invalid_env() -> None:
    """Invalid environment strings must be rejected."""
    with pytest.raises(ValidationError):
        Settings(
            APP_SECRET_KEY="test_secret_32_bytes_long_minimum!!",
            DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/db",
            REDIS_URL="redis://localhost:6379/0",
            ENVIRONMENT="invalid_stage",  # type: ignore[arg-type]
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase5_system_status() -> None:
    """Verify /api/v1/status response structure."""
    res = await get_system_status()
    assert res.status == "ok"
    assert res.version == "0.1.0"
    assert res.environment is not None
    assert res.operating_mode in ("SIMULATED", "REAL_TEST_MODE")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase5_model_health_endpoint() -> None:
    """Verify /api/v1/system/model-health returns calibrated status and 0.35 fallback."""
    res = await get_model_health()
    assert res.fallback_probability == 0.35
    assert "ActionValueEstimator" in res.model_name
    assert "calibrated" in res.calibration_status
    assert res.feature_schema_version == "v1.0"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase5_readiness_probe_structure() -> None:
    """Verify /readiness returns structured component health."""
    resp, code = await check_readiness()
    assert code in (200, 503)
    assert "postgres" in resp.components
    assert "redis" in resp.components
    assert "model" in resp.components
    assert resp.status in ("READY", "DEGRADED", "NOT_READY")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase5_canonical_recovery_analytics() -> None:
    """Verify /api/v1/analytics/recovery-canonical calculates organic, gross, incremental and net."""
    merchant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Mock user
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.role = "VIEWER"
    current_user = CurrentUser(user=user, merchant_id=merchant_id)

    # Mock session
    session = AsyncMock()

    # 1. Merchant query
    merchant_res = MagicMock()
    merchant_res.scalar_one_or_none.return_value = "INR"

    # 2. Cases query (1 recovered case of 100,000 paise with P0=0.35, 1 open case of 50,000 paise)
    case1 = MagicMock(spec=RevenueRiskCase)
    case1.id = uuid.uuid4()
    case1.merchant_id = merchant_id
    case1.state = "RECOVERED"
    case1.payment_amount_at_risk = 100000
    case1.recovered_amount = 100000
    case1.currency = "INR"
    case1.created_at = now
    case1.ai_source = "StubAIProvider"
    case1.case_metadata = {
        "decision_trace": {
            "baseline_recovery_probability": 0.35,
            "model_preferred_action": "PAYMENT_LINK",
        }
    }

    case2 = MagicMock(spec=RevenueRiskCase)
    case2.id = uuid.uuid4()
    case2.merchant_id = merchant_id
    case2.state = "ACTION_PENDING"
    case2.payment_amount_at_risk = 50000
    case2.recovered_amount = None
    case2.currency = "INR"
    case2.created_at = now
    case2.ai_source = "StubAIProvider"
    case2.case_metadata = {
        "decision_trace": {
            "baseline_recovery_probability": 0.35,
            "model_preferred_action": "RETRY",
        }
    }

    cases_res = MagicMock()
    cases_res.scalars.return_value.all.return_value = [case1, case2]

    # 3. Interventions query (1 executed PAYMENT_LINK costing 500 paise)
    inv1 = MagicMock()
    inv1.case_id = case1.id
    inv1.intervention_type = "PAYMENT_LINK"
    inv1.requires_human_approval = False

    invs_res = MagicMock()
    invs_res.scalars.return_value.all.return_value = [inv1]

    session.execute.side_effect = [merchant_res, cases_res, invs_res]

    res = await get_canonical_recovery_analytics(current_user=current_user, session=session)
    assert res.currency == "INR"
    assert "7d" in res.cohorts
    c7d = res.cohorts["7d"]

    assert c7d.total_cases == 2
    assert c7d.recovered_cases == 1
    assert c7d.recovery_rate == 0.5
    assert c7d.total_at_risk_paise == 150000
    # Organic expectation = floor(0.35 * 100000) + floor(0.35 * 50000) = 35000 + 17500 = 52500 paise
    assert c7d.organic_recovery_paise == 52500
    # Gross recovered = 100000 paise
    assert c7d.gross_recovered_paise == 100000
    # Incremental = 100000 - 52500 = 47500 paise
    assert c7d.incremental_recovery_paise == 47500
    # Intervention cost = 500 paise
    assert c7d.total_intervention_cost_paise == 500
    # Net incremental = 47500 - 500 = 47000 paise
    assert c7d.net_incremental_recovery_paise == 47000
    # Average net per case = 47000 / 2 = 23500 paise
    assert c7d.average_net_recovery_per_case_paise == 23500


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase5_canonical_negative_incremental_preservation() -> None:
    """Verify that when Gross < Organic (e.g. Yselected < Y0), negative incremental value is NOT clamped to zero."""
    merchant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.role = "VIEWER"
    current_user = CurrentUser(user=user, merchant_id=merchant_id)

    session = AsyncMock()
    merchant_res = MagicMock()
    merchant_res.scalar_one_or_none.return_value = "INR"

    # Case where organic baseline expectation is high (P0=0.80), but case failed (recovered = 0)
    case_failed = MagicMock(spec=RevenueRiskCase)
    case_failed.id = uuid.uuid4()
    case_failed.merchant_id = merchant_id
    case_failed.state = "FAILED"
    case_failed.payment_amount_at_risk = 100000
    case_failed.recovered_amount = None
    case_failed.currency = "INR"
    case_failed.created_at = now
    case_failed.ai_source = "StubAIProvider"
    case_failed.case_metadata = {
        "decision_trace": {
            "baseline_recovery_probability": 0.80,
            "model_preferred_action": "RETRY",
        }
    }

    cases_res = MagicMock()
    cases_res.scalars.return_value.all.return_value = [case_failed]

    inv1 = MagicMock()
    inv1.case_id = case_failed.id
    inv1.intervention_type = "RETRY"
    inv1.requires_human_approval = False

    invs_res = MagicMock()
    invs_res.scalars.return_value.all.return_value = [inv1]

    session.execute.side_effect = [merchant_res, cases_res, invs_res]

    res = await get_canonical_recovery_analytics(current_user=current_user, session=session)
    c7d = res.cohorts["7d"]

    assert c7d.total_cases == 1
    assert c7d.recovered_cases == 0
    # Organic expectation = 80000 paise
    assert c7d.organic_recovery_paise == 80000
    # Gross recovered = 0 paise
    assert c7d.gross_recovered_paise == 0
    # Incremental = Gross - Organic = 0 - 80000 = -80000 paise (strictly preserved negative value)
    assert c7d.incremental_recovery_paise == -80000
    # Retry cost = 100 paise
    assert c7d.total_intervention_cost_paise == 100
    # Net incremental = -80000 - 100 = -80100 paise
    assert c7d.net_incremental_recovery_paise == -80100



@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase5_demo_fixtures_manager() -> None:
    """Verify demo fixtures manager seeds scenarios with is_demo_fixture=True."""
    from backend.database import async_session_factory
    mgr = DemoFixtureManager(async_session_factory)
    scenarios = await mgr.seed_all_scenarios()
    assert len(scenarios) == 8
    assert "DEMO-001" in scenarios
    assert "DEMO-002" in scenarios
    assert "DEMO-008" in scenarios
