# Averiq Decision Intelligence Reference

## Mathematical Formulation of Decision Intelligence

### 1. The Core Decision Problem
A failed payment $i$ carries an at-risk balance $\text{Amount}_i$ (in integer paise).

The engine must choose an action $a \in \mathcal{A}$ where:
$$\mathcal{A} = \{\text{NO\_INTERVENTION}, \text{PAYMENT\_LINK}, \text{RETRY}, \text{CUSTOMER\_ACTION}, \text{STOP}\}$$

### 2. Potential Outcomes Framework
Under Rubin's Causal Model:
- $Y_i(0)$: Binary recovery indicator if no intervention is attempted.
- $Y_i(a)$: Binary recovery indicator if intervention $a$ is executed.
- $\tau_i(a) = Y_i(a) - Y_i(0)$: Individual treatment effect (ITE).

### 3. Observable Estimation at Decision Time
In production, potential outcomes are unobservable counterfactuals. The decision engine uses observable features $X \in \mathbb{R}^{14}$ available at the decision timestamp to estimate:
$$P_0 = P(Y(0) = 1 \mid X)$$
$$P_a = P(Y(a) = 1 \mid X)$$

Estimated incremental uplift is non-negative:
$$\widehat{\Delta P}_a = \max\left(0, P_a - P_0\right)$$

### 4. Monetary Valuation & Integer Paise Discipline
Gross expected recovery in integer minor currency units:
$$\text{GrossEV}_a = \left\lfloor \widehat{\Delta P}_a \times \text{Amount}_{\text{paise}} \right\rfloor$$

Net expected recovery after accounting for provider costs:
$$\text{NetEV}_a = \text{GrossEV}_a - \text{Cost}_a$$

| Intervention | Execution Rail | Default Cost (Paise) | Target Failure Archetype |
|---|---|---|---|
| `NO_INTERVENTION` | None | 0 | Unrecoverable or organic self-healing |
| `RETRY` | Background Gateway Queue | 100 (₹1.00) | `NETWORK_TIMEOUT`, `TECHNICAL_FAILURE` |
| `CUSTOMER_ACTION` | In-App / SMS Prompt | 200 (₹2.00) | `AUTHENTICATION_FAILURE`, `EXPIRED_CARD` |
| `PAYMENT_LINK` | Razorpay API Link | 500 (₹5.00) | `CARD_DECLINED`, `LIMIT_EXCEEDED` |
| `STOP` | Internal Terminal State | 0 | Fraud, exhausted retries, negative EV |

---

## Decision Trace Structure
Every decision produces an auditable trace stored in `revenue_risk_cases.case_metadata['decision_trace']`:

```json
{
  "ranker_version": "context-aware-v3.0",
  "baseline_recovery_probability": 0.1146,
  "candidate_evaluations": [
    {
      "action": "PAYMENT_LINK",
      "probability": 0.456,
      "incremental_uplift": 0.3414,
      "cost_paise": 500,
      "expected_net_value_paise": 33640
    },
    {
      "action": "CUSTOMER_ACTION",
      "probability": 0.186,
      "incremental_uplift": 0.0714,
      "cost_paise": 200,
      "expected_net_value_paise": 6940
    },
    {
      "action": "RETRY",
      "probability": 0.181,
      "incremental_uplift": 0.0664,
      "cost_paise": 100,
      "expected_net_value_paise": 6540
    },
    {
      "action": "STOP",
      "probability": 0.0,
      "incremental_uplift": 0.0,
      "cost_paise": 0,
      "expected_net_value_paise": 0
    }
  ],
  "model_preferred_action": "PAYMENT_LINK",
  "preferred_expected_net_value_paise": 33640,
  "reason_codes": [
    "POSITIVE_INCREMENTAL_VALUE",
    "PAYMENT_LINK_MAX_VALUE"
  ]
}
```

---

## Metric Taxonomy: Model vs. Decision vs. Business Metrics

To prevent presentation and measurement ambiguity, Averiq strictly distinguishes between three metric tiers:

### 1. Model Quality Metrics (Supervised Learning Layer)
- **ROC-AUC & PR-AUC:** Discrimination ability per action model.
- **Brier Score & Expected Calibration Error (ECE):** Probability calibration quality over out-of-fold validation sets.
- *Role:* Validates that the underlying estimators output well-calibrated probabilities.

### 2. Decision Quality Metrics (Decision Intelligence Layer)
- **Intervention Rate:** Fraction of cases where an active recovery rail is chosen over `STOP`.
- **Action Distribution:** Share of choices across `RETRY`, `CUSTOMER_ACTION`, `PAYMENT_LINK`, and `STOP`.
- **Economic Regret:** Difference between the theoretical ex-post oracle net gain and the chosen action's net gain.
- **Oracle Agreement:** Fraction of cases where the model's ex-ante expected value ranking picks the exact action that produced the highest realized counterfactual payoff ex-post.
- *Role:* Diagnostic and policy auditing. **Oracle agreement is an exploratory diagnostic, NOT a primary production business KPI.**

### 3. Business & Financial Metrics (Production Executive Layer)
- **Organic Recovered Revenue:** $Y_0 \times \text{Amount}$ (revenue that recovers naturally without Averiq).
- **Gross Recovered Revenue:** $Y_a \times \text{Amount}$ (total revenue recovered under the strategy).
- **Incremental Recovered Revenue:** $(Y_a - Y_0) \times \text{Amount}$ (true treatment effect attributable to intervention).
- **Intervention Cost:** $\text{Cost}(a)$ (operational fees incurred).
- **Net Incremental Recovery Value:** $\text{Incremental Revenue} - \text{Intervention Cost}$.
- **Cost Reduction vs. Heuristic:** Direct operational budget savings achieved by intelligent intervention suppression.
- *Canonical Identity:* $\text{Gross Revenue} \equiv \text{Organic Revenue} + \text{Incremental Revenue}$.

