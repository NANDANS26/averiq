# Averiq — Phase 6 Forensic Baseline Report
**AI Revenue Recovery Decision Engine**
**Pre-Implementation Audit & Scope Contract**

---

## 1. Verified Architecture & Existing Foundations

Averiq is an enterprise AI decision engine built to maximize net incremental recovery value for failed payments under strict policy, fraud, and risk constraints.

```
PAYMENT RISK
     ↓
DECISION INTELLIGENCE (T-Learner LightGBM)
     ↓
POLICY / SAFETY (Deterministic Rule Engine)
     ↓
APPROVAL (Human-in-the-loop Gate)
     ↓
EXECUTION (Idempotent Outbox & Gateway Adapter)
     ↓
VERIFICATION (Asynchronous Evidence Engine)
     ↓
REVENUE ATTRIBUTION (Integer-Paise Canonical Surplus)
```

### Verified Pipeline Invariants (FROZEN)
1. **Decision Intelligence**: Calibrated 4-action T-Learner LightGBM (`NO_INTERVENTION`, `PAYMENT_LINK`, `RETRY`, `CUSTOMER_ACTION`).
2. **Deterministic Economics**:
   $$\text{Incremental Recovery} = (Y_{\text{selected}} - Y_0) \times \text{Amount}$$
   $$\text{Net Incremental Surplus} = \text{Incremental Recovery} - \text{Intervention Cost}$$
   - Preserves true negative incremental recovery when $Y_{\text{selected}} < Y_0$.
   - Strictly integer paise arithmetic; zero floating point currency representations.
3. **Fallback Base-Rate**: Unconditional $P_0 = 0.35$ fallback when models or artifacts are unavailable. Zero references to 0.70 fallback probability.
4. **Safety & Policy Engine**: Hard checks for suspected fraud, maximum case amounts, merchant cooldowns, and confidence thresholds. Terminal `STOP` (`NO_INTERVENTION`) actions always pass policy.
5. **State Orchestration & Idempotency**:
   - 10-state formal state machine (`DETECTED`, `EVALUATED`, `ACTION_PROPOSED`, `AWAITING_APPROVAL`, `ACTION_APPROVED`, `EXECUTING`, `VERIFYING`, `RECOVERED`, `FAILED`, `ESCALATED`, `STOPPED`).
   - Redis concurrency locks and idempotency keys guarantee exactly-once external gateway execution.
   - `CUSTOMER_ACTION` is classified as `INTERNAL_ACTION` with 0 external gateway calls and 0 verification attempts.
6. **Backend Infrastructure**:
   - PostgreSQL 18 with async SQLAlchemy models.
   - Redis 6379 broker and Celery background workers.
   - Multi-tenant tenant scoping enforced at API and database query levels.

---

## 2. Current Product Surfaces Audit

### Backend APIs (`backend/api/`)
- `GET /api/v1/cases`: Paginated case list with risk score, tier, and search.
- `GET /api/v1/cases/{id}`: Detailed case record.
- `GET /api/v1/cases/{id}/trace`: Mathematical explainability trace, baseline $P_0$, candidate action net expected values, reason codes.
- `GET /api/v1/cases/{id}/audit`: Redacted audit log timeline.
- `GET /api/v1/analytics/recovery-canonical`: Canonical Phase 3.6 economic breakdown across 7d, 30d, all-time cohorts.
- `GET /api/v1/analytics/revenue`: Revenue-at-risk aggregation across non-terminal states.
- `GET /api/v1/analytics/attribution`: Counterfactual attribution summary.
- `GET /health`, `GET /readiness`, `GET /api/v1/system/model-health`: Liveness, dependency latency checks, and ML model metadata.
- `backend/simulation/demo_fixtures.py`: Pre-seeded scenarios `DEMO-001` through `DEMO-008` marked with `is_demo_fixture = True`.

### Frontend Surfaces (`frontend/src/pages/`)
- `OverviewDashboard.tsx`: Basic KPI tiles, partial attribution metrics.
- `RevenueRiskQueue.tsx`: Case list table with status badges and search.
- `CaseIntelligence.tsx`: Detailed view with candidate probabilities, execution status, and audit log.
- `RecoveryAnalytics.tsx`: Cohort trends and recovery metrics.
- `Settings.tsx`, `HumanReviewQueue.tsx`, `WebhookMonitor.tsx`, `FailureLab.tsx`.

---

## 3. Existing Gaps & Phase 6 Improvements

| Surface / Area | Current State | Phase 6 Requirement |
| :--- | :--- | :--- |
| **Information Architecture** | Disconnected routes (`/cases/:id/interventions`, `/cases/:id/execution`, etc.) | Streamlined executive navigation: **Overview**, **Risk Queue**, **Operations**, **Analytics**, **System Health**, **Demo Center**. Consolidated Case Detail with unified tabs. |
| **Executive Overview** | Basic cards, missing negative incremental awareness and live funnels | Executive Fintech Dashboard: Live funnel ($\text{At Risk} \to \text{Diagnosed} \to \text{Intervention} \to \text{Verifying} \to \text{Recovered}$), unclipped canonical KPIs with explicit tooltips, state & intervention mix charts. |
| **Case Detail & Explainability** | Tabbed panels with technical jargon | Hiring-grade Case Detail: Executive Header, Risk Assessment, Decision Comparison Matrix, Policy Pass/Block breakdown, Execution & Verification cards, and Audit Timeline. |
| **Decision Trace** | Generic candidate JSON list | Deterministic explainability: Baseline $P_0$, expected incremental uplift $\Delta Y$, cost penalty, net surplus, and explicit reason code. |
| **Operations Center** | Scattered review/execution pages | Unified Operations Center: Pending human approvals, active gateway executions, verifying interventions, failed operations, and escalations. |
| **Demo Center** | Runbook markdown file only | Dedicated interactive Demo Center UI: Direct trigger/inspection of `DEMO-001` through `DEMO-008`, Golden path, Gateway failure, Fraud block, and Fallback scenarios with live state reflections. |
| **System Health & Observability** | Separate basic JSON endpoints | System Health Dashboard: Visual dependency status (FastAPI, Postgres, Redis, Celery, Model artifact), latency gauges, correlation ID inspection. |
| **Design System & A11y** | Mixed light/dark styling | Stripe/Datadog-grade dark-first fintech design system, keyboard accessible, WCAG compliant contrast, clear hierarchy. |

---

## 4. Frozen Components vs Phase 6 Scope

### Components That MUST Remain Untouched (FROZEN)
- `backend/ml/` (inference models, T-Learner pipelines, feature stores, scaler/encoder weights).
- `backend/domain/` (database ORM models, state definitions, monetary schema types).
- `backend/state_machine.py` (transitions, invariants, lock semantics).
- `backend/policy/engine.py` (policy checks, thresholds, freeze rules).
- `backend/config.py` (fallback probability 0.35, environment enums).
- `backend/simulation/full_verification_matrix.py` (Phase 4 authoritative suite).

### Files to Extend / Create (Phase 6 Scope)
1. **Frontend Architecture & Navigation**:
   - Update `frontend/src/components/AppLayout.tsx` with executive navigation bar, breadcrumbs, and live operating mode indicator.
   - Refactor `frontend/src/App.tsx` routes.
2. **Frontend Pages**:
   - `frontend/src/pages/OverviewDashboard.tsx`: Executive dashboard with live funnels and canonical metrics.
   - `frontend/src/pages/RevenueRiskQueue.tsx`: Operator risk queue with multi-filtering and sorting.
   - `frontend/src/pages/CaseIntelligence.tsx`: Polished hiring-grade Case Detail & Decision Trace screen.
   - `frontend/src/pages/OperationsCenter.tsx` (new): Unified operations view for approvals, executions, and verifications.
   - `frontend/src/pages/DemoCenter.tsx` (new): Interactive runner and inspector for DEMO-001–008.
   - `frontend/src/pages/SystemHealth.tsx` (new): Visual infrastructure and ML model readiness console.
3. **Backend Safe Additions**:
   - `backend/api/demo.py` (new): Dedicated safe router to run and query demo fixtures (`/api/v1/demo/scenarios`, `/api/v1/demo/run/{scenario_id}`) scoped strictly to `is_demo_fixture = True`.
   - Mount demo router in `backend/main.py`.
4. **Documentation & Hiring-Grade Artifacts**:
   - `docs/AVERIQ_TECHNICAL_CASE_STUDY.md`: Deep technical architectural case study.
   - `docs/AVERIQ_INTERVIEW_CHEATSHEET.md`: 20-question interviewer cheat sheet.
   - `docs/AVERIQ_ARCHITECTURE.md`: Architecture specification with Mermaid sequence and state diagrams.
   - `docs/AVERIQ_5_MINUTE_DEMO.md`: Rehearsal demo script.
   - `README.md`: Upgraded executive portfolio README.
   - `docs/PHASE_6_COMPLETION_REPORT.md` and `docs/PHASE_6_REGRESSION_REPORT.md`.

---

## 5. Verification Baseline Metrics

- **Current Backend Test Suite**: `700 passed, 0 failures` (in 89.20s).
- **Current Frontend Build**: `tsc && vite build` passed (`0 errors`, bundle size 385kB charts chunk).
- **Decision Invariant**: Unclamped canonical economics, $P_0 = 0.35$ fallback, 0 external operations on `CUSTOMER_ACTION`.

Baseline verified. Proceeding with Phase 6 implementation.
