# Averiq — Observability & Explainability Architecture (Phase 5)

This report details the structured logging, tracing, metrics, and mathematical explainability framework implemented in Averiq Phase 5.

---

## 1. Structured Logging Taxonomy

Averiq utilizes `structlog` emitting standardized JSON events with standard context keys:
- `timestamp`: ISO-8601 UTC timestamp
- `level`: `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- `correlation_id`: Unique identifier spanning the full lifecycle of an event
- `case_id`: Identifier of the RevenueRiskCase
- `merchant_id`: Tenant identifier
- `event`: Machine-parseable event name (e.g., `decision_pipeline_started`, `action_value_inference_success`, `policy_evaluation_completed`, `intervention_executed`, `verification_outcome_received`)

---

## 2. Decision Explainability Framework

Every intervention decision includes a complete mathematical audit trace persisted in `case_metadata["decision_trace"]` and served via `GET /api/v1/cases/{case_id}/trace`:

### A. Mathematical Formulation
$$\text{IncrementalUplift}(a) = \max(0, P(\text{recovery} \mid X, a) - P_0)$$
$$\text{ExpectedIncrementalRecovery}(a) = \lfloor \text{IncrementalUplift}(a) \times \text{Amount}_{\text{paise}} \rfloor$$
$$\text{ExpectedNetRecoveryValue}(a) = \text{ExpectedIncrementalRecovery}(a) - \text{Cost}_{\text{paise}}$$

### B. Explainability API Response Fields
- `baseline_recovery_probability_p0`: Empirical or model-evaluated organic baseline ($P_0 = P(\text{NO\_INTERVENTION})$).
- `model_preferred_action`: Action maximizing expected net recovery value.
- `reason_codes`: Deterministic justification tags (e.g. `POSITIVE_INCREMENTAL_VALUE`, `PAYMENT_LINK_MAX_VALUE`, `HIGH_EXPOSURE_TIER`, `NEGATIVE_VALUE_TERMINATION`).
- `mathematical_evaluations`: Per-candidate probability, uplift, cost, net expected value, and explicit formula string.

---

## 3. Canonical Revenue Recovery Accounting

Implemented in `/api/v1/analytics/recovery-canonical` following Phase 3.6 canonical definitions:
- **Organic Recovery**: Expected baseline recovery without intervention: $\sum \lfloor P_0 \times \text{Amount} \rfloor$.
- **Gross Recovered**: Realized recoveries from successful terminal states: $\sum \text{Amount}_{\text{recovered}}$.
- **Incremental Recovery**: Realized gain exceeding organic expectation: $\max(0, \text{Gross} - \text{Organic})$.
- **Intervention Cost**: Cumulative communication and execution costs incurred.
- **Net Incremental Recovery**: Economic surplus: $\text{Incremental} - \text{Costs}$.
