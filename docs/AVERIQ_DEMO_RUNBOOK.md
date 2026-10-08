# Averiq — Operator Demo Runbook & Scenario Guide (Phase 5)

This runbook guides operators, auditors, and stakeholders through executing and inspecting the 8 deterministic demo scenarios in Averiq.

---

## 1. Environment Verification

Before running scenarios, verify system health:

```bash
# 1. Check liveness
curl http://localhost:8000/health

# 2. Check component readiness (PostgreSQL, Redis, Model)
curl http://localhost:8000/readiness

# 3. Check model metadata
curl http://localhost:8000/api/v1/system/model-health
```

Expected response for `/readiness`:
```json
{
  "status": "READY",
  "environment": "development",
  "components": {
    "postgres": {"status": "READY", "latency_ms": 1.2},
    "redis": {"status": "READY", "latency_ms": 0.8},
    "model": {"status": "READY", "detail": "Artifact action-value-v3.0-calibrated verified"}
  }
}
```

---

## 2. Seed Deterministic Scenarios

Run the demo fixture seeder:

```bash
python -c "
import asyncio
from backend.database import async_session_factory
from backend.simulation.demo_fixtures import DemoFixtureManager

async def run():
    mgr = DemoFixtureManager(async_session_factory)
    cases = await mgr.seed_all_scenarios()
    print('Seeded Scenarios:')
    for k, v in cases.items():
        print(f'{k}: {v}')

asyncio.run(run())
"
```

---

## 3. Demo Scenarios Reference

| Scenario ID | Name / Objective | Key Behaviors to Observe |
| :--- | :--- | :--- |
| **DEMO-001** | **Normal Recovery (Golden Path)** | Failed card payment (₹2,500) $\to$ Diagnosed $\to$ Recovery Eligible $\to$ Auto-Approved Payment Link $\to$ Executed $\to$ Reconciled $\to$ `RECOVERED`. |
| **DEMO-002** | **High-Value Human Review** | Failed subscription payment (₹15,000) exceeds ₹10,000 limit $\to$ Routed to `HUMAN_REVIEW` $\to$ Review ticket created in Risk Queue. |
| **DEMO-003** | **Policy Violation / Fraud Block** | Flagged as `FRAUD_SUSPECTED` $\to$ Intervention Ranker selects `STOP` $\to$ Zero execution cost $\to$ `STOPPED`. |
| **DEMO-004** | **Gateway Execution Failure** | Downstream payment link generation returns 502 error $\to$ Idempotent handler captures failure $\to$ `FAILED`. |
| **DEMO-005** | **Verification Retry Exhaustion** | Customer does not complete link across 3 verification polling attempts $\to$ Max attempts exhausted $\to$ `ESCALATED`. |
| **DEMO-006** | **Duplicate Webhook Ingestion** | Duplicate webhook with matching `x-razorpay-event-id` rejected safely $\to$ Deduplication preserved. |
| **DEMO-007** | **Duplicate Execution Race** | Concurrent execution attempt blocked by atomic `action_id` unique constraint $\to$ Zero duplicated charges. |
| **DEMO-008** | **Model Fallback Heuristic** | Model artifact unavailable $\to$ Decision Engine applies empirical base-rate fallback $P_0 = 0.35$ $\to$ Clearly tagged in audit trace. |

---

## 4. Inspecting Case Traces

Fetch the full mathematical explanation for any case:

```bash
curl -H "Authorization: Bearer <TOKEN>" http://localhost:8000/api/v1/cases/<CASE_ID>/trace
```

Inspect the `explainability` node for the baseline $P_0$, preferred action, incremental uplift, and integer paise formula calculation.
