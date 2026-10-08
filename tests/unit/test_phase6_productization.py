"""Unit tests for Phase 6 productization, demo endpoints, and UI contracts.

Tests:
- Demo scenarios metadata endpoint contract
- Demo scenario execution triggers
- System health readiness contract
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock
import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app
from backend.api.dependencies import CurrentUser, get_current_user
from backend.domain.models import User


def _make_mock_user(merchant_id: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")) -> CurrentUser:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.role = "ADMIN"
    return CurrentUser(user=user, merchant_id=merchant_id)


@pytest.fixture
async def demo_client():
    # Override authentication dependency cleanly
    app.dependency_overrides[get_current_user] = lambda: _make_mock_user()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_demo_scenarios_list(demo_client: AsyncClient):
    """GET /api/v1/demo/scenarios returns all 8 deterministic demo scenarios."""
    resp = await demo_client.get("/api/v1/demo/scenarios")
    assert resp.status_code == 200
    scenarios = resp.json()
    assert len(scenarios) == 8
    codes = {s["code"] for s in scenarios}
    assert "DEMO-001" in codes
    assert "DEMO-008" in codes


@pytest.mark.asyncio
async def test_demo_scenario_run_golden_path(demo_client: AsyncClient):
    """POST /api/v1/demo/run/demo-001 seeds DEMO-001 and returns case ID."""
    resp = await demo_client.post("/api/v1/demo/run/demo-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scenario_id"] == "demo-001"
    assert data["expected_final_state"] == "RECOVERED"
    assert "case_id" in data


@pytest.mark.asyncio
async def test_demo_cases_list(demo_client: AsyncClient):
    """GET /api/v1/demo/cases returns cases flagged with is_demo_fixture=True."""
    resp = await demo_client.get("/api/v1/demo/cases")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)


@pytest.mark.asyncio
async def test_system_health_endpoints(demo_client: AsyncClient):
    """Readiness and model health endpoints return valid status."""
    r_resp = await demo_client.get("/readiness")
    assert r_resp.status_code == 200
    r_data = r_resp.json()
    assert r_data["status"] in ("READY", "DEGRADED")
    assert "postgres" in r_data["components"]
    assert "redis" in r_data["components"]

    m_resp = await demo_client.get("/api/v1/system/model-health")
    assert m_resp.status_code == 200
    m_data = m_resp.json()
    assert m_data["fallback_probability"] == 0.35
    assert m_data["model_loaded"] is True
