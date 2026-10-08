# Averiq — Engineering Interview Cheat Sheet
**20 High-Frequency Technical Architecture Questions & Forensic Answers**

---

### 1. What core engineering problem does Averiq solve?
Averiq transforms failed digital payment recovery from naive, static heuristic retries into a **decision intelligence engine**. It identifies at-risk transactions, calculates the counterfactual incremental recovery value of candidate interventions, enforces deterministic policy and fraud constraints, coordinates human-in-the-loop approvals, and executes idempotent gateway operations with full auditability.

### 2. Why not just use a rules engine?
Static rules engines (e.g., "retry whenever error is INSUFFICIENT_FUNDS") suffer from three fatal flaws:
1. They ignore **counterfactual organic recovery** (wasting money and customer patience on transactions that would have recovered on their own).
2. They cannot estimate non-linear, multi-variable recovery probabilities across diverse payment contexts.
3. They lack dynamic action ranking that balances expected gross recovery against execution friction costs and failure penalties.

### 3. Why incremental uplift rather than raw recovery probability?
A raw prediction model tells you: $P(\text{Recovery} \mid \text{Action})$. If a customer has an 80% chance of recovering organically without any action, intervening to achieve an 82% recovery rate yields only a **2% incremental uplift** while costing fees and risking customer disruption. Averiq optimizes for **Incremental Net Value**:
$$\text{Net Value} = (P_{\text{action}} - P_0) \times \text{Amount} - \text{Cost}$$

### 4. Why action-specific models (T-Learner) instead of a single multi-class model?
A single multi-class classifier predicts which action was observed in observational data, conflating past merchant behavior with treatment effects. A **T-Learner** fits distinct, calibrated LightGBM estimators $\mu_a(X)$ for each action arm $a \in \mathcal{A}$, providing unbiased estimates of potential outcomes $Y(a)$ given context $X$.

### 5. Why is `NO_INTERVENTION` mandatory in the action vocabulary?
`NO_INTERVENTION` is the control arm representing organic baseline recovery $P_0$. Without $P_0$, an engine cannot compute incremental counterfactual value. If no active intervention produces positive net incremental surplus over $P_0$, Averiq chooses `NO_INTERVENTION` and transitions to `STOPPED`.

### 6. Why is `CUSTOMER_ACTION` an internal action rather than a gateway call?
`CUSTOMER_ACTION` refers to merchant portal prompts, in-app notifications, or email advisories asking the customer to update payment details. It is classified as `ExecutionCapability.INTERNAL_ACTION` and therefore executes **0 external gateway operations** and requires **0 gateway verification attempts**.

### 7. How are intervention friction costs accounted for?
Each candidate intervention carries an explicit operational friction cost in integer paise (e.g., payment links incur messaging and checkout costs; retries incur gateway processing fees). The expected net surplus subtracts these costs directly from the expected incremental recovery.

### 8. Why are financial amounts strictly integer paise?
Floating-point arithmetic (IEEE 754) introduces binary rounding errors (e.g., `0.1 + 0.2 = 0.30000000000000004`). In financial systems, all monetary values must be stored as minor currency units (paise in INR, cents in USD) using `BigInteger` database columns to guarantee exact mathematical reconciliation.

### 9. How is webhook idempotency enforced?
Incoming webhooks are hashed using the provider event identifier or SHA-256 payload digest. When a webhook arrives, an atomic database lookup checks `webhook_events`. If the event ID already exists in `PROCESSED` status, the ingestion pipeline logs the deduplication and immediately returns `ALREADY_PROCESSED` without re-triggering the state machine.

### 10. How do you prevent duplicate execution under concurrent worker calls?
Before executing an intervention outbox task, Celery workers acquire a Redis mutex `lock:execution:{case_id}` with an explicit timeout. If a duplicate worker or concurrent API trigger attempts execution simultaneously, the lock acquisition fails and execution aborts before any external API is called.

### 11. How does settlement verification work?
After an intervention (such as `PAYMENT_LINK`) is executed, the case enters `VERIFYING`. Averiq supports both asynchronous webhook confirmation (`payment.captured`) and bounded polling tasks. Once gateway settlement evidence is verified, the case transitions to `RECOVERED` and attributes revenue surplus.

### 12. What happens when verification fails repeatedly?
Verification attempts are bounded by `VERIFICATION_MAX_ATTEMPTS = 3`. If the settlement window expires after 3 unsuccessful attempts, the case automatically transitions to `ESCALATED`, routing it to an operator review queue rather than leaving it in an ambiguous state.

### 13. What happens if the ML model is unavailable or corrupted?
If the artifact `action_value_v3.pkl` is unreadable or model inference raises an exception, the system activates `DeterministicFallback`. In fallback mode, the engine uses the empirical baseline recovery probability **$P_0 = 0.35$** and deterministic rule heuristics, ensuring 100% uptime for transaction ingestion.

### 14. Why is the fallback probability strictly 0.35?
The value $P_0 = 0.35$ represents the calibrated empirical base rate of organic recovery derived from historical dataset analysis. Code audits confirmed that zero references to arbitrary 0.70 probabilities exist in the decision path.

### 15. How are policy constraints enforced independently of ML?
The policy engine runs downstream of ML inference. ML models propose probabilities, but the policy engine holds veto power. It applies deterministic checks (fraud detection, maximum case amounts, merchant cooldowns). If a check fails, the candidate action is blocked, and the system transitions to `STOPPED`.

### 16. How are high-value transactions governed?
If a transaction's value exceeds the merchant's configured threshold (`human_approval_threshold_minor_units`, e.g., ₹10,000), the policy engine blocks automated execution and transitions the case to `AWAITING_APPROVAL`. A human operator must explicitly approve or reject the action via the UI or API before any gateway call is dispatched.

### 17. How is cross-merchant tenant isolation enforced?
All database queries and API endpoints require the authenticated user's `merchant_id` in the `WHERE` clause. Attempting to query another merchant's case ID returns an HTTP 404 (Not Found) rather than 403, preventing multi-tenant enumeration attacks.

### 18. How is the decision auditable?
Every state transition produces an append-only `AuditEvent` record containing `from_state`, `to_state`, `action_type`, `policy_version`, `model_version`, and a shared `correlation_id`. Sensitive cardholder data and raw gateway payloads are stripped per rule `AUD-006`.

### 19. What are Averiq's biggest architectural limitations?
1. It is a **production-hardened simulation and decision platform**, not a live card processor.
2. It assumes a static 4-action vocabulary rather than arbitrary dynamic interventions.
3. It currently evaluates individual payment lifecycles independently rather than optimizing lifetime customer value across years.

### 20. What would you build next (Phase 7+)?
1. Real-time online model calibration (Platt/Isotonic recalibration from live settlement feedback).
2. Bandit-style exploration budgets (allocating 5% of traffic to safe randomized exploration to discover evolving payment rail trends).
3. Production cloud packaging (Terraform modules for AWS ECS/EKS with KMS-encrypted secrets and VPC peering).
