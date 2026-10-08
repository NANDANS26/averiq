# Averiq — Final Defense & Interview Guide
**How to Defend Averiq Under Hostile Razorpay / Fintech Questioning**

---

## 1. The 5-Minute Technical Defense Narrative

### 0:00 – The Problem (30s)
> "In digital payments, failure recovery is usually handled either through blind retries—which burn card brand velocity limits and trigger fraud alarms—or static if/else rules based on gateway decline strings. Both approaches ignore **organic counterfactual recovery**: a significant portion of customers will notice a decline and complete the payment on their own without merchant intervention."

### 0:30 – Why Existing Systems Fail (30s)
> "If a customer has an 80% chance of recovering organically ($P_0 = 0.80$), and an automated payment link achieves an 82% recovery rate ($P_a = 0.82$), the **incremental uplift is only 2%**. Spending ₹5 on SMS/WhatsApp notifications and gateway link fees for a 2% lift is economically irrational. Traditional heuristic systems intervene blindly, eroding margin. We built Averiq to solve this through decision intelligence."

### 1:00 – The Averiq Architecture (30s)
> "Averiq is structured as a strict pipeline:
> `Ingestion` $\to$ `Decision Intelligence` $\to$ `Policy Guardrails` $\to$ `Approval Gate` $\to$ `Idempotent Execution` $\to$ `Asynchronous Verification` $\to$ `Canonical Revenue Attribution`.
> We decouple machine learning from policy: ML models propose and rank; deterministic policy engines hold absolute veto power."

### 1:30 – Decision Intelligence & T-Learner (30s)
> "Rather than a naive multi-class model predicting past merchant behavior, we use a calibrated **T-Learner** built on separate LightGBM estimators for four discrete treatments: `NO_INTERVENTION` (organic control), `PAYMENT_LINK`, `RETRY`, and `CUSTOMER_ACTION`. Each model is calibrated using Isotonic Regression on a non-overlapping temporal validation split, ensuring our predicted probabilities reflect genuine risk."

### 2:00 – Canonical Counterfactual Economics (30s)
> "We measure value under Phase 3.6 canonical counterfactual definitions:
> - $\text{Gross Value} = P_{\text{selected}} \times \text{Amount}$
> - $\text{Organic Value} = P_0 \times \text{Amount}$
> - $\text{Incremental Value} = (P_{\text{selected}} - P_0) \times \text{Amount}$
> - $\text{Net Incremental Surplus} = \text{Incremental Value} - \text{Cost}$
> Every calculation is conducted strictly in **integer paise** using 64-bit BigInt storage. We never clamp negative numbers: if an intervention underperforms organic baseline, we record the negative economic impact honestly."

### 2:30 – Safety, Policy & High-Value Governance (30s)
> "Our policy engine enforces non-negotiable guardrails: fraud detection, merchant cooldown periods (3,600s), and exposure limits. High-value transactions (₹10,000+) automatically halt in `AWAITING_APPROVAL` for human review before any external gateway operation can execute. Terminal `STOP` actions always bypass policy to guarantee safe termination."

### 3:00 – Distributed Execution & Idempotency (30s)
> "In payments, double execution is catastrophic. We enforce three layers of defense:
> 1. Cryptographic webhook hashing drops duplicate deliveries.
> 2. Redis distributed mutexes (`lock:execution:{case_id}`) block concurrent workers.
> 3. PostgreSQL `SELECT ... FOR UPDATE` row locks serialize state machine updates.
> Crucially, `CUSTOMER_ACTION` is classified as an `INTERNAL_ACTION`—it triggers 0 external gateway calls and 0 verification polling tasks."

### 3:30 – Asynchronous Verification & Attribution (30s)
> "Executing an action does not mean money was recovered. After link creation, the case enters `VERIFYING`. Celery tasks perform bounded asynchronous verification. If confirmed via gateway, revenue surplus is attributed. If unconfirmed after 3 bounded retries, the case transitions to `ESCALATED`."

### 4:00 – Observability & Auditability (30s)
> "Every state change produces an append-only `AuditEvent` record with correlation IDs linked across API, Celery, and database contexts. Per rule `AUD-006`, raw gateway payloads, PANs, and secrets are strictly redacted from audit API projections."

### 4:30 – Empirical Evidence & Test Suite (30s)
> "Averiq is verified by:
> - **704 passing automated tests** across unit and integration suites.
> - **15 / 15 scenarios passed** in our authoritative Phase 4 E2E state machine matrix.
> - A verified clean React TypeScript production build.
> - Strict empirical fallback $P_0 = 0.35$ guaranteeing 100% uptime during model outages."

---

## 2. Top 30 Interview Attack Questions & Answers

### A. Machine Learning Questions

#### Q1: "Why did you use a T-Learner instead of an S-Learner or X-Learner?"
- **Why They Ask**: To check if you understand meta-learners in causal inference.
- **Strong Answer**: "An S-Learner includes treatment as a single feature, which often shrinks treatment effect towards zero if the feature space is high-dimensional. An X-Learner is optimal when treatment and control sample sizes are severely imbalanced. We chose a T-Learner because we had balanced synthetic intervention cohorts and wanted completely independent estimators to avoid cross-arm regularization bias."
- **What NOT to say**: *"T-Learner was just the easiest to implement."*

#### Q2: "Why LightGBM instead of deep neural networks?"
- **Strong Answer**: "Payment failure data is predominantly tabular (categorical error codes, failure codes, merchant IDs, attempt counts, transaction amounts). Gradient-boosted decision trees consistently outperform neural networks on tabular data in training efficiency, inference latency (~11ms vs 80ms+), and native support for categorical splits without massive embedding layers."

#### Q3: "Why did you calibrate probabilities using Isotonic Regression?"
- **Strong Answer**: "Raw tree-based model outputs do not represent true posterior probabilities; they group predictions near leaf averages. Because our ranking engine multiplies probability by financial amount ($\text{Paise} = P \times \text{Amount}$), miscalibrated probabilities distort expected rupee values. Isotonic regression non-parametrically calibrates scores without assuming sigmoid shapes."

#### Q4: "Is this genuine causal inference?"
- **Strong Answer**: "In this simulation platform, yes, because potential outcomes were generated under known, unconfounded mechanisms with explicit treatment assignment. In live production observational data, however, merchant policies create severe selection bias (confounding). Moving to live production would require propensity score re-weighting, doubly robust estimation, or randomized exploration budgets."

---

### B. Payments & Fintech Questions

#### Q5: "Why can't you just retry the payment 10 times until it succeeds?"
- **Strong Answer**: "In card networks (Visa/Mastercard), excessive retries trigger **acquirer decline fees**, burn card brand velocity thresholds, and trigger issuing bank fraud blocks. Furthermore, retrying an `EXPIRED_CARD` or `AUTHENTICATION_FAILURE` has a 0% recovery probability while eroding customer trust."

#### Q6: "Why is `CUSTOMER_ACTION` an internal action?"
- **Strong Answer**: "When an issuing bank declines a transaction for `AUTHENTICATION_FAILURE` or `EXPIRED_CARD`, the merchant cannot charge the card on the gateway. The only recovery path is asking the customer to log in and update their billing details. Calling an external gateway API in this state would be invalid, so `CUSTOMER_ACTION` is classified as `INTERNAL_ACTION` with 0 gateway calls."

#### Q7: "What is the difference between authorization, capture, and settlement?"
- **Strong Answer**: "Authorization holds funds on the customer's card. Capture commits the transaction for settlement. Settlement is the actual interbank transfer of funds (T+2 in India). Averiq verifies that a payment link has reached `captured` or `settled` status before declaring a case `RECOVERED`."

---

### C. Distributed Systems & Reliability Questions

#### Q8: "What happens if a worker crashes right after creating a payment link on Razorpay?"
- **Strong Answer**: "This is the classic dual-write problem. Our mitigation is client-side idempotency: when creating the link, we pass `reference_id = str(case.id)`. If the worker crashes before committing the database record and another worker retries, Razorpay recognizes the existing `reference_id` and idempotently returns the original payment link rather than charging twice."

#### Q9: "Why combine Redis mutexes with PostgreSQL row locks?"
- **Strong Answer**: "Redis distributed mutexes provide fast, in-memory admission control at the Celery task layer to stop duplicate execution tasks before they touch the database. PostgreSQL `SELECT ... FOR UPDATE` provides ACID transactional serialization inside the database to guarantee that state transitions and audit logging execute atomically."

#### Q10: "Why is verification polling bounded at 3 attempts?"
- **Strong Answer**: "Unbounded polling wastes worker capacity and creates queue backlog. After 3 attempts across the attribution window, unresolved cases transition to `ESCALATED`, routing them to an operator review queue rather than leaking resources."

---

## 3. Engineering Decisions: "Why Did You Build It This Way?"

| Architectural Decision | Technical Rationale |
| :--- | :--- |
| **FastAPI + Python 3.12** | Native async I/O, Pydantic v2 type safety, high developer velocity, and direct integration with ML inference libraries (`lightgbm`, `numpy`, `scikit-learn`). |
| **PostgreSQL 18** | ACID transaction guarantees, check constraints for state machine enforcement, row-level locking (`FOR UPDATE`), and native JSONB indexing for audit events. |
| **Redis + Celery** | Decouples fast HTTP webhook ingestion (<10ms) from slow external gateway calls and asynchronous polling tasks. |
| **Integer Paise (BigInt)** | Eliminates IEEE 754 floating-point rounding errors in financial balances. |
| **Fallback P0 = 0.35** | Empirical base rate ensures 100% transaction ingestion uptime during model artifact outages. |
