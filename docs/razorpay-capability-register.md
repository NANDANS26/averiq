# Razorpay API Capability Register

This document records the verified status of each Razorpay API operation
used or considered by Averiq. It is the authoritative source for the
`ProviderSupportStatus` of each operation.

**Last synchronized**: Implementation complete

## Terminology

| Status | Meaning |
|--------|---------|
| VERIFIED | Confirmed in official Razorpay documentation |
| UNVERIFIED | Not confirmed in documentation |
| NOT_SUPPORTED | Confirmed to NOT exist in the Razorpay API |

## Capability Register

### Payment Links (Payment Recovery — Primary Mechanism)

| Operation | Endpoint | Status | Notes |
|-----------|----------|--------|-------|
| Create Payment Link | `POST /v1/payment_links` | **VERIFIED** | Primary recovery mechanism. Amount in paise. |
| Get Payment Link | `GET /v1/payment_links/:id` | **VERIFIED** | Used for verification (status = 'paid'). |
| Get Payment | `GET /v1/payments/:id` | **VERIFIED** | Used to confirm capture status. |

### Subscriptions

| Operation | Endpoint | Status | Notes |
|-----------|----------|--------|-------|
| Get Subscription | `GET /v1/subscriptions/:id` | **VERIFIED** | Returns status: active/pending/halted/cancelled. |
| Pause Subscription | `POST /v1/subscriptions/:id/pause` | **UNVERIFIED** | Not confirmed as programmatic path. |

### Payments — CRITICAL SAFETY NOTES

| Operation | Endpoint | Status | Notes |
|-----------|----------|--------|-------|
| Retry Failed Payment | N/A | **NOT_SUPPORTED** | Razorpay has NO API to retry a failed standalone payment. RETRY = RECOMMENDATION_ONLY. |
| Capture Failed Payment | `POST /v1/payments/:id/capture` | **PROHIBITED** | MUST NOT be called on `status='failed'` payments. Only valid for `status='authorized'`. |

**INT-RAZORPAY-006**: Never call `POST /v1/payments/:id/capture` on a payment
with `status='failed'`. This is enforced by the absence of `capture_payment()`
from `GatewayAbstraction`.

### Webhooks

| Event | Notes |
|-------|-------|
| `payment.failed` | Primary trigger for case creation |
| `subscription.charged` | Subscription payment attempt |
| `payment.authorized` | Payment authorized but not captured |
| `x-razorpay-event-id` | Deduplication header — must be preserved verbatim |

## Test Mode Limits

| Limit | Value | Notes |
|-------|-------|-------|
| Payment Links per business | ~30 | Applies in REAL_TEST_MODE |
| Payment Links in SIMULATED | Unlimited | Uses FakeRazorpayGateway |

When `payment_link_usage.link_count` approaches the test limit, the
Intervention Engine automatically substitutes RECOMMENDATION_ONLY (FR-INTVN-007).

## HMAC Signature Verification

Razorpay signs webhook payloads with:
```
HMAC-SHA256(raw_body, webhook_secret)
```
Header: `X-Razorpay-Signature`

The webhook secret must be stored as Fernet-encrypted ciphertext (SEC-AUTH-012)
and decrypted at verification time — NOT hashed with bcrypt/Argon2.

## Operating Modes

| Mode | Gateway | Notes |
|------|---------|-------|
| `SIMULATED` | `FakeRazorpayGateway` | No external calls; safe for dev/CI |
| `REAL_TEST_MODE` | `RazorpayTestGateway` | Real Razorpay test-mode credentials required |

## Source References

| Component | File |
|-----------|------|
| GatewayAbstraction | `backend/gateway/abstract.py` |
| FakeRazorpayGateway | `backend/gateway/fake_razorpay.py` |
| RazorpayTestGateway | `backend/gateway/razorpay_test.py` |
| PaymentLinkReconciliation | `backend/verification_engine/engine.py` |
