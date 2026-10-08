# Webhook Design

This document describes the webhook ingestion pipeline, outbox pattern, at-least-once
delivery semantics, deduplication strategy, rate limiting, and the Razorpay API Capability
Register update process.

---

## Outbox Pattern and At-Least-Once Semantics

Averiq uses the **transactional outbox pattern** for webhook delivery. The synchronous
HTTP handler does NOT publish directly to Celery. Instead:

1. The handler atomically commits two records in a single DB transaction:
   - `webhook_events` row (status = `PERSISTED`)
   - `outbox_events` row (status = `enqueued = false`)
2. A Celery Beat task polls the outbox every 30 seconds using `SELECT FOR UPDATE SKIP LOCKED`
   (queue consumption semantics — SKIP LOCKED is intentional here, unlike case state transitions
   which use `SELECT FOR UPDATE` without SKIP LOCKED).
3. The beat task publishes each un-enqueued record to Celery and marks `enqueued = true`.

### Failure Modes

| Scenario | Outcome |
|----------|---------|
| DB commit succeeds, beat not yet run | Outbox record persists; picked up within 30s |
| Beat publishes then crashes before `enqueued=true` | Duplicate Celery task delivery; idempotency in processor absorbs duplicate |
| Queue unavailable | Outbox stays `enqueued=false`; beat retries on next cycle |

This is **at-least-once + idempotent**, not exactly-once. Every downstream consumer
of outbox-delivered messages must handle duplicate deliveries correctly.

---

## Deduplication Strategy

### Primary Key: `x-razorpay-event-id`

The `x-razorpay-event-id` header is the canonical deduplication key for Razorpay webhook
events. Averiq stores this value verbatim as `provider_event_id` in `webhook_events`.

The unique constraint `UNIQUE(merchant_id, provider_event_id)` is the DB-level deduplication
guard. Any duplicate delivery with the same `(merchant_id, provider_event_id)` pair returns
HTTP 200 immediately without creating a domain event.

**Note on status update (task 22.4)**: The `x-razorpay-event-id` header status was
corrected from `UNVERIFIED` to `VERIFIED` in `requirements.md` Section 6. The authoritative
register is maintained in `docs/razorpay-capability-register.md`.

### Fallback Dedup Key (Degraded Mode)

When the `x-razorpay-event-id` header is absent or malformed:
- A structured warning is logged with hashed `merchant_id`
- Metric `webhook_missing_event_id_total` is incremented
- A fallback key is synthesized from: `entity_id + event_type + provider_event_time_bucket`
- The fallback key is stored as `provider_event_id` for this request only

**Important**: the fallback key is NOT equivalent to a real provider event ID. It may not
survive a legitimate re-delivery of the same event. This is degraded-mode behavior only.

---

## Rate Limiting

A Redis token bucket rate limiter is applied per `merchant_id` **before** the dedup check,
to avoid unnecessary DB writes on abuse bursts.

- Threshold: `WEBHOOK_RATE_LIMIT_PER_MINUTE` (default: 300)
- Breach response: HTTP 429 + `Retry-After` header
- Logging: structured warning with hashed `merchant_id` and burst count on every 429

**Design rationale**: HTTP 429 causes Razorpay to retry per its exponential backoff policy
(up to 24 hours), so legitimate events are not silently lost. The rate limit is set
conservatively to protect against abuse bursts, not legitimate provider traffic.

---

## Capability Register Update Process

The Razorpay API Capability Register is maintained in two places:

1. **`requirements.md` Section 6** — the inline register in the spec, used during design
   and requirements review. This is the human-readable source of truth referenced by all
   requirement IDs.

2. **`docs/razorpay-capability-register.md`** — the standalone authoritative register,
   used by implementation and operations teams. Contains full detail including safety notes,
   test mode limits, and source file references.

### How to Update the Register

When a Razorpay API capability changes status (e.g., from UNVERIFIED to VERIFIED):

1. Update `requirements.md` Section 6 — change the `Status` column and update the `Notes`
   to reference `docs/razorpay-capability-register.md`.
2. Update `docs/razorpay-capability-register.md` — update the corresponding row in the
   relevant section and update the **Last synchronized** date.
3. If the status change affects any integration requirement (INT-RAZORPAY-*), update the
   requirement body to reflect the verified behavior.
4. If the change enables a new execution capability (e.g., UNVERIFIED → VERIFIED), update
   `ProviderCapabilityConfig` and the Intervention Engine accordingly.
5. If the change removes a previously assumed capability (e.g., VERIFIED → NOT_SUPPORTED),
   audit all code paths that assumed the capability and update them.

### Register Update Log

| Date | Operation | Old Status | New Status | Reason |
|------|-----------|-----------|-----------|--------|
| Implementation complete | `x-razorpay-event-id` dedup header | UNVERIFIED | VERIFIED | Confirmed usage in webhook ingestion pipeline (task 22.4); used as primary dedup key per design Known Limitations #9 and Webhook Architecture section |

---

## Synchronous Handler — Step-by-Step

The synchronous webhook handler (`POST /api/v1/webhooks/razorpay`) performs exactly these
steps in order, with no Celery publish in the HTTP path:

1. Capture `raw_body` as bytes before any parsing (FR-WEBHOOK-001)
2. Extract `x-razorpay-event-id` header → `provider_event_id` (with fallback for missing header)
3. Rate limit check (Redis token bucket per `merchant_id`) — HTTP 429 on breach (FR-WEBHOOK-014)
4. HMAC-SHA256 verify using `hmac.compare_digest` — HTTP 401 on failure (FR-WEBHOOK-002, FR-WEBHOOK-003)
5. Schema-validate parsed payload; log violations without crashing (FR-WEBHOOK-012)
6. Dedup check `SELECT WHERE (merchant_id, provider_event_id)` — HTTP 200 if duplicate (FR-WEBHOOK-009)
7. Single DB transaction: `INSERT webhook_events` (status=PERSISTED) + `INSERT outbox_events` (enqueued=false)
8. COMMIT — durable before HTTP response
9. Return HTTP 200

No AI, ML, or external calls are made in this path (FR-WEBHOOK-005, FR-WEBHOOK-006).

---

## Source References

| Component | File |
|-----------|------|
| Synchronous handler | `backend/webhook_processor/handler.py` |
| Outbox beat task | `backend/webhook_processor/outbox.py` |
| Async processor | `backend/webhook_processor/processor.py` |
| Capability register | `docs/razorpay-capability-register.md` |
