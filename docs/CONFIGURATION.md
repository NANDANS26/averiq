# Averiq — Production Configuration Reference (Phase 5)

This document specifies all application configuration parameters, environment constraints, security validations, and fallback defaults implemented in Averiq Phase 5.

---

## 1. Environment & Operating Modes

| Setting | Type / Allowed Values | Default | Description |
| :--- | :--- | :--- | :--- |
| `ENVIRONMENT` | `development`, `test`, `simulation`, `production` | `development` | Runtime environment. Determines log verbosity and strictness. |
| `OPERATING_MODE` | `SIMULATED`, `REAL_TEST_MODE`, `DEMO_FIXTURE` | `SIMULATED` | Controls whether real Razorpay test endpoints or local mock gateways are used. |
| `APP_SECRET_KEY` | `str` (minimum 32 bytes) | **Required** | Secret key for JWT signing and HMAC webhook verification. |

---

## 2. Database & Broker Connectivity

| Setting | Protocol | Default / Format | Purpose |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | PostgreSQL Async (`asyncpg`) | `postgresql+asyncpg://...` | Authoritative transactional state store (PostgreSQL 18). |
| `DATABASE_SYNC_URL`| PostgreSQL Sync (`psycopg2`) | `postgresql+psycopg2://...` | Synchronous operations and schema migrations. |
| `REDIS_URL` | Redis | `redis://localhost:6379/0` | Cache, idempotency lock store, and queue broker. |
| `CELERY_BROKER_URL`| Kombu / Redis | Defaults to `REDIS_URL` | Distributed task execution queue broker. |
| `CELERY_RESULT_BACKEND`| Redis | Defaults to `REDIS_URL` | Task status and async result storage. |

---

## 3. Decision Intelligence & Model Parameters

| Setting | Type | Value | Enforcement / Fallback Policy |
| :--- | :--- | :--- | :--- |
| `MODEL_ARTIFACT_PATH` | Path | `model_artifacts/action_value_v3.pkl` | Path to trained T-Learner LightGBM model bundle. |
| `MODEL_VERSION` | `str` | `action-value-v3.0-calibrated` | Active calibrated model release identifier. |
| `FALLBACK_PROBABILITY`| `float` | `0.35` | Empirical population base-rate fallback when model is unavailable. |
| `VERIFICATION_MAX_ATTEMPTS` | `int` | `3` | Maximum reconciliation polling attempts before escalation. |
| `ATTRIBUTION_WINDOW_SECONDS`| `int` | `86400` (24h) | Timeframe for attributing downstream recoveries to interventions. |

---

## 4. Policy Guardrails & Economic Limits

| Parameter | Default (Paise) | Default (INR) | Enforcement Rule |
| :--- | :--- | :--- | :--- |
| `human_approval_threshold_minor_units` | `1,000,000` | ₹10,000.00 | Interventions on payments $\ge$ this limit route to `HUMAN_REVIEW`. |
| `max_amount_paise` | `50,000,000` | ₹500,000.00 | Hard maximum ceiling permitted for automated intervention. |
| `cooldown_seconds` | `3,600` | 1 hour | Minimum gap between repeated interventions on the same customer. |
| `max_retries` | `3` | 3 attempts | Maximum automated retries per payment attempt lifecycle. |

---

## 5. Health & Readiness Probes

Averiq provides native cloud-standard liveness and readiness probes:
- `GET /health`: Liveness probe returning service metadata and operational status.
- `GET /readiness`: Comprehensive dependency probe checking:
  1. PostgreSQL (`SELECT 1`)
  2. Redis (`ping()`)
  3. Model artifact existence (`action_value_v3.pkl`)
  Returns HTTP 200 with component latencies (`latency_ms`) or HTTP 503 if critical dependencies are down.
- `GET /api/v1/system/model-health`: Exposes model calibration, version, schema version, and runtime fallback parameters.
