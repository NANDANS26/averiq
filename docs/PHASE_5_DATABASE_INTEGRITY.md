# Averiq — Phase 5 Database & Financial Integrity Audit

This report validates schema constraints, currency isolation, and integer paise accounting across all production database tables in Averiq.

---

## 1. Zero Float Money Invariant (DATA-001)

PostgreSQL schema verification confirms that **zero monetary columns use floating-point types (`float`, `real`, `double precision`)**:

| Table | Column | DB Type | Unit | Nullable |
| :--- | :--- | :--- | :--- | :--- |
| `payments` | `amount` | `BIGINT` | Integer Paise | No |
| `revenue_risk_cases` | `payment_amount_at_risk` | `BIGINT` | Integer Paise | No |
| `revenue_risk_cases` | `projected_subscription_revenue_at_risk` | `BIGINT` | Integer Paise | Yes |
| `revenue_risk_cases` | `recovered_amount` | `BIGINT` | Integer Paise | Yes |
| `interventions` | `expected_recovery_value` | `BIGINT` | Integer Paise | Yes |
| `subscriptions` | `billing_amount` | `BIGINT` | Integer Paise | No |

**Audit Result**: **PASSED**. 100% of financial attributes use `BIGINT` representing integer paise. Non-financial rates (`recovery_probability`, `diagnosis_confidence`) correctly use `FLOAT`.

---

## 2. Multi-Currency Isolation (FR-RISK-007)

Every financial record enforces an explicit ISO 4217 three-letter currency code:
- Currency default: `"INR"`
- Aggregation rules: Cross-currency sums are strictly forbidden at the API query level. Cases in currencies different from the merchant's configured currency are segregated into `currency_mismatch_cases`.

---

## 3. Append-Only Audit Trail (AUD-002, AUD-006)

The `audit_events` table enforces complete auditability:
- **No `updated_at` column**: Table is append-only by schema design.
- **Payload Privacy (AUD-006)**: Raw gateway responses and card numbers are excluded from JSONB `payload`.
- **Correlation ID Tracking**: Every row links a UUID `correlation_id` tying asynchronous Celery jobs, webhooks, and decision traces into an immutable causality tree.

---

## 4. State Machine & Integrity Constraints

All tables feature strict PostgreSQL `CHECK` constraints:
- `chk_cases_state_valid`: 13 discrete valid states (`OBSERVED`, `AT_RISK`, `DIAGNOSED`, `RECOVERY_ELIGIBLE`, `ACTION_PENDING`, `ACTION_APPROVED`, `ACTION_EXECUTING`, `VERIFYING`, `RETRYABLE`, `RECOVERED`, `STOPPED`, `ESCALATED`, `FAILED`, `UNKNOWN_TERMINAL`).
- `chk_cases_payment_amount_positive`: `payment_amount_at_risk > 0`.
- `ck_ext_op_status`: Valid status values only (`NOT_STARTED`, `REQUESTED`, `SUCCEEDED`, `FAILED`, `TIMEOUT`, `UNKNOWN`, `VERIFIED`).
- `chk_verification_outcome`: `RECOVERED`, `STILL_FAILED`, `TIMEOUT`, `PARTIAL`.
