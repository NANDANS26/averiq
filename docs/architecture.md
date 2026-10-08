# Averiq — Architecture

## 1. System Overview

Averiq is a **modular monolith** — a single deployable unit with strictly-enforced
internal module boundaries. All inter-module communication between `backend/` and
`ai/` flows exclusively through `shared/contracts/`.

```
┌──────────────────────────────────────────────────────────────────┐
│                          Frontend (React 18)                      │
│  13 pages · TanStack Query · AuthContext · OperatingModeBanner   │
└──────────────────┬───────────────────────────────────────────────┘
                   │ REST / JSON (Bearer JWT)
┌──────────────────▼───────────────────────────────────────────────┐
│                        FastAPI Backend                            │
│                                                                   │
│  ┌──────────┐  ┌────────────┐  ┌────────────┐  ┌─────────────┐  │
│  │ Webhook  │  │  Decision  │  │  Policy    │  │  Execution  │  │
│  │ Pipeline │  │  Engine    │  │  Engine    │  │  Engine     │  │
│  └────┬─────┘  └─────┬──────┘  └─────┬──────┘  └──────┬──────┘  │
│       │              │                │                 │          │
│  ┌────▼──────────────▼────────────────▼─────────────────▼──────┐  │
│  │            State Machine  (SELECT FOR UPDATE)                 │  │
│  └──────────────────────────┬───────────────────────────────────┘  │
│                             │                                      │
│  ┌──────────────────────────▼───────────────────────────────────┐  │
│  │  PostgreSQL 15  (17 tables, 6 migrations, BIGINT amounts)    │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌──────────────┐  ┌──────────────────────────────────────────┐  │
│  │  Redis 7     │  │  Celery (webhook, decisions, verification │  │
│  │  (broker +   │  │   dead_letters queues + beat scheduler)   │  │
│  │  rate limit) │  └──────────────────────────────────────────┘  │
│  └──────────────┘                                                 │
└──────────────────────────────────────────────────────────────────┘
                   │ shared/contracts/ only
┌──────────────────▼───────────────────────────────────────────────┐
│                        AI Module                                  │
│  StubAIProvider · ExternalLLMProvider · LightGBMModel            │
│  SyntheticDataGenerator · train.py · evaluate.py · simulate.py   │
└──────────────────────────────────────────────────────────────────┘
```

## 2. Module Boundaries (Hard Rules)

| Rule | Rationale |
|------|-----------|
| `ai/` must never import from `backend/` | Prevents circular deps; keeps ML pipeline runnable standalone |
| `backend/` must never import from `ai/` | AI providers injected at startup via `AIProvider` protocol |
| Both use `shared/contracts/` freely | This is the intentional seam |
| No component imports `os.environ` directly | All config via `backend/config.py` `Settings` class |

## 3. Decision Pipeline — Data Flow

```
Razorpay Webhook
      │
      ▼
[1] Webhook Handler (synchronous, 10 steps)
      │  HMAC verify → rate limit → dedup → INSERT webhook_events + outbox_events (atomic)
      ▼
[2] Outbox Beat (every 30 s) — SELECT FOR UPDATE SKIP LOCKED
      │  Publishes Celery task → marks outbox enqueued=True
      ▼
[3] Webhook Processor (Celery worker)
      │  Idempotency guard → correlate/create RevenueRiskCase (OBSERVED) → enqueue decision
      ▼
[4] State Machine: OBSERVED → AT_RISK (SELECT FOR UPDATE, audit-atomic)
      ▼
[5] Root Cause Reasoner
      │  AIProvider.diagnose() with timeout → validate schema → fallback if needed
      ▼
[6] Risk Assessor — compute payment_amount_at_risk, exposure_tier, recovery_risk_tier
      ▼
[7] Recoverability Model — predict(RecoverabilityFeatures) → recovery_probability (float)
      ▼
[8] Intervention Engine — generate candidates → compute EV (Decimal ROUND_FLOOR) → rank
      ▼
[9] Policy Engine — evaluate each candidate against 7 rules → APPROVED / BLOCKED / HUMAN_REVIEW
      ▼
[10] Execution Engine
      │  CanExecuteAction (4 pre-conditions) → create ExternalOperation(PENDING)
      │  → FakeRazorpayGateway.create_payment_link() with asyncio.wait_for timeout
      │  → SUCCEEDED / TIMEOUT (never FAILED on timeout alone)
      ▼
[11] Verification Engine — get_payment_link() + get_payment() → RECOVERED / PARTIAL / TIMEOUT
      ▼
[12] State Machine: → VERIFYING → RECOVERED (or ESCALATED/FAILED)
      ▼
[13] Attribution Engine — DIRECT_INTERVENTION (within window) or ORGANIC
```

## 4. Delivery Model

**At-least-once + idempotent** — NOT exactly-once.

| Component | Idempotency mechanism |
|-----------|----------------------|
| Webhook ingestion | `UNIQUE(merchant_id, provider_event_id)` dedup check |
| Outbox delivery | `enqueued=True` flag; duplicate Celery delivery absorbed by PROCESSED guard |
| Execution engine | `action_id UUID NOT NULL UNIQUE` — generated before external call |
| State machine | `SELECT FOR UPDATE` serializes concurrent workers |
| Recoverability model | Fixed `random_seed`, deterministic feature extraction |

## 5. Database Design Decisions

| Decision | Rationale |
|----------|-----------|
| All amounts as `BIGINT` paise | No float rounding errors for financial arithmetic |
| All timestamps as `TIMESTAMPTZ` | Timezone-aware UTC throughout; no naive datetimes |
| `audit_events` has NO `updated_at` | Append-only invariant enforced at schema level |
| State stored as `VARCHAR CHECK` not `ENUM` | Adding states requires no table rewrite |
| Partial UNIQUE indexes | `idx_cases_active_payment WHERE state NOT IN (terminal)` — efficient uniqueness |
| `SELECT FOR UPDATE` (not SKIP LOCKED) for state machine | Serialization semantics; SKIP LOCKED only for outbox queue |
| Fernet encryption for webhook secrets | Must be retrievable for HMAC; bcrypt is one-way |

## 6. Security Architecture

```
                 ┌─────────────────────────────────────────────┐
                 │          Security Layers                     │
                 │                                             │
Request  ──────► │  1. CORS (ALLOWED_CORS_ORIGINS, no *)       │
                 │  2. JWT verification + revocation check     │
                 │  3. RBAC (require_viewer/operator/admin)    │
                 │  4. Merchant isolation (repository layer)   │
                 │  5. AI output validation (allowlist)        │
                 │  6. Policy Engine gate (mandatory)          │
                 │  7. CanExecuteAction (4 pre-conditions)     │
                 └─────────────────────────────────────────────┘
```

**SEC-AI-003**: LLM output NEVER reaches ExecutionEngine or GatewayAbstraction
without passing through PolicyEngine. This is a structural guarantee enforced
by the call graph, not just documentation.

## 7. State Machine

```
OBSERVED ──► AT_RISK ──► DIAGNOSED ──► RECOVERY_ELIGIBLE
                                              │
                                              ▼
                              ┌──── ACTION_PENDING
                              │           │
                              │    ACTION_APPROVED
                              │           │
                              │    ACTION_EXECUTING
                              │           │
                              │       VERIFYING ──► RETRYABLE ──┐
                              │           │                      │
                              │           ▼                      │
                              └──► RECOVERED (terminal)          │
                                                                 │
All non-terminal states                                          │
can also reach:                                           ◄──────┘
  STOPPED (terminal)
  ESCALATED (terminal)
  FAILED (terminal)
  UNKNOWN_TERMINAL (terminal)
```

Every transition: `UPDATE revenue_risk_cases + INSERT audit_events` in one transaction.
If the audit INSERT fails → entire transaction rolls back → no un-audited state change.

## 8. AI Architecture

```
                    CaseContext (structured, not raw text)
                          │
              ┌───────────▼────────────┐
              │   Root Cause Reasoner  │
              │   asyncio.wait_for     │
              │   (AI_RESPONSE_TIMEOUT)│
              └──────┬────────┬────────┘
                     │        │ timeout / error
                     ▼        ▼
              AIProvider  DeterministicFallback
              (external   (pure in-memory,
               LLM)        < 2 s, zero I/O)
                     │        │
                     ▼        ▼
               DiagnosisOutput  ← Pydantic schema validation (SEC-AI-001)
                     │
                     ▼
             FailureCategory allowlist check (SEC-AI-002)
                     │
                     ▼
               PolicyEngine  ← mandatory gate before any execution (SEC-AI-003)
```

**Prompt injection defense**: static `DIAGNOSIS_SYSTEM_PROMPT` in `"role": "system"`.
All untrusted data (`error_code`, etc.) as structured JSON in `"role": "user"` only.
`customer_id`, `error_description` excluded from LLM input.

## 9. Policy Engine — Rule Evaluation Order

Rules evaluated in this exact documented order. First blocking rule wins.
STOP intervention bypasses all rules (always APPROVED).

```
1. payment_already_recovered  — case already RECOVERED?
2. duplicate_action           — same (case_id, type) executing?
3. confidence_threshold       — recovery_probability < min_confidence?
4. amount_limit               — payment_amount > max_amount[currency]?
5. retry_budget               — attempt_count >= max_retries[type]?
6. cooldown                   — elapsed < cooldown_seconds?
7. fraud_block                — failure_category == FRAUD_SUSPECTED?
8. human_approval             — amount > human_approval_threshold → HUMAN_REVIEW
   ↓
   APPROVED
```

## 10. Frontend Architecture

```
main.tsx
  └── BrowserRouter
       └── QueryClientProvider (TanStack Query v5)
            └── App
                 ├── AuthProvider (JWT state, login/logout)
                 └── OperatingModeProvider (reads X-Operating-Mode header)
                      └── Routes
                           ├── /login → LoginPage (public)
                           └── ProtectedRoute → AppLayout
                                │  └── OperatingModeBanner (always visible)
                                └── 12 protected pages
```

**API layer** — three modules:
- `api/client.ts` — axios instance, Bearer interceptor, error normalizer
- `api/cases.ts` — all case/intervention/analytics endpoints
- `api/policy.ts` — policy CRUD + lifecycle

**No global state management library** — TanStack Query handles server state.
`AuthContext` and `OperatingModeContext` handle the only two pieces of client state.

## 11. Infrastructure

```
docker-compose.yml
├── postgres (postgres:15-alpine)   — health check: pg_isready
├── redis (redis:7-alpine)          — health check: redis-cli ping
├── backend                         — depends_on: postgres+redis (healthy)
├── worker                          — same image, celery worker command
├── beat                            — same image, celery beat command
├── frontend                        — nginx (prod) / Vite (dev override)
└── otel_collector                  — profile: observability only

docker-compose.override.yml (dev only)
├── backend: volume mount ./backend:/app/backend (uvicorn --reload)
├── worker/beat: same source mounts
└── frontend: Vite dev server with HMR, port 5173
```

## 12. Observability

**Structured logging** — every log entry includes:
`correlation_id`, `request_id`, `level`, `timestamp` (UTC ISO), `message`
Plus `case_id`, `merchant_id` when available in context.

**10 named OTel metrics** (NFR-OBS-003):
`webhook_received_total`, `webhook_duplicate_total`, `webhook_missing_event_id_total`,
`decision_latency_seconds`, `model_inference_latency_seconds`, `action_success_total`,
`action_failure_total`, `policy_block_total`, `recovered_revenue_total`, `ai_fallback_total`

All metric labels are low-cardinality — never `case_id`, `customer_id`, `payment_id`.

**PII safety** — `FORBIDDEN_LOG_FIELDS` constant covers: raw webhook payloads,
card numbers, bank account numbers, auth credentials, raw customer PII.
Applied to both structured logs and `audit_events.payload` (AUD-006).

## 13. Key Design Invariants

| Invariant | Enforcement |
|-----------|------------|
| Financial amounts always `int` paise | `BIGINT` schema + `Mapped[int]` ORM + `Decimal ROUND_FLOOR` in EV |
| Audit events always atomic with state change | `session.flush()` includes both UPDATE + INSERT together |
| No un-audited state change | If audit INSERT fails → transaction rolls back |
| LLM output never bypasses PolicyEngine | Structural call graph; DiagnosisOutput has no authorization fields |
| TIMEOUT ≠ FAILED | `external_operations.status` can only reach FAILED after reconciliation |
| At-most-one active policy per merchant | DB partial unique index `idx_policies_active` |
| At-most-one active model per type | DB partial unique index `idx_model_active` |
| Webhook secret retrievable (not hashed) | Fernet encryption (reversible), not bcrypt (one-way) |
| merchant_id always forced in repositories | `MerchantScopedRepository.create()` overrides any supplied value |
| SYSTEM role never assignable via API | `SystemRoleGuard` Pydantic mixin → HTTP 422 |
