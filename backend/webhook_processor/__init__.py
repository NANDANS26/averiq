"""Webhook Processor — handles incoming Razorpay webhook events.

Architecture: synchronous HTTP handler → outbox → async Celery worker.

Delivery model: at-least-once + idempotent (NOT exactly-once). Every
component that receives duplicate deliveries handles them via the
UNIQUE(merchant_id, provider_event_id) dedup check.

Modules:
- handler.py   — synchronous POST handler (HMAC verify, dedup, outbox insert)
- outbox.py    — Celery beat task: polls outbox every 30s, publishes to queue
- processor.py — async Celery worker: processes events, drives state machine
"""
