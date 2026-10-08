# Evaluation Pipeline

## Dataset Generation
- `make train` runs `ai/training/train.py`
- `DATASET_SIZE` env var (default: 50000, valid: 100-250000)
- `RANDOM_SEED` env var (default: 42) — same seed = identical dataset
- Latent variables: creditworthiness_score ~ Beta(2,5)
- No label leakage: recovery_outcome generated AFTER features via independent draw

## Feature Extraction (10 features)
1. failure_code (categorical)
2. error_code (categorical)
3. payment_method (categorical)
4. exposure_tier (ordinal: HIGH/MEDIUM/LOW)
5. payment_attempt_count (integer)
6. customer_success_rate (float — model input only)
7. time_since_failure_seconds (float — from decision_timestamp, not NOW())
8. subscription_status (categorical)
9. hour_of_day (integer 0-23)
10. day_of_week (integer 0=Monday, 6=Sunday)

**LLM-derived failure_category is NOT a model feature** (prevents train/serve mismatch)

## time_since_failure_seconds semantics

Computed as `decision_timestamp - failure_timestamp` where `decision_timestamp`
is the explicit timestamp stored on `revenue_risk_cases` at the moment features
are extracted for inference. During training, use the `decision_timestamp` from
the historical record. During serving, set `decision_timestamp = NOW()` at the
start of the Decision Engine run and record it on the case before feature
extraction. Using `NOW()` directly creates a train/serve mismatch and is
explicitly forbidden.

## Temporal Split
- training: created_at < 2023-09-01
- validation: 2023-09-01 <= created_at < 2023-11-01
- test: created_at >= 2023-11-01

Split boundaries are non-overlapping UTC timestamps documented in
`ai/training/feature_extraction.py`.

## Model Training
- `make train` → trains LightGBM (primary) and LogisticRegression (baseline)
- Artifacts stored in `model_artifacts/` with SHA-256 integrity check
- Each artifact includes: model_version, training_timestamp (UTC), dataset_hash,
  hyperparameters, random_seed

## Evaluation Metrics (`make evaluate`)
- Precision, Recall, F1, ROC-AUC, Brier score computed on test split
- Quality gate: Brier score ≤ BRIER_SCORE_WARNING_THRESHOLD (default: 0.25)
- Exit non-zero if gate fails; comparison table always printed
- Cohort consistency: baseline and LightGBM use identical test cases, same
  eligibility criteria, same outcome definition, same observation window, same currency

## Simulation Metrics (`make simulate`)
- Recovery_Rate, Incremental_Recovered_Revenue, Intervention_Success_Rate
- Escalation_Rate, Stop_Rate
- Division by zero → NOT_AVAILABLE (never a runtime error)
- All values computed at runtime — no hardcoded targets

## Incremental_Recovered_Revenue formula
```
Incremental_Recovered_Revenue = actual_recovered_revenue_in_cohort (minor units)
                              - baseline_expected_recovered_revenue_in_cohort (minor units)
```
Both values computed on the same eligible cohort, same eligibility criteria,
same recovery outcome definition, same observation window, same currency.
This is NOT a causal uplift claim — it is incremental measurement relative
to a defined baseline.

## Model Lifecycle
```
trained → evaluated → candidate → active → retired
```
- POST /api/v1/models/{id}/promote advances through stages
- Quality gates enforced on candidate → active (Brier score, F1, comparison vs current)
- All gate thresholds are configurable; none are hardcoded constants
- A model failing quality gates stays at CANDIDATE and cannot be promoted
- At most one active model per model_type (partial unique index)
- On activation: previous active model transitions to retired; artifact and metrics retained

## Reproducibility
- Fixed `RANDOM_SEED` throughout training and evaluation
- Same seed + same dataset → identical training run on same hardware
- Bit-for-bit float equality is not guaranteed across different hardware architectures;
  reproducibility within documented numerical tolerance is required (FR-MODEL-010)

## Source References

| Component | File |
|-----------|------|
| SyntheticDataGenerator | ai/data/generator.py |
| FeatureExtraction | ai/training/feature_extraction.py |
| TrainPipeline | ai/training/train.py |
| EvaluatePipeline | ai/evaluation/evaluate.py |
| SimulatePipeline | ai/evaluation/simulate.py |
| ModelLifecycleAPI | backend/api/models.py |
| LightGBMModel | ai/models/lightgbm_model.py |
| LogisticRegressionBaseline | ai/models/logistic_regression.py |
