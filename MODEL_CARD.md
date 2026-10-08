# Model Card: Averiq Recoverability Prediction Model (v2.0-calibrated)

## Model Details
- **Model Name**: Calibrated LightGBM Recoverability Predictor
- **Version**: `lgbm-v2.0-calibrated`
- **Feature Version**: `v2.0`
- **Model Architecture**: LightGBM Gradient Boosted Decision Trees with post-hoc Isotonic Regression Probability Calibration
- **Primary Task**: Predicts counterfactual recovery probability $P(\text{recovery} \mid X_{\text{decision}})$ for failed transactions to guide recovery intervention prioritization.
- **Maintainer**: Averiq Core ML / Payment Decision Engine Team
- **Release Date**: 2026-10-07
- **Artifact Path**: `model_artifacts/lgbm_v2_calibrated.pkl`
- **SHA-256 Digest**: `5b6e263c813231ff0cb6e4e8913994344476059d43503b1e32d56c80bc54407b`

---

## Intended Use
- **Primary Use**: Scoring failed payment events inside Averiq's Celery decision pipeline (`backend/decision_engine/tasks.py`) to rank potential recovery interventions by Expected Incremental Recovered Value.
- **Decision Engine Boundary**: The model outputs risk and recovery probabilities ONLY. It has **zero authority** to initiate money movement or execute payment requests. All interventions are gated by `PolicyEngine` and executed by `ExecutionEngine`.
- **Out of Scope**: 
  - Real-time pre-authorization fraud scoring (use specialized upstream gateway fraud tools).
  - Autonomous debiting without policy and human approval gating when high-risk thresholds are exceeded.

---

## Training Data & Methodology
- **Dataset**: Averiq Dataset 2.0 Benchmark (`data/v2/dataset_20k.parquet`)
- **Total Cases**: 20,000 cases (70% Train [14,000], 15% Validation [3,000], 15% Test [3,000])
- **Temporal Splitting**: Chronological partitioning by `created_at` with zero future lookahead.
  - Train: `2023-01-01T01:09:44Z` to `2023-09-13T14:39:40Z`
  - Validation: `2023-09-13T14:40:41Z` to `2023-11-06T07:47:31Z`
  - Test: `2023-11-06T08:21:46Z` to `2023-12-31T23:07:47Z`
- **Latent Ground Truth**: Synthetic generation incorporates latent customer payment intent, temporary balance liquidity, and issuer gateway availability states, producing structural potential outcomes $Y(0)$, $Y(\text{PAYMENT\_LINK})$, $Y(\text{RETRY})$, $Y(\text{CUSTOMER\_ACTION})$.

---

## Feature Schema & Guardrails
- **Input Dimensions**: 14 decision-time features (`DECISION_TIME_FEATURES`).
- **Feature Whitelist**:
  1. `amount_paise` (Integer, authoritative minor currency units)
  2. `exposure_tier` (Ordinal)
  3. `failure_category_idx` (Categorical)
  4. `payment_method_idx` (Categorical)
  5. `time_since_failure_seconds` (Float elapsed duration)
  6. `hour_of_day` (Integer 0–23)
  7. `day_of_week` (Integer 0–6)
  8. `payment_attempt_count` (Integer)
  9. `has_subscription` (Binary 0/1)
  10. `customer_historical_success_rate` (Float)
  11. `customer_tenure_days` (Integer)
  12. `gateway_health_score` (Float)
  13. `is_transient_error` (Binary 0/1)
  14. `merchant_risk_tier` (Ordinal)
- **Strict Leakage Blacklist**: Post-intervention outcomes, counterfactual $Y(x)$, observed recovery amounts, and unobservable latents are banned from model ingestion and tested via `tests/unit/test_dataset_v2.py`.

---

## Performance Evaluation (Holdout Test Split $N=3,000$)
- **ROC-AUC**: **0.7933** (vs 0.7495 for Logistic Regression baseline, 0.5000 heuristic)
- **PR-AUC**: **0.6092** (vs 0.5916 for Logistic Regression baseline, 0.3487 heuristic)
- **Brier Score**: **0.1729** (vs 0.2006 for Logistic Regression baseline, 0.2271 heuristic)
- **Binary Log Loss**: **0.5178** (vs 0.5912 for Logistic Regression baseline)
- **Expected Calibration Error (ECE)**: **0.0350** (3.5% error across all probability deciles)
- **Uplift Targeting Efficiency**: **2.52× higher true incremental uplift** in top 30% targeted cohort vs untargeted cohort.

---

## Ethical & Operational Safeguards
1. **Financial Precision**: Authoritative financial amounts remain integer paise (`DATA-001`, `DATA-003`). Floats are restricted strictly to model weights and probability outputs.
2. **Fail-Safe Fallback**: If the model artifact fails SHA-256 verification or raises an inference exception, the pipeline falls back gracefully to a deterministic baseline (`0.70`), logs an operational alert, and proceeds to the Policy Gate.
3. **No Direct Execution**: The model is strictly advisory; only `ExecutionEngine` interacting with idempotent gateway abstractions can trigger external operations.
