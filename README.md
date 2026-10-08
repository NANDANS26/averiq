# Averiq — AI Revenue Recovery Decision Engine

> **A production-hardened simulation and decision platform that identifies at-risk failed payments, estimates the incremental net value of recovery interventions, enforces deterministic policy guardrails, routes decisions through human approval gates, and executes idempotent gateway operations with an auditable decision trail.**

[![Test Suite](https://img.shields.io/badge/pytest-704%20passed-emerald)](#)
[![E2E Verification](https://img.shields.io/badge/E2E%20Matrix-15%2F15%20passed-blue)](#)
[![Economics](https://img.shields.io/badge/Economics-Phase%203.6%20Canonical-purple)](#)
[![Accounting](https://img.shields.io/badge/Monetary-Integer%20Paise%20(BigInt)-indigo)](#)

---

## What Problem Does Averiq Solve?

In digital payments (cards, UPI, recurring mandates), transaction declines cost merchants 2% to 7% of gross merchandise volume. Existing recovery systems rely on:
1. **Blind Retries**: Hammering payment rails without context, leading to card velocity locks, customer churn, and acquirer penalties.
2. **Static Heuristic Rules**: Hardcoded `if/else` logic that cannot predict context-dependent recovery rates and completely ignores **organic baseline recovery**.

Averiq turns recovery into **Context-Aware Decision Intelligence**:
- ML models estimate recovery probabilities for candidate interventions.
- Incremental value is measured against a counterfactual control arm (`NO_INTERVENTION`).
- Actions are ranked by expected **Net Incremental Surplus** after operational costs.
- Safety policies and human-in-the-loop gates protect high-value and high-risk transactions.

---

## Architecture Overview

```
PAYMENT RISK
     ↓
DECISION INTELLIGENCE (T-Learner LightGBM)
     ↓
POLICY & GOVERNANCE (Deterministic Safety Rules)
     ↓
APPROVAL GATE (Human Review Queue)
     ↓
EXECUTION ORCHESTRATION (Redis Mutex & Outbox Tasks)
     ↓
SETTLEMENT VERIFICATION (Asynchronous Gateway Reconciliation)
     ↓
CANONICAL ATTRIBUTION (Integer-Paise Surplus Accounting)
```

### Core Technologies
- **Backend**: FastAPI, Python 3.12, Async SQLAlchemy 2.0, Pydantic v2
- **Data & Message Queues**: PostgreSQL 18, Redis 6379, Celery
- **Decision Engine**: Calibrated T-Learner LightGBM (`model_artifacts/action_value_v3.pkl`)
- **Frontend**: React 18, TypeScript, Vite, Tailwind CSS (Dark-first fintech interface)

---

## Canonical Economics (Phase 3.6 Frozen Standard)

Averiq enforces exact integer-paise financial accounting without floating-point representation:

$$\text{Gross Recovery} = \hat{Y}_{\text{selected}} \times \text{Amount}$$
$$\text{Organic Recovery} = \hat{Y}_0 \times \text{Amount}$$
$$\text{Incremental Recovery} = (\hat{Y}_{\text{selected}} - \hat{Y}_0) \times \text{Amount}$$
$$\text{Net Incremental Surplus} = \text{Incremental Recovery} - \text{Intervention Cost}$$

- **Unclamped Accounting**: If an active intervention arm underperforms the organic control arm ($\hat{Y}_{\text{selected}} < \hat{Y}_0$), negative incremental surplus is preserved honestly.
- **Mandatory Control Arm**: Action `NO_INTERVENTION` models the organic base rate $P_0$. If no action generates positive net incremental value, the engine stops.

---

## Key Engineering Guardrails

| Guardrail | Implementation Invariant |
| :--- | :--- |
| **Monetary Integrity** | 100% integer paise; zero floating point representation (`BigInteger` in PostgreSQL). |
| **Model Fallback** | Unconditional empirical baseline $P_0 = 0.35$ when model artifacts are unavailable. |
| **Idempotency** | Webhook hashes drop duplicates; Redis locks prevent concurrent execution side effects. |
| **CUSTOMER_ACTION** | Classified as `INTERNAL_ACTION`: 0 external operations, 0 gateway verifications. |
| **Policy Separation** | ML models predict; deterministic policy engines hold veto power on fraud and limits. |
| **Multi-Tenancy** | Row-level `merchant_id` binding on all queries; returns 404 on cross-tenant requests. |

---

## Getting Started

### Prerequisites
- Python 3.12+
- Node.js 18+
- PostgreSQL 18 & Redis 6379 running locally

### Running the Backend
```bash
# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Start Celery worker daemon
celery -A backend.worker.celery_app worker --loglevel=info --pool=solo -Q default,webhook,decisions,verification

# Start FastAPI API server
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### Running the Frontend
```bash
cd frontend
npm install
npm run dev
# Dashboard available at http://localhost:5173
```

---

## Verification & Testing Suite

Averiq is verified by an automated regression test suite:

- **Pytest Suite**: **704 passed tests, 0 failures** (`pytest tests/unit/ tests/integration/ -m "not slow" -q`)
- **Authoritative E2E Matrix**: **15 / 15 scenarios passed** (`python -m backend.simulation.full_verification_matrix`)
- **Production Build**: Verified clean React TypeScript build (`npm run build`)

---

---

## Technical Documentation & Case Studies

- [**Documentation Index**](docs/README.md) — Comprehensive directory of architecture, models, and runbooks.
- [**Technical Case Study**](docs/AVERIQ_TECHNICAL_CASE_STUDY.md) — Architectural and systems deep dive.
- [**Architecture Specification**](docs/AVERIQ_ARCHITECTURE.md) — Mermaid diagrams and component breakdowns.
- [**Interview Cheat Sheet**](docs/AVERIQ_INTERVIEW_CHEATSHEET.md) — 20 key technical questions and answers.
- [**5-Minute Demo Script**](docs/AVERIQ_5_MINUTE_DEMO.md) — Step-by-step walkthrough for presentations.
- [**Configuration Specification**](docs/CONFIGURATION.md) — Environment variables and default parameters.
- [**Final Claim Evidence Matrix**](docs/FINAL_CLAIM_EVIDENCE_MATRIX.md) — Comprehensive verification evidence cross-referencing all 704 tests.

---

## Contributing & Security

- [**Contributing Guidelines**](CONTRIBUTING.md) — Architectural invariants, local development, and pull request checklist.
- [**Security Policy**](SECURITY.md) — Vulnerability reporting guidelines and defensive security architecture.

---

## License

License: Not yet specified. Proprietary research and evaluation software. All rights reserved.

---

## Notice
*Averiq is a production-hardened simulation and decision platform designed for technical evaluation and architectural demonstration. It does not process live payment gateway funds.*

