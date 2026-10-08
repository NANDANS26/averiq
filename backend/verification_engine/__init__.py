"""Verification Engine — confirms whether an executed intervention recovered revenue.

Dispatches to a ReconciliationStrategy based on intervention_type:
- PaymentLinkReconciliationStrategy     — checks link paid AND payment captured
- SubscriptionReconciliationStrategy    — checks subscription active AND overdue payment captured
- InternalActionReconciliationStrategy  — checks internal workflow completion
- RecommendationOnlyReconciliationStrategy — outcome = RECOMMENDATION_DELIVERED (no external call)

Re-verification is scheduled on UNKNOWN outcomes; escalates after 3 failed attempts.

Modules:
- engine.py    — VerificationEngine dispatcher and reconciliation strategies
- scheduler.py — Re-verification scheduling with escalation (FR-VERIFY-006, FR-VERIFY-007)
- tasks.py     — Celery task entry-points for async re-verification
"""
