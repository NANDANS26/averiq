# Security Reference

## Authentication (SEC-AUTH-001)
JWT Bearer tokens. `POST /api/v1/auth/login` → access_token + refresh_token.

## RBAC Matrix (SEC-AUTH-002)
| Role | Cases Read | Write | Approve/Reject | Admin |
|------|-----------|-------|----------------|-------|
| VIEWER | ✓ | ✗ | ✗ | ✗ |
| OPERATOR | ✓ | ✓ | ✓ | ✗ |
| ADMIN | ✓ | ✓ | ✓ | ✓ |
| SYSTEM | Internal only — never assignable via API |

SYSTEM role protection: any API payload submitting `role='SYSTEM'` → HTTP 422.

## Webhook Security (FR-WEBHOOK-002)
HMAC-SHA256: `hmac.new(decrypt(merchant.webhook_secret), raw_body, sha256).hexdigest()`
Webhook secrets stored as Fernet-encrypted ciphertext (not bcrypt — must be retrievable).

## Merchant Isolation (SEC-ISOL-001)
All repositories inject `WHERE merchant_id = :authenticated_merchant_id`.
Cross-merchant access → `ForbiddenError` → HTTP 403.

## AI Security (SEC-AI-001 through SEC-AI-003)
- All LLM output validated through DiagnosisOutput Pydantic schema
- failure_category validated against FailureCategory enum allowlist
- LLM output NEVER directly authorises execution — must pass PolicyEngine gate

## Audit Safety (AUD-006)
`audit_events.payload` and the audit API response never contain:
raw gateway payloads, card numbers, bank account numbers, auth credentials, raw PII.
