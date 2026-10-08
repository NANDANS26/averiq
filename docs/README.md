# Averiq Documentation Index

Welcome to the comprehensive documentation directory for **Averiq** — AI Revenue Recovery Decision Engine.

This repository represents an end-to-end engineered, verified, and audited fintech platform for payment recovery decision intelligence and orchestration.

---

## 1. System Architecture & Case Studies

- [**Technical Case Study**](AVERIQ_TECHNICAL_CASE_STUDY.md): High-level system overview, problem statement, business impact, counterfactual economics, and architectural walkthrough.
- [**Architecture Specification**](AVERIQ_ARCHITECTURE.md): Comprehensive system blueprints, component interaction diagrams, data models, state machine flowcharts, and verification loops.
- [**Domain Model & Schemas**](domain-model.md): Entity relationships, integer-paise representation, and database definitions.
- [**API Reference**](api.md): Complete OpenAPI endpoint documentation, request/response models, and status codes.
- [**Webhook Design**](webhook-design.md): Ingestion architecture, signature validation, idempotency hash checks, and queue routing.
- [**Security Architecture**](security.md): Threat modeling, AES-256-GCM secret encryption, RBAC permissions, and multi-tenant isolation.
- [**Configuration Specification**](CONFIGURATION.md): Complete breakdown of environment variables, tuning knobs, and deployment configurations.

---

## 2. Decision Intelligence & Economics

- [**Decision Intelligence Overview**](DECISION_INTELLIGENCE.md): LightGBM T-Learner uplift modeling, isotonic calibration, and contextual ranking.
- [**Decision Engine Deep Dive**](decision-engine.md): Detailed mathematical formulation of Net Incremental Surplus, cost functions, and fallback behaviors.
- [**Model Evaluation Report**](evaluation.md): Offline validation benchmarks, calibration curves, and ROC-AUC / log-loss metrics.
- [**Model Card**](../MODEL_CARD.md): Formal model governance card covering training data, limitations, bias checks, and operational boundaries.

---

## 3. Engineering Audit & Defense

- [**Final Claim Evidence Matrix**](FINAL_CLAIM_EVIDENCE_MATRIX.md): Comprehensive line-by-line verification evidence cross-referencing all 704 tests and 15 E2E scenarios.
- [**Averiq Final Defense**](AVERIQ_FINAL_DEFENSE.md): Senior engineer and jury-grade technical defense explaining architectural trade-offs, edge cases, and operational resiliency.
- [**Red Flag Audit**](AVERIQ_RED_FLAG_AUDIT.md): Hostile engineering review analyzing race conditions, distributed locking, state-machine soundness, and security posture.
- [**Interview Cheat Sheet**](AVERIQ_INTERVIEW_CHEATSHEET.md): 20 critical engineering questions and answers covering payments, ML uplift, distributed systems, and economics.
- [**5-Minute Demo Script**](AVERIQ_5_MINUTE_DEMO.md): Step-by-step presentation walkthrough showcasing live recovery simulations, decision explanations, and human approvals.

---

## 4. Phase Verification & Gating Reports

- [**Phase 6 Baseline Report**](PHASE_6_BASELINE.md): Pre-implementation baseline audit and contract freeze.
- [**Phase 6 Completion Report**](PHASE_6_COMPLETION_REPORT.md): Final verification report for Phase 6 productization and demo readiness.
- [**Phase 6 Regression Report**](PHASE_6_REGRESSION_REPORT.md): Complete regression test execution output (704 passed tests, 15/15 E2E matrix).
- [**Phase 5 Re-Gating Report**](PHASE_5_RE_GATING_REPORT.md): Audit and verification evidence for observability and human approval gates.
- [**Phase 4 Targeted Audit Report**](PHASE_4_TARGETED_AUDIT_REPORT.md): Detailed verification of execution state transitions, customer action invariants, and retry loops.
- [**Phase 4 E2E Results**](PHASE_4_E2E_RESULTS.md): Tabular and JSON results of the authoritative 15-scenario end-to-end matrix.
