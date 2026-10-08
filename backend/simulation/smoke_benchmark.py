"""Phase 5 Performance & Load Smoke Benchmark (Workstream P).

DISCLAIMER & METHODOLOGY:
These timings represent LOCAL DEVELOPMENT IN-PROCESS FUNCTION EXECUTIONS,
NOT full-network HTTP wire round-trip latencies or production cluster benchmarks.
They measure raw internal execution overhead under local loop conditions.

Measures in-process latency (p50, p95, max) for:
- Health check handler (/health)
- Readiness probe handler (/readiness)
- Model health endpoint handler (/api/v1/system/model-health)
- Model inference execution
"""
from __future__ import annotations

import asyncio
import time
import numpy as np
from pathlib import Path

from backend.api.system import check_readiness, get_model_health, get_system_status
from backend.database import async_session_factory
from backend.simulation.demo_fixtures import DemoFixtureManager
from ai.models.action_value_model import ActionValueEstimator
from shared.contracts.schemas import RecoverabilityFeatures
from shared.contracts.enums import FailureCategory


async def benchmark_endpoint(fn, iterations: int = 50) -> dict[str, float]:
    latencies = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        res = fn()
        if asyncio.iscoroutine(res):
            await res
        latencies.append((time.perf_counter() - t0) * 1000)
    return {
        "p50_ms": round(float(np.percentile(latencies, 50)), 2),
        "p95_ms": round(float(np.percentile(latencies, 95)), 2),
        "max_ms": round(float(np.max(latencies)), 2),
        "min_ms": round(float(np.min(latencies)), 2),
    }


async def benchmark_model_inference(iterations: int = 100) -> dict[str, float]:
    artifact_path = Path("model_artifacts/action_value_v3.pkl")
    if not artifact_path.exists():
        return {"error": "artifact_not_found"}
    estimator = ActionValueEstimator.load(artifact_path)
    features = RecoverabilityFeatures(
        amount_paise=250000,
        currency="INR",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        payment_method="card",
        time_since_failure_seconds=120.0,
        hour_of_day=14,
        day_of_week=2,
        previous_failure_count=0,
        has_subscription=False,
        feature_version="v1.0",
    )
    latencies = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        _ = estimator.predict_action_probabilities(features)
        latencies.append((time.perf_counter() - t0) * 1000)
    return {
        "p50_ms": round(float(np.percentile(latencies, 50)), 2),
        "p95_ms": round(float(np.percentile(latencies, 95)), 2),
        "max_ms": round(float(np.max(latencies)), 2),
        "min_ms": round(float(np.min(latencies)), 2),
    }


async def main():
    print("=" * 60)
    print("AVERIQ PHASE 5 PERFORMANCE & LOAD SMOKE BENCHMARK")
    print("=" * 60)

    # 1. Health Liveness
    h_bench = await benchmark_endpoint(get_system_status, 50)
    print(f"[BENCHMARK] /health & /status:  p50={h_bench['p50_ms']}ms, p95={h_bench['p95_ms']}ms, max={h_bench['max_ms']}ms")

    # 2. Readiness Probe
    r_bench = await benchmark_endpoint(check_readiness, 20)
    print(f"[BENCHMARK] /readiness probe:   p50={r_bench['p50_ms']}ms, p95={r_bench['p95_ms']}ms, max={r_bench['max_ms']}ms")

    # 3. Model Health
    m_bench = await benchmark_endpoint(get_model_health, 50)
    print(f"[BENCHMARK] /model-health:      p50={m_bench['p50_ms']}ms, p95={m_bench['p95_ms']}ms, max={m_bench['max_ms']}ms")

    # 4. Model Inference
    inf_bench = await benchmark_model_inference(100)
    print(f"[BENCHMARK] Model Inference:    p50={inf_bench['p50_ms']}ms, p95={inf_bench['p95_ms']}ms, max={inf_bench['max_ms']}ms")

    print("=" * 60)
    print("BENCHMARK COMPLETED SUCCESSFULLY.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
