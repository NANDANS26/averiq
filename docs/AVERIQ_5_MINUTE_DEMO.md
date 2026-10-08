# Averiq — 5-Minute Executive Demo Script

This script provides an exact chronological walkthrough to demonstrate Averiq to technical interviewers, executives, or hiring committees.

---

### Minute 0:00 – 0:30 • The Problem & System Purpose
**Action**: Open `http://localhost:5173/` (Overview Dashboard).  
**Script**:  
> "Averiq is an AI Revenue Recovery Decision Engine. In typical fintech infrastructure, failed payments are retried blindly or managed with fragile if/else heuristics. This wastes merchant fees, annoys customers, and ignores organic recovery. Averiq turns recovery into a context-aware decision engine that ranks interventions by *net incremental value* while enforcing strict policy and fraud constraints."

---

### Minute 0:30 – 1:00 • The Executive Overview & Canonical Economics
**Action**: Point to the Executive Overview KPI cards and the live Pipeline Funnel.  
**Script**:  
> "Here on the executive overview, all figures are computed in real-time from our PostgreSQL database. Notice our four primary economic metrics: Revenue at Risk, Gross Recovered, Incremental Revenue, and Net Incremental Surplus. Under Phase 3.6 canonical accounting, Incremental Revenue measures the recovery attributable to our model above the organic baseline ($P_0$), and Net Surplus subtracts action costs in integer paise. If an intervention underperforms organic baseline, we preserve negative incremental surplus honestly without artificial zero-clamping."

---

### Minute 1:00 – 2:00 • Revenue Risk Queue & Opening a Case
**Action**: Click **Risk Queue** in the top navigation. Filter by "All States". Click on a case ID to open **Case Detail**.  
**Script**:  
> "The Revenue Risk Queue gives operators a prioritized view of all active failed payments. Opening this case brings us to the core demo screen: Case Detail and Decision Intelligence."

---

### Minute 2:00 – 2:45 • Decision Explainability Matrix
**Action**: On the Case Detail screen, point to the **Decision Intelligence** tab.  
**Script**:  
> "Averiq is completely transparent. For this case, our T-Learner LightGBM model evaluated all four discrete actions: `NO_INTERVENTION`, `PAYMENT_LINK`, `RETRY`, and `CUSTOMER_ACTION`.  
> - Our counterfactual organic baseline $P_0$ is evaluated.
> - For each action, we compute the predicted probability, uplift $\Delta P$, expected gross value, action cost, and net surplus.
> - The model chose `PAYMENT_LINK` because it produced the highest net surplus. Below, our deterministic explanation details the mathematical reasoning without any LLM hallucinations."

---

### Minute 2:45 – 3:30 • Policy & Human Governance Gate
**Action**: Click on the **Policy & Safety** tab.  
**Script**:  
> "Machine learning predicts, but policy controls. Here we see our automated guardrails: fraud detection, merchant cooldown, and maximum exposure limits. If a transaction exceeds the human approval threshold (e.g. ₹10,000), automated execution is paused in `AWAITING_APPROVAL`. An operator must manually approve it before any external gateway operation can execute."

---

### Minute 3:30 – 4:15 • Interactive Demo Center & Fault Tolerance
**Action**: Click **Demo Center** in the top navigation bar.  
**Script**:  
> "To test Averiq under adverse conditions, our Demo Center exposes 8 deterministic scenarios:
> - `DEMO-001`: Golden recovery path via payment link.
> - `DEMO-002`: High-value human approval gate.
> - `DEMO-003`: Fraud policy violation triggering an immediate `STOP`.
> - `DEMO-004`: Gateway 502 Bad Gateway failure handling.
> - `DEMO-006`: Webhook deduplication with idempotency keys.
> - `DEMO-008`: Model offline fallback triggering our calibrated base rate $P_0 = 0.35$."  
> *(Click "Execute Scenario" on DEMO-001, then click "Inspect Case Decision Trace" on the resulting success banner.)*

---

### Minute 4:15 – 5:00 • System Health & Engineering Architecture
**Action**: Click **System** in the top navigation.  
**Script**:  
> "Finally, our System Health dashboard shows live dependency probes: PostgreSQL 18 with integer-paise schema, Redis 6379 for Celery and distributed mutex locks, and our calibrated LightGBM model artifact.
> In summary: Averiq is a production-hardened simulation and decision platform backed by 704 passed tests, 15/15 verified E2E state machine scenarios, and zero float arithmetic."
