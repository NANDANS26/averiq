# Averiq — Final Claim vs. Evidence Matrix
**Authoritative Forensic Audit & Claim Truth Verification**
**Auditor:** Razorpay-Style Principal Infrastructure / ML / Payments Engineering Review

---

## Confidence Legend

- **PROVEN**: Verified by automated deterministic tests, check constraints, or database query evidence on disk.
- **PARTIALLY PROVEN**: Logic exists and functions, but relies on in-memory mocks, simulation scaffolding, or bounded synthetic fixtures.
- **SIMULATED**: The mechanism is explicitly modeled via simulation components (e.g. `FakeRazorpayGateway`). Does not connect to live commercial payment rails.
- **UNPROVEN**: Claimed in documentation or presentations without reproducible code or empirical test output.

---

## 1. Machine Learning & Decision Intelligence

| # | System Claim | Implementation Location | Test Verification | Empirical Finding | Audit Confidence | Safe Interview Wording |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | **Action-Specific Modeling (T-Learner)** | `ai/models/action_value_model.py` (`ActionValueEstimator`) | `tests/unit/test_action_value_model.py` | Separate LightGBM estimators fit on 4 discrete treatments (`NO_INTERVENTION`, `PAYMENT_LINK`, `RETRY`, `CUSTOMER_ACTION`). | **PROVEN** | "Implemented a T-Learner architecture using distinct LightGBM classifiers for each intervention arm to model potential recovery outcomes." |
| **2** | **Probability Calibration** | `ai/models/action_value_model.py` | `tests/unit/test_calibrated_lgbm.py` | Isotonic Regression fitted on non-overlapping validation split. Brier score gate enforced. | **PROVEN** | "Calibrated action-value probabilities using Isotonic Regression on held-out temporal validation splits to ensure risk estimates are statistically calibrated." |
| **3** | **Causal Uplift Inference** | `ai/evaluation/phase3_economic_evaluation.py` | `tests/unit/test_phase3_economics.py` | Differences $\Delta P = P_a - P_0$ are conditional expectation differences in synthetic data with known propensity scores. | **PARTIALLY PROVEN** | "Computes expected incremental lift relative to a modeled organic control arm under unconfounded synthetic simulation, rather than natural observational causal identification." |
| **4** | **Model Offline Fallback (P0 = 0.35)** | `backend/decision_engine/fallback_classifier.py`, `backend/config.py` | Full Matrix Test 14, `test_phase6_productization.py` | When artifact is unreadable or raises error, pipeline safely defaults to empirical base rate $P_0 = 0.35$ and deterministic heuristics. Zero 0.70 stale constants. | **PROVEN** | "Provides a deterministic fallback yielding an empirical baseline $P_0 = 0.35$ so model failure never halts webhook ingestion or causes undefined state transitions." |

---

## 2. Canonical Economics & Financial Accounting

| # | System Claim | Implementation Location | Test Verification | Empirical Finding | Audit Confidence | Safe Interview Wording |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **5** | **Zero Float Money (Integer Paise)** | `backend/domain/models.py` (`BigInteger` columns) | `tests/unit/test_pbt_financial_integers.py` | 100% of stored currency values are `BigInteger` minor units (integer paise). Floating-point arithmetic strictly forbidden for stored balances. | **PROVEN** | "Enforces exact integer-paise representation across database columns and business logic to eliminate floating-point rounding errors." |
| **6** | **Unclamped Incremental Accounting** | `backend/api/analytics.py` (`recovery-canonical`) | `test_phase5_canonical_negative_incremental_preservation` | Formula: $\text{Incremental} = \text{Gross} - \text{Organic}$. Negative incremental value (-80,000 paise) and negative net surplus (-80,100 paise) preserved without zero-clamping. | **PROVEN** | "Preserves true negative incremental values in analytical cohorts whenever an active intervention underperforms organic baseline recovery." |
| **7** | **Deterministic Net Surplus Ranking** | `backend/decision_engine/ranking.py`, `ai/models/action_value_model.py` | `tests/unit/test_phase3_economics.py` | Ranks interventions by $\lfloor (P_a - P_0) \times \text{Amount} \rfloor - \text{Cost}$. Lowest net surplus actions and negative-EV actions routed to `STOP`. | **PROVEN** | "Ranks candidate interventions deterministically by net expected incremental surplus after subtracting explicit action friction costs." |

---

## 3. Policy & Governance Safety

| # | System Claim | Implementation Location | Test Verification | Empirical Finding | Audit Confidence | Safe Interview Wording |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **8** | **Deterministic Policy Separation** | `backend/policy_engine/engine.py` | `tests/unit/test_policy_engine.py`, Full Matrix Test 13 | Policy engine holds absolute veto power over ML proposals. Evaluates rules in strict precedence order. | **PROVEN** | "Decouples machine learning proposal from deterministic policy enforcement; policy rules independently block or route actions regardless of ML scores." |
| **9** | **Fraud Policy Hard-Block** | `backend/policy_engine/engine.py` | `test_policy_engine.py` | Suspected fraud blocks all active interventions (`PAYMENT_LINK`, `RETRY`, `CUSTOMER_ACTION`), but permits `NO_INTERVENTION` to transition safely to `STOPPED`. | **PROVEN** | "Enforces hard-block policy rules that stop all active interventions upon fraud signals while permitting terminal STOP transitions." |
| **10** | **High-Value Human Approval Gate** | `backend/policy_engine/engine.py`, `backend/state_machine/machine.py` | Full Matrix Test 5, 6, 15 | Transactions exceeding threshold (₹10,000+) route to `AWAITING_APPROVAL`. Rejection stops execution; approval releases outbox task. | **PROVEN** | "Routes transactions exceeding configured exposure thresholds to a human-in-the-loop review queue before any gateway call is dispatched." |

---

## 4. Distributed Systems, Execution & Idempotency

| # | System Claim | Implementation Location | Test Verification | Empirical Finding | Audit Confidence | Safe Interview Wording |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **11** | **Webhook Ingestion Idempotency** | `backend/webhook_processor/handler.py` | `tests/unit/test_pbt_webhook_idempotency.py`, Full Matrix Test 7 | Webhook events deduplicated via provider event ID or payload hash; duplicate calls return `ALREADY_PROCESSED` without state machine side effects. | **PROVEN** | "Deduplicates incoming gateway webhooks using cryptographic payload hashes to prevent duplicate transaction state creation." |
| **12** | **Distributed Execution Concurrency Mutex** | `backend/execution_engine/engine.py`, `backend/state_machine/machine.py` | Full Matrix Test 8 & 10 | Redis mutex `lock:execution:{case_id}` combined with PostgreSQL `SELECT ... FOR UPDATE` prevents duplicate side effects under concurrent calls. | **PROVEN** | "Combines Redis distributed mutexes and PostgreSQL row-level locks to guarantee exactly-once external operation creation under concurrent triggers." |
| **13** | **CUSTOMER_ACTION Contract** | `backend/execution_engine/engine.py` | Full Matrix Test 4 | Designates `CUSTOMER_ACTION` as `INTERNAL_ACTION`. Exactly 0 external gateway calls and 0 verification polling tasks are created. | **PROVEN** | "Classifies CUSTOMER_ACTION as an internal merchant advisory action with zero external gateway operations and zero verification attempts." |
| **14** | **Bounded Asynchronous Verification** | `backend/verification_engine/tasks.py` | Full Matrix Test 12 | Celery task polls gateway settlement. Bounded at 3 attempts; upon exhaustion, automatically transitions case to `ESCALATED`. | **PROVEN** | "Reconciles settlement asynchronously through Celery polling tasks, automatically escalating cases after 3 failed verification attempts." |

---

## 5. Security & Multi-Tenancy

| # | System Claim | Implementation Location | Test Verification | Empirical Finding | Audit Confidence | Safe Interview Wording |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **15** | **Multi-Tenant Isolation** | `backend/api/dependencies.py`, `backend/api/cases.py` | `tests/integration/test_cross_merchant_isolation.py` (17 tests) | All DB queries scoped by `merchant_id`. Cross-tenant case requests return HTTP 404 (Not Found), preventing enumeration. | **PROVEN** | "Enforces strict row-level merchant scoping on all endpoints and database queries, returning 404 on cross-tenant requests." |
| **16** | **Audit Log PII Redaction** | `backend/api/cases.py` (`get_case_audit`) | `test_cases_api.py` (`test_get_case_audit_no_payload`) | `payload` JSONB column containing raw gateway dumps and card tokens is strictly excluded from API projections per `AUD-006`. | **PROVEN** | "Excludes raw gateway payloads and cardholder tokens from audit log API projections to protect sensitive customer data." |

---

## 6. Infrastructure & Deployment Environment

| # | System Claim | Implementation Location | Test Verification | Empirical Finding | Audit Confidence | Safe Interview Wording |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **17** | **Razorpay Gateway Integration** | `backend/gateway/fake_razorpay.py` | Entire test suite | Implemented as in-memory Python simulation (`FakeRazorpayGateway`). Does not invoke live Razorpay REST API endpoints. | **SIMULATED** | "Simulates Razorpay gateway interactions using an in-process fault-injectable adapter modeled on Razorpay's API schemas." |
| **18** | **Production Scale Traffic** | `backend/simulation/smoke_benchmark.py` | Local ASGI timings | Benchmarks measured locally via in-process ASGI calls on Windows dev machine. Does not reflect distributed network throughput. | **SIMULATED** | "Evaluated under local in-process developer benchmarks; production cloud throughput requires distributed load testing." |
