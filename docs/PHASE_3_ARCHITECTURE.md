# Averiq Phase 3 Architecture: Context-Aware Intervention Decision Engine

## 1. Overview & Objective

Phase 3 upgrades Averiq from estimating only a single general recovery propensity:
$$P(\text{recovery} \mid X)$$
into an economic, counterfactual-aware decision engine that answers:

> "Given this failed payment, which permitted recovery intervention is expected to create the greatest incremental recovery value, while respecting policy, risk, cost, cooldowns, fraud controls, and human-approval requirements?"

### Core Design Principle:
$$\text{OBSERVE} \longrightarrow \text{UNDERSTAND} \longrightarrow \text{PREDICT} \longrightarrow \text{RANK} \longrightarrow \text{POLICY} \longrightarrow \text{APPROVAL} \longrightarrow \text{EXECUTION} \longrightarrow \text{VERIFICATION} \longrightarrow \text{LEARNING}$$

- **ML Predicts**: Action-specific recovery probabilities $P(Y(a)=1 \mid X)$.
- **Decision Intelligence Ranks**: Evaluates expected incremental net recovery value.
- **Policy Controls**: Deterministically approves, routes to human review, or blocks actions.
- **Infrastructure Executes**: Only `ExecutionEngine` can trigger external gateway operations.
- **Verification Confirms**: Independent gateway polling confirms true payment settlement.

---

## 2. Multi-Action Valuation & Economic Formulation

### A. Counterfactual Action Space
Supported recovery interventions:
1. `NO_INTERVENTION`: Baseline organic recovery ($a=0$). Cost: ₹0.
2. `PAYMENT_LINK`: Customer payment link via Razorpay API. Cost: ₹5.00 (500 paise).
3. `RETRY`: Gateway retry scheduling for transient failures. Cost: ₹1.00 (100 paise).
4. `CUSTOMER_ACTION`: Customer authentication / payment method update re-prompting. Cost: ₹2.00 (200 paise).
5. `STOP`: Terminal action terminating recovery attempts. Net EV: 0 paise.

### B. Action-Value Modeling (T-Learner)
Using the Dataset 2.0 benchmark, action-specific models estimate:
$$P_a = P(\text{recovery} \mid X, a) \quad \forall a \in \{\text{NO\_INTERVENTION}, \text{PAYMENT\_LINK}, \text{RETRY}, \text{CUSTOMER\_ACTION}\}$$

- **Base Estimators**: Separate LightGBM classifiers trained on cases receiving intervention $a$.
- **Calibration**: Isotonic Regression fitted on the non-overlapping validation split.
- **Incremental Uplift**:
  $$\Delta P_a = \max\left(0, P_a - P_{\text{NO\_INTERVENTION}}\right)$$

### C. Economic Expected Value
Financial calculations are conducted strictly in **integer paise** using `Decimal` with `ROUND_FLOOR`:
$$\text{ExpectedIncrementalRecovery}_a = \left\lfloor \Delta P_a \times \text{Amount}_{\text{paise}} \right\rfloor$$
$$\text{ExpectedNetRecoveryValue}_a = \text{ExpectedIncrementalRecovery}_a - \text{Cost}_a$$

---

## 3. Candidate Ranking & Policy Boundary

1. **Deterministic Ranking Order**:
   - Primary: `expected_net_recovery_value` descending
   - Secondary: `recovery_probability` descending
   - Tertiary: Lower intervention cost
2. **Policy Authority**:
   - The ranker establishes **Model Preference**.
   - The `PolicyEngine` evaluates the ranked candidates sequentially.
   - If the top candidate violates any rule (`amount_limit`, `retry_budget`, `cooldown`, `fraud_block`), it is **BLOCKED**, and the system falls back to the next approved candidate.
   - If all positive-EV candidates are blocked, `STOP` is selected, transitioning the case to `STOPPED`.

---

## 4. Fallback Architecture

If the action-value model or calibrated base model is missing or corrupted:
1. **Fallback Recovery Probability**: $\mathbf{0.35}$ (the empirical benchmark base rate, rather than an optimistic 0.70).
2. **Model Version String**: Marked as `fallback-heuristic` to ensure fallback executions are clearly distinguishable in audit logs and database queries.
3. **Safety Guarantee**: The fallback cannot trigger unapproved operations because all policy gates and human approval thresholds remain authoritative.
