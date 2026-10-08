# Averiq — Hostile Red-Flag Audit & Forensic Risk Assessment
**Razorpay-Style Senior Engineering Audit**

---

## 1. Executive Summary: The Harsh Truth

Averiq is an **exceptionally rigorous, well-architected prototype and simulation platform**. Its state machine rigor, integer arithmetic discipline, and causal framing put it well above the standard of hackathon or student projects.

However, if presented to a cynical Principal Engineer or Payments Director at Razorpay, Stripe, or Adyen, claiming this is a "live revenue recovery system" will lead to immediate reputational damage.

The project shines when defended as:
> **"A production-hardened decision intelligence simulation and state-machine orchestration engine."**

It fails when hyped as:
> **"A production AI system recovering live merchant revenue on Razorpay."**

---

## 2. Red-Flag Inventory by Severity

### CRITICAL (Deal-Breaker in Interview if Misrepresented)

| Item | Description & Code Finding | Risk in Technical Interview | Required Correction |
| :--- | :--- | :--- | :--- |
| **RF-01** | **Gateway Integration is In-Memory Only** (`backend/gateway/fake_razorpay.py`): There is no active TLS connection to `api.razorpay.com`. `RazorpayTestGateway` exists as a stub, but all tests and fixtures run on `FakeRazorpayGateway`. | Interviewer asks: "How do you handle Razorpay's 429 rate limit backoff headers or mTLS?" Answering with live claims will expose that no network traffic occurs. | Explicitly state: "We built an in-process fault-injectable gateway adapter mirroring Razorpay's schemas to rigorously test failure modes and state transitions." |
| **RF-02** | **"Causal Inference" vs. Synthetic Potential Outcomes**: The T-Learner is trained on synthetic data where potential outcomes were drawn from known Beta distributions. | Claiming "we proved causal uplift" will trigger an ML scientist to ask about unobserved confounding, SUTVA violations, and selection bias. | State: "Under our unconfounded synthetic generation, the T-Learner models potential outcomes; in live production, offline observational data would require doubly robust estimation or randomized exploration." |
| **RF-03** | **Transaction Boundary During Gateway Timeout**: In `backend/execution_engine/engine.py`, the gateway call `await self.gateway.create_payment_link(...)` occurs inside the async execution flow after inserting `ExternalOperation(status="IN_PROGRESS")`. If the Python worker process crashes mid-HTTP call (before DB commit), the gateway may have charged the user or created a link, but PostgreSQL rolls back. | A distributed systems engineer will immediately probe this two-phase commit vulnerability. | Acknowledge: "This is the classic dual-write distributed systems problem. In a full production deployment, we would require a persistent outbox reconciliation scanner polling the gateway before retrying." |

---

### HIGH (Architectural Limitations That Must Be Acknowledged)

| Item | Description & Code Finding | Architectural Reality |
| :--- | :--- | :--- |
| **RF-04** | **In-Process ASGI Benchmarking**: Benchmark latencies in `smoke_benchmark.py` (e.g. 0.1ms for `/health`) were measured using in-process ASGI calls on localhost. | This does not reflect real-world network hop latencies, TLS negotiation, reverse proxy overhead, or distributed database connection pool contention. |
| **RF-05** | **Static 4-Action Vocabulary**: The action space is hardcoded to `NO_INTERVENTION`, `PAYMENT_LINK`, `RETRY`, and `CUSTOMER_ACTION`. | In real e-commerce, actions include dynamic discounts, WhatsApp messaging, payment rail rerouting, and UPI intent requests. |
| **RF-06** | **Fixed Verification Timeout (3 Attempts)**: Verification uses a hardcoded maximum of 3 retries in Celery tasks. | If a customer pays via payment link 4 hours later, the case has already transitioned to `ESCALATED`. Real webhook ingestion must handle out-of-order late settlements. |

---

### MEDIUM (Minor Implementation Shortcomings)

| Item | Description & Code Finding | Status |
| :--- | :--- | :--- |
| **RF-07** | **Pydantic Protected Namespace Warnings**: Pydantic v2 raises warnings regarding fields starting with `model_` (`model_version`, `model_name`, `model_loaded`). | Harmless deprecation warning, but shows minor configuration friction with Pydantic v2 protected namespaces. |
| **RF-08** | **OpenTelemetry Metric Exporter Teardown**: Pytest logs note `ValueError: I/O operation on closed file` during OTel metric shutdown. | Test teardown artifact; does not affect application runtime. |

---

## 3. The 20 Hostile Architecture Scenarios (Systems Stress Audit)

Here is how Averiq behaves under extreme real-world payment failures:

#### 1. What happens if Redis dies?
- **Behavior**: Celery tasks cannot be dispatched; acquiring `lock:execution:{case_id}` fails.
- **State Impact**: Case remains in `ACTION_APPROVED`. No external operations are dispatched.
- **Audit**: Fail-closed. Safe, but stops recovery until Redis recovers.

#### 2. What happens if Celery dies?
- **Behavior**: Ingestion still succeeds via FastAPI and writes to PostgreSQL. Cases pause in `ACTION_APPROVED` awaiting workers.
- **Mitigation**: Database state is preserved; once workers resume, the outbox queue can be re-dispatched.

#### 3. What happens if PostgreSQL becomes unavailable?
- **Behavior**: Webhook ingestion returns HTTP 500. Gateway will retry webhook delivery per standard webhook backoff (e.g. 24h).
- **Mitigation**: Stateless API layer fails gracefully; gateway retry guarantees eventual consistency.

#### 4. What happens if the worker crashes after gateway execution but before DB commit?
- **Behavior**: Gateway created the payment link, but DB transaction rolls back.
- **Remaining Risk**: A subsequent run might attempt to recreate the link.
- **Mitigation**: `FakeRazorpayGateway` (and real Razorpay) uses `reference_id = case.id` as an idempotency key, so duplicate link creation returns the existing link.

#### 5. What happens if the same webhook arrives 100 times?
- **Behavior**: Hashed event ID check fires. Exactly 1 webhook is processed; 99 are dropped with `ALREADY_PROCESSED`.
- **Test Evidence**: Verified by `test_p2_processed_webhook_returns_already_processed`.

#### 6. What happens if two workers process the same case concurrently?
- **Behavior**: PostgreSQL `SELECT ... FOR UPDATE` serializes the workers. The second worker re-reads the updated state and exits safely.
- **Test Evidence**: Verified in Full Matrix Test 10.

#### 7. What happens if verification is delayed for hours?
- **Behavior**: Celery verification task retries up to 3 times, then escalates to `ESCALATED`.
- **Mitigation**: If a late webhook arrives later, the case can be reconciled.

#### 8. What happens if verification never arrives?
- **Behavior**: Case transitions cleanly to terminal state `ESCALATED`. Does not remain in indefinite pending state.

#### 9. What happens if the ML model returns NaN?
- **Behavior**: Pydantic schema validation fails. `RootCauseReasoner` catches the validation error and triggers `DeterministicFallbackClassifier` with $P_0 = 0.35$.

#### 10. What happens if the model artifact is deleted from disk?
- **Behavior**: System automatically invokes fallback base rate $P_0 = 0.35$; log emits `model_artifact_missing`.
- **Test Evidence**: Verified in Full Matrix Test 14.

#### 11. What happens if policy and model disagree?
- **Behavior**: Policy wins unconditionally. The model only ranks candidates; policy holds absolute veto power.

#### 12. What happens if the transaction amount is ₹10 Crore (1,000,000,000 paise)?
- **Behavior**: Policy rule `amount_limit` blocks automated execution. Case is routed to `AWAITING_APPROVAL` or `STOPPED`.

#### 13. What happens if the merchant is flagged for fraud?
- **Behavior**: Policy rule `fraud_suspected` fires. All active interventions are blocked; action transitions to `STOPPED` with zero external calls.

#### 14. What happens if an action is retried after success?
- **Behavior**: Pre-condition check `case.state != RECOVERED` fires in `CanExecuteAction`. Execution is blocked.

#### 15. What prevents double execution on payment links?
- **Behavior**: Combination of Redis mutex lock, DB row lock, and outbox table constraints.

#### 16. What prevents cross-merchant tenant leaks?
- **Behavior**: Row-level SQL scoping (`WHERE merchant_id = :id`). Cross-tenant requests return 404.
- **Test Evidence**: Verified across 17 integration tests in `test_cross_merchant_isolation.py`.

#### 17. What happens if a human approves a stale decision 3 days later?
- **Behavior**: Case state is checked upon submission. If expired or already transitioned, the approval is rejected.

#### 18. What happens if action economics produce negative incremental recovery?
- **Behavior**: Negative value is preserved in canonical accounting; ranking engine rejects negative-EV actions and selects `STOP`.

#### 19. What happens if the gateway returns HTTP 500?
- **Behavior**: `ExecutionEngine` catches the gateway exception, marks `ExternalOperation(status="FAILED")`, and logs failure.
- **Test Evidence**: Verified in Demo Scenario 4.

#### 20. What happens if the gateway times out but actually processed the payment?
- **Behavior**: Asynchronous verification engine checks payment status via gateway query; late webhook reconciliation confirms settlement.
