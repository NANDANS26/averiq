"""API layer — FastAPI routers for REST endpoints.

Routers in this package:
- auth.py       — JWT login, refresh, logout
- dependencies.py — shared FastAPI dependencies (auth, RBAC)
- cases.py      — /api/v1/cases endpoints
- interventions.py — /api/v1/interventions endpoints
- policy.py     — /api/v1/policy endpoints
- analytics.py  — /api/v1/analytics endpoints
- models.py     — /api/v1/models lifecycle endpoints
- webhooks.py   — /api/v1/webhooks/razorpay endpoint
- simulation.py — /api/v1/simulation/failures endpoints (Failure Lab)
"""
