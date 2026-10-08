# REST API Reference

## Base URL
All endpoints: `http://localhost:8000/api/v1/`

## Authentication
Bearer JWT token in `Authorization` header.
Tokens obtained via `POST /api/v1/auth/login`.

## Error Response Shape (API-007)
All 4xx/5xx responses:
```json
{"error": "ERROR_CODE", "detail": [...], "request_id": "uuid"}
```

## Endpoint Summary

### Auth
- `POST /auth/login` — exchange credentials for access + refresh tokens
- `POST /auth/refresh` — exchange refresh token for new access token
- `POST /auth/logout` — revoke current token

### Cases
- `GET /cases` — paginated cursor-based list
- `GET /cases/{id}` — full case detail
- `GET /cases/{id}/trace` — decision trace
- `GET /cases/{id}/audit` — audit history (no raw payloads per AUD-006)
- `GET /cases/{id}/interventions` — interventions for case

### Interventions
- `GET /interventions/{id}` — single intervention detail
- `POST /interventions/{id}/approve` — approve human review (OPERATOR+)
- `POST /interventions/{id}/reject` — reject human review (OPERATOR+)

### Analytics
- `GET /analytics/revenue` — revenue at risk summary (FR-RISK-006, FR-RISK-007)
- `GET /analytics/attribution` — attribution report (FR-ATTR-003)
- `GET /analytics/metrics` — named metrics summary

### Policies
- `GET /policies` — list policies (VIEWER+)
- `GET /policies/active` — active policy (VIEWER+)
- `POST /policies` — create draft (ADMIN)
- `POST /policies/{id}/activate` — promote to active (ADMIN)

### Models
- `POST /models/{id}/promote` — advance model lifecycle (ADMIN)

### System
- `GET /status` — operating mode, version, failure lab flag
- `GET /health` — liveness probe (no auth required)

### Webhooks
- `POST /webhooks/razorpay/{merchant_id}` — ingest Razorpay webhook (no auth, HMAC-verified)

### Human Review
- `GET /review` — pending review queue (OPERATOR+)

### Simulation (Failure Lab)
- `GET /simulation/failures` — list scenarios
- `POST /simulation/failures/{scenario}` — trigger scenario (requires ENABLE_FAILURE_LAB=true)
