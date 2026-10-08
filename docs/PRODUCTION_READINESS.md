# Averiq — Production Readiness & Architecture Assessment (Phase 5)

This document certifies the operational readiness of Averiq as an enterprise AI Revenue Recovery Decision Engine.

---

## 1. System Topology & Architecture

The production architecture separates responsibilities across clearly defined system layers:

```
[ Inbound Razorpay Webhook ]
            |
            v
[ Webhook Ingestion API (FastAPI) ]
            |  (Deduplication + Outbox Pattern)
            v
[ PostgreSQL 18 ] <---- Atomic SELECT FOR UPDATE State Machine
            |
            v
[ Celery Distributed Workers (Queues: webhook, decisions, verification, default) ]
            |
            +---> Root Cause Reasoner (AI Failure Classifier)
            +---> T-Learner Action-Value Estimator (Calibrated LightGBM)
            +---> Context-Aware Intervention Ranker (Integer EV Optimization)
            +---> Policy Engine (Deterministic Rule Enforcement & Human Gate)
            +---> Execution Engine (Distributed Idempotent Gateway Actions)
            +---> Verification Engine (Exponential Backoff Reconciliation)
```

---

## 2. Hardened Infrastructure & Operational Metrics

- **Web Server**: FastAPI / Uvicorn with structured async concurrency.
- **Database**: PostgreSQL 18 with asyncpg connection pooling (`NullPool` across process workers) ensuring clean event loop separation.
- **Queue & Broker**: Redis 6379 with Celery late ACKs (`task_acks_late=True`) and unacknowledged worker failure rejection.
- **Observability**: End-to-end correlation ID propagation (`X-Correlation-ID` header -> Celery task context -> Audit events -> Logs).

---

## 3. SLA & Performance Benchmarks

Measured on local hardware smoke benchmarks (`smoke_benchmark.py`):
- `GET /health` & `/status`: **p50 = <0.1ms**, **p95 = <0.1ms**
- `GET /readiness` (Full Postgres + Redis probe): **p50 = 97.2ms**, **p95 = 204.4ms**
- `GET /api/v1/system/model-health`: **p50 = 0.11ms**, **p95 = 0.14ms**
- **Model Inference** (`ActionValueEstimator.predict_action_probabilities`): **p50 = 11.1ms**, **p95 = 21.1ms**

---

## 4. Failure Mode Recovery Matrix

1. **Model Unavailable**:
   - Fallback kicks in deterministically.
   - Assigns empirical population base-rate probability `P0 = 0.35`.
   - Tags decision with `fallback_reason = "ML_MODEL_UNAVAILABLE"` and `model_version = "fallback-heuristic"`.
2. **Gateway API Outage (502 / 504)**:
   - State machine safely transitions case to `FAILED` or schedules retry.
   - No payment duplication or orphaned external transactions.
3. **Verification Timeout / Retry Exhaustion**:
   - Evaluates up to `VERIFICATION_MAX_ATTEMPTS = 3`.
   - Transitions case to `ESCALATED` for human operator triage.
4. **Duplicate Webhook Delivery**:
   - `uq_webhook_merchant_event` unique constraint prevents duplicate ingestion.
   - Celery outbox reconciler ensures exactly-once execution semantics.
