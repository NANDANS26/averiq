# Averiq — Phase 4 Production Architecture Reference
## Live Orchestration & Revenue Recovery Execution Pipeline

---

## 1. System Overview

Averiq operates on the core principle:
`AI Proposes → Policy Controls → Infrastructure Executes → Verification Confirms → Metrics Prove`

In Phase 4, the system transitions from offline decision verification into an end-to-end executable asynchronous pipeline backed by:
- **PostgreSQL**: Authoritative relational persistence with row-level locking (`SELECT ... FOR UPDATE`), partial unique indices, and ACID transactions.
- **Redis (Port 6379)**: Distributed task broker and result store for Celery.
- **Celery Workers**: Independent queues for `webhook`, `decisions`, `default` (execution), and `verification`.
- **FastAPI**: REST API layer for webhook ingestion, review actions, case queries, and analytics.

---

## 2. End-to-End Orchestration Workflow

```
[Payment Failure Webhook]
          │
          ▼
   [Webhook Ingestion API]  (HMAC signature validation, Raw payload logged)
          │
          ▼
   [Outbox Event / WebhookEvent] (PostgreSQL commit)
          │
          ▼
   [Celery: webhook queue] (processor.process_webhook_event)
          │
          ▼
   [RevenueRiskCase Created: OBSERVED → AT_RISK]
          │
          ▼
   [Celery: decisions queue] (tasks.run_decision_pipeline)
          │
          ├──> Model Inference (ActionValueEstimator calibrated probabilities)
          ├──> ContextAwareInterventionRanker (Incremental EV in integer paise)
          ├──> PolicyEngine Evaluation (Fraud limits, Amount limits, Cooldown)
          │
          ├──[BLOCKED / STOP] ──> Transition case to STOPPED (Terminal)
          │
          ├──[REQUIRES_HUMAN_APPROVAL] ──> Create HumanReview (OPEN)
          │                                  │
          │                                  ├──> Reviewer APPROVE ──┐
          │                                  └──> Reviewer REJECT ───┼──> STOPPED
          │                                                          │
          └──[APPROVED] ─────────────────────────────────────────────┘
          │
          ▼
   [Celery: default queue] (tasks.execute_intervention)
          │
          ├──> ExecutionEngine Pre-execution Guards (CanExecuteAction)
          ├──> Create ExternalOperation (IN_PROGRESS)
          ├──> Transition case: ACTION_APPROVED → ACTION_EXECUTING
          ├──> Razorpay Gateway Call (Simulated / Test Link creation)
          └──> Transition case: ACTION_EXECUTING → VERIFYING
          │
          ▼
   [Celery: verification queue] (tasks.run_verification)
          │
          ├──> VerificationEngine Query (Gateway link status check)
          ├──> Re-scheduling if unconfirmed (Bounded exponential retry)
          └──> Confirmed Recovery (Outcome == RECOVERED)
          │
          ▼
   [AttributionEngine & Final State]
          │
          ├──> Attribution Window Validation (Within 24h of failure)
          ├──> Incremental Value & Cost Calculation (Integer paise)
          └──> Transition case: VERIFYING → SUCCEEDED (Terminal)
```

---

## 3. Queue Architecture & Celery Task Mapping

| Queue | Task Name | Concurrency / Routing | Acks Late |
|---|---|---|---|
| `webhook` | `backend.webhook_processor.processor.process_webhook_event` | Dedicated queue for processing raw webhook payloads | Yes |
| `decisions` | `backend.decision_engine.tasks.run_decision_pipeline` | Drives ML inference, candidate EV ranking, and policy gates | Yes |
| `default` | `backend.execution_engine.tasks.execute_intervention` | Executes external side-effects (Razorpay payment link creation) | Yes |
| `verification` | `backend.verification_engine.tasks.run_verification` | Queries gateway for payment confirmation and triggers attribution | Yes |

---

## 4. Concurrency & Idempotency Controls

1. **Row-Level Locking:** All state transitions and case mutations acquire `SELECT ... FOR UPDATE` locks on `RevenueRiskCase` and `Payment`.
2. **Partial Unique Indices:** Prevents concurrent duplicate cases for the same active payment:
   `CREATE UNIQUE INDEX idx_cases_active_payment ON revenue_risk_cases (merchant_id, payment_id) WHERE state NOT IN ('SUCCEEDED', 'FAILED', 'STOPPED', 'EXPIRED');`
3. **Execution Guard:** `CanExecuteAction` validates that no concurrent or completed `ExternalOperation` exists for the given action before triggering a gateway side-effect.
4. **Attribution Guard:** Attribution records verify that a case is attributed at most once, and that `incremental_recovery = (Y_selected - Y0) * amount`.
