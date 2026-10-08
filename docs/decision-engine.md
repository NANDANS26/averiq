# Decision Engine

The Averiq Decision Engine orchestrates the revenue recovery pipeline.

## AI Fallback Flow

1. RootCauseReasoner attempts AIProvider.diagnose() with asyncio.wait_for timeout
2. On success: validates DiagnosisOutput schema (SEC-AI-001)
3. On timeout/exception: invokes DeterministicFallbackClassifier
4. On schema validation failure: invokes DeterministicFallbackClassifier
5. Returns DiagnosisResult with metadata for AuditEvent

## Prompt Injection Defense (SEC-AI-002)

- Static DIAGNOSIS_SYSTEM_PROMPT in "role": "system" — never constructed at runtime
- All untrusted data (error_code, etc.) in "role": "user" as structured JSON only
- customer_id, subscription_id, error_description excluded from LLM input (PII minimization)

## Schema Validation Gate (SEC-AI-001)

DiagnosisOutput is validated via Pydantic.model_validate() before any downstream use.
Unknown failure_category values trigger DeterministicFallback.

## FailureCategory Allowlist

All 9 FailureCategory values are pre-validated in ai_output_validator.py.
Unknown values from LLM → None → fallback invoked.

## Circuit Breaker (ExternalLLMProvider)

Opens after 3 consecutive asyncio.TimeoutError in 60 seconds.
Allows one probe call after RESET_TIMEOUT_SECONDS (60s).

## Policy Rule Definitions and Evaluation Order

The Policy_Engine evaluates rules in the following documented order (FR-POLICY-001):

1. **State validity check** — pre-condition: case is not in a terminal state
2. **payment_already_recovered** — blocks if case is RECOVERED
3. **duplicate_action** — blocks if identical `(case_id, intervention_type)` is ACTION_EXECUTING
4. **confidence_threshold** — blocks if `recovery_probability < min_confidence`
5. **amount_limit** — blocks if `payment_amount_at_risk > max_amount[intervention_type]`
6. **retry_budget** — blocks RETRY if `attempt_count >= max_retries`
7. **cooldown** — blocks if `elapsed < cooldown_seconds`
8. **human_approval** — routes to HUMAN_REVIEW queue if `payment_amount_at_risk > human_approval_threshold`

If none of the above block or route: decision = APPROVED.

Higher-precedence rules always fire before lower-precedence ones. For example,
`payment_already_recovered` fires before `confidence_threshold`. This ensures
terminal-state cases are caught immediately without evaluating downstream rules.

## Expected Value (EV) Pipeline

The EV pipeline executes in this strict order (enforced in code):

1. Recoverability model produces `recovery_probability`
2. Generate candidate interventions based on `ProviderCapabilityConfig`
3. Compute `expected_recovery_value` per candidate:
   `EV = P(recovery) × payment_amount_at_risk − cost − risk_penalty`
   Using `decimal.Decimal` with `ROUND_FLOOR`; result converted to `int` (paise)
4. Policy safety eligibility rules (blocking) applied by Policy_Engine — NOT as a pre-filter
5. Rank candidates by `expected_recovery_value` descending
6. Pass ranked list to Policy_Engine

## Source References

| Component | File |
|-----------|------|
| StubAIProvider | ai/providers/stub.py |
| ExternalLLMProvider | ai/providers/external_llm.py |
| DeterministicFallbackClassifier | backend/decision_engine/fallback_classifier.py |
| RootCauseReasoner | backend/decision_engine/root_cause_reasoner.py |
| AI Output Validator | backend/decision_engine/ai_output_validator.py |
| InterventionEngine | backend/decision_engine/intervention_engine.py |
| ExpectedValue | backend/decision_engine/expected_value.py |
| RankingEngine | backend/decision_engine/ranking.py |
| PolicyEngine | backend/policy_engine/engine.py |
