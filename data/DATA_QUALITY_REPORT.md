# Dataset 2.0 Quality & Validation Audit Report

**Generated**: 2026-10-07T16:55:40.453198+00:00  
**Generator Version**: 2.0.0  
**Random Seed**: 42  
**Dataset Size**: 20,000 records  
**File Location**: `data/v2/dataset_20k.parquet`  

---

## 1. Executive Summary & Integrity Verification

Dataset 2.0 was generated using the structural potential outcomes simulator (`PaymentEnvironmentSimulator`) incorporating:
- Multi-tier entities (50 simulated merchants, 4,000 distinct customer profiles).
- Latent customer intent, temporary liquidity, and gateway availability states.
- Structural counterfactual responses: $Y(0)$, $Y(\text{PAYMENT\_LINK})$, $Y(\text{RETRY})$, $Y(\text{CUSTOMER\_ACTION})$.
- Policy-guided treatment assignment with 30% exploration.
- Strict chronological temporal split with zero future-lookahead.

### Core Metrics:
- **Total Cases**: 20,000
- **Total At-Risk Volume**: ₹21,978,715.00 (2,197,871,500 paise)
- **Total Recovered Volume**: ₹8,235,094.51 (823,509,451 paise)
- **Overall Recovery Rate**: 34.97%
- **Missing Values**: 0 across all decision-time mandatory features.

---

## 2. Chronological Temporal Splits

Splits are strictly chronological by `created_at` with documented non-overlapping UTC boundaries:

| Split | Count | Share | Window Start | Window End | Recovery Rate |
|---|---|---|---|---|---|
| **Train** | 14,000 | 70.0% | `2023-01-01T01:09:44+00:00` | `2023-09-13T14:39:40+00:00` | 35.29% |
| **Validation** | 3,000 | 15.0% | `2023-09-13T14:40:41+00:00` | `2023-11-06T07:47:31+00:00` | 33.57% |
| **Test** | 3,000 | 15.0% | `2023-11-06T08:21:46+00:00` | `2023-12-31T23:07:47+00:00` | 34.87% |

*Temporal Verification*:
- $\max(\text{train.created\_at}) \le \min(\text{val.created\_at})$: **VERIFIED**
- $\max(\text{val.created\_at}) \le \min(\text{test.created\_at})$: **VERIFIED**

---

## 3. Intervention Distribution & Observed Recovery

| Intervention Assigned | Sample Count | Distribution | Observed Recovery Rate |
|---|---|---|---|
| `CUSTOMER_ACTION` | 3,422 | 17.1% | 42.34% |
| `NO_INTERVENTION` | 4,415 | 22.1% | 11.46% |
| `PAYMENT_LINK` | 8,551 | 42.8% | 35.59% |
| `RETRY` | 3,612 | 18.1% | 55.26% |

---

## 4. Failure Category Breakdown

| Failure Category | Sample Count | Share | Observed Recovery Rate |
|---|---|---|---|
| `AUTHENTICATION_FAILURE` | 4,264 | 21.3% | 54.60% |
| `INSUFFICIENT_FUNDS` | 3,515 | 17.6% | 8.76% |
| `EXPIRED_CARD` | 2,850 | 14.2% | 30.32% |
| `LIMIT_EXCEEDED` | 2,034 | 10.2% | 35.25% |
| `FRAUD_SUSPECTED` | 1,501 | 7.5% | 6.73% |
| `TECHNICAL_FAILURE` | 1,500 | 7.5% | 71.67% |
| `UNKNOWN` | 1,496 | 7.5% | 15.51% |
| `CARD_DECLINED` | 1,424 | 7.1% | 29.35% |
| `NETWORK_TIMEOUT` | 1,416 | 7.1% | 67.16% |

---

## 5. Payment Method Breakdown

| Payment Method | Sample Count | Share | Observed Recovery Rate |
|---|---|---|---|
| `card` | 10,017 | 50.1% | 34.78% |
| `upi` | 5,989 | 29.9% | 36.88% |
| `netbanking` | 2,435 | 12.2% | 32.53% |
| `wallet` | 956 | 4.8% | 32.32% |
| `emi` | 603 | 3.0% | 33.17% |

---

## 6. Leakage & Feature Guardrail Audit

1. **Decision-Time Feature Whitelist ({len(DECISION_TIME_FEATURES)} fields)**:
   - Validated: Every feature in `DECISION_TIME_FEATURES` is observable strictly before intervention execution.
2. **Prohibited Leakage Blacklist ({len(PROHIBITED_LEAKAGE_FEATURES)} fields)**:
   - Validated: None of the prohibited post-intervention fields (`recovered`, `recovered_amount`, `recovery_time_seconds`, `treatment_assigned`, counterfactual $Y(x)$, or latent variables) are accessible to candidate models.
3. **Automated Unit Test Status**:
   - `tests/unit/test_dataset_v2.py`: **7/7 PASSED**.
