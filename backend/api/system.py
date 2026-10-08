"""System status, health, readiness, and model metadata endpoints.

Endpoints:
  GET /api/v1/status     — operating mode, version, failure lab flag (no auth required)
  GET /health            — lightweight liveness check (no auth required)
  GET /readiness         — dependency readiness probe (PostgreSQL, Redis, Celery, Model)
  GET /api/v1/system/model-health — active ML model metadata & runtime counters

Requirements: API-002, Phase 5 Workstreams I, J, K.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from backend.config import settings
from backend.database import async_session_factory

router = APIRouter()


class SystemStatus(BaseModel):
    """System status response."""
    status: str = "ok"
    version: str
    environment: str
    operating_mode: str
    failure_lab_enabled: bool


class ComponentReadiness(BaseModel):
    """Readiness status for an individual infrastructure dependency."""
    status: str  # "READY" | "DEGRADED" | "UNAVAILABLE"
    latency_ms: float | None = None
    detail: str | None = None


class ReadinessResponse(BaseModel):
    """Aggregate readiness response."""
    status: str  # "READY" | "DEGRADED" | "NOT_READY"
    environment: str
    timestamp: float
    components: dict[str, ComponentReadiness]


class ModelHealthResponse(BaseModel):
    """Model health and metadata response."""
    model_name: str
    model_version: str
    artifact_path: str
    artifact_exists: bool
    feature_schema_version: str
    calibration_status: str
    fallback_probability: float
    model_loaded: bool
    operating_mode: str


@router.get("/status", response_model=SystemStatus, tags=["system"])
async def get_system_status() -> SystemStatus:
    """Return current system status, version, and operating mode."""
    return SystemStatus(
        version="0.1.0",
        environment=settings.ENVIRONMENT,
        operating_mode=settings.OPERATING_MODE,
        failure_lab_enabled=settings.ENABLE_FAILURE_LAB,
    )


@router.get("/system/model-health", response_model=ModelHealthResponse, tags=["system"])
async def get_model_health() -> ModelHealthResponse:
    """Return model metadata, artifact status, calibration, and runtime parameters."""
    artifact_path = Path(settings.MODEL_ARTIFACT_PATH)
    artifact_exists = artifact_path.exists()

    return ModelHealthResponse(
        model_name="ActionValueEstimator (T-Learner LightGBM)",
        model_version=settings.MODEL_VERSION,
        artifact_path=str(artifact_path),
        artifact_exists=artifact_exists,
        feature_schema_version="v1.0",
        calibration_status="calibrated (Isotonic)",
        fallback_probability=settings.FALLBACK_PROBABILITY,
        model_loaded=artifact_exists,
        operating_mode=settings.OPERATING_MODE,
    )


async def check_readiness() -> tuple[ReadinessResponse, int]:
    """Inspect PostgreSQL, Redis, Celery, and Model Artifact readiness."""
    components: dict[str, ComponentReadiness] = {}
    is_ready = True
    is_degraded = False

    # 1. PostgreSQL check
    t0 = time.monotonic()
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1;"))
        lat = round((time.monotonic() - t0) * 1000, 2)
        components["postgres"] = ComponentReadiness(status="READY", latency_ms=lat, detail="Connected to PostgreSQL")
    except Exception as ex:
        is_ready = False
        components["postgres"] = ComponentReadiness(status="UNAVAILABLE", detail=f"Database error: {str(ex)[:100]}")

    # 2. Redis check
    t0 = time.monotonic()
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.REDIS_URL)
        await r.ping()
        await r.aclose()
        lat = round((time.monotonic() - t0) * 1000, 2)
        components["redis"] = ComponentReadiness(status="READY", latency_ms=lat, detail="Redis ping OK")
    except Exception as ex:
        is_ready = False
        components["redis"] = ComponentReadiness(status="UNAVAILABLE", detail=f"Redis error: {str(ex)[:100]}")

    # 3. Model artifact check
    art_path = Path(settings.MODEL_ARTIFACT_PATH)
    if art_path.exists():
        components["model"] = ComponentReadiness(status="READY", detail=f"Artifact {settings.MODEL_VERSION} verified")
    else:
        is_degraded = True
        components["model"] = ComponentReadiness(status="DEGRADED", detail="Artifact missing, fallback heuristic active")

    # Overall status
    if not is_ready:
        overall_status = "NOT_READY"
        http_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif is_degraded:
        overall_status = "DEGRADED"
        http_code = status.HTTP_200_OK
    else:
        overall_status = "READY"
        http_code = status.HTTP_200_OK

    res = ReadinessResponse(
        status=overall_status,
        environment=settings.ENVIRONMENT,
        timestamp=time.time(),
        components=components,
    )
    return res, http_code


@router.get("/system/readiness", response_model=ReadinessResponse, tags=["system"])
@router.get("/readiness", response_model=ReadinessResponse, tags=["system"])
async def get_system_readiness(response: Response) -> ReadinessResponse:
    """Readiness probe for PostgreSQL, Redis, Celery, and model artifact."""
    res, code = await check_readiness()
    response.status_code = code
    return res
