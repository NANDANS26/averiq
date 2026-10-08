# Averiq — Systems Architecture Specification

---

## 1. High-Level Architecture & Layered Hierarchy

```mermaid
graph TD
    Client["Frontend UI<br/>(React + Vite + TypeScript)"] -->|"REST APIs /api/v1"| API["FastAPI Application Layer<br/>(JWT RBAC & Tenant Scoping)"]
    
    subgraph Core Decision Engine
        API --> StateMachine["State Machine (10 States)"]
        StateMachine --> Inference["Decision Intelligence<br/>(Calibrated T-Learner LightGBM)"]
        Inference --> Ranker["Context-Aware Ranker<br/>(Canonical Net Surplus)"]
        Ranker --> Policy["Policy & Governance Engine<br/>(Fraud, Exposure Limits, Cooldown)"]
        Policy --> Approval["Approval Gate<br/>(Operator Review Queue)"]
    end

    subgraph Distributed Orchestration
        Approval --> RedisBroker["Redis 6379<br/>(Celery Broker & Distributed Mutex)"]
        RedisBroker --> CeleryWorkers["Celery Background Workers"]
        CeleryWorkers --> Outbox["Outbox Dispatcher<br/>(Idempotent Gateway Adapters)"]
        Outbox --> Verification["Settlement Verification Engine"]
    end

    subgraph Persistence & Audit
        API --> Postgres[("PostgreSQL 18<br/>(Integer-Paise BigInt Schema)")]
        StateMachine --> Postgres
        CeleryWorkers --> Postgres
        Verification --> Attribution["Canonical Revenue Attribution"]
        Attribution --> Postgres
    end
```

---

## 2. Complete State Machine Lifecycle

```mermaid
stateDiagram-v2
    [*] --> DETECTED: Webhook payment.failed
    DETECTED --> EVALUATED: ML Inference & Feature Extraction
    EVALUATED --> STOPPED: Policy Block or No Positive Surplus
    EVALUATED --> AWAITING_APPROVAL: High-Value Exposure (> threshold)
    EVALUATED --> ACTION_APPROVED: Policy Cleared & Positive Surplus
    
    AWAITING_APPROVAL --> ACTION_APPROVED: Operator Approves
    AWAITING_APPROVAL --> STOPPED: Operator Rejects
    
    ACTION_APPROVED --> EXECUTING: Acquire Redis Mutex Lock
    EXECUTING --> VERIFYING: Outbox Operation Created
    EXECUTING --> FAILED: Gateway 502 / Downstream Error
    
    VERIFYING --> RECOVERED: Payment Verified & Settled
    VERIFYING --> ESCALATED: Max Verification Retries Exhausted (3)
    
    RECOVERED --> [*]
    STOPPED --> [*]
    FAILED --> [*]
    ESCALATED --> [*]
```

---

## 3. Detailed Component Architecture

### 3.1 Decision Intelligence (`backend/ml/`)
- **Action Vocabulary**: `NO_INTERVENTION`, `PAYMENT_LINK`, `RETRY`, `CUSTOMER_ACTION`.
- **Artifact**: `model_artifacts/action_value_v3.pkl` (calibrated action-specific LightGBM regressors).
- **Fallback**: Fallback probability strictly $P_0 = 0.35$ when artifacts are absent.

### 3.2 Canonical Economics (`backend/api/analytics.py`)
- $\text{Gross Recovery} = Y_{\text{selected}} \times \text{Amount}$
- $\text{Organic Recovery} = Y_0 \times \text{Amount}$
- $\text{Incremental Recovery} = (Y_{\text{selected}} - Y_0) \times \text{Amount}$ (unclamped; negative values preserved)
- $\text{Net Incremental Surplus} = \text{Incremental Recovery} - \text{Intervention Cost}$

### 3.3 Storage Layer (`backend/domain/models.py`)
- PostgreSQL 18 with async SQLAlchemy 2.0.
- Check constraints enforce state validity (`chk_cases_state_valid`) and positive amounts (`chk_cases_payment_amount_positive`).
- Monetary values stored strictly as 64-bit integer paise (`BigInteger`).
