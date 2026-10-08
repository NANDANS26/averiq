"""webhook and outbox tables

Revision ID: 002
Revises: 001
Create Date: 2024-01-01 00:00:00.000000

Creates the webhook ingestion and transactional outbox tables:

    webhook_events         — normalised inbound events; primary dedup key is
                             UNIQUE(merchant_id, provider_event_id) where
                             provider_event_id is the value of the
                             x-razorpay-event-id request header.

    webhook_raw_payloads   — optional byte-for-byte retention of the raw
                             request body.  Only created when the
                             WEBHOOK_RETAIN_RAW_PAYLOAD configuration flag is
                             enabled (default: false).  The table is always
                             created by this migration; the application layer
                             controls whether rows are ever written.

    outbox_events          — transactional outbox that decouples HTTP request
                             handling from Celery task publishing.  The partial
                             index idx_outbox_unenqueued makes the outbox beat
                             poller fast: it scans only un-enqueued rows.

Requirements covered: FR-WEBHOOK-004, FR-WEBHOOK-007, FR-WEBHOOK-008
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# ---------------------------------------------------------------------------
# Revision identifiers — used by Alembic to build the migration chain.
# ---------------------------------------------------------------------------
revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    # ------------------------------------------------------------------
    # webhook_events
    #
    # Primary dedup key: UNIQUE(merchant_id, provider_event_id)
    #
    # provider_event_id  — the verbatim value of the x-razorpay-event-id
    #                      request header.  When that header is absent or
    #                      malformed the application writes a deterministic
    #                      fallback key derived from entity_id + event_type
    #                      + a provider_event_time bucket (documented as
    #                      degraded-mode behaviour in the webhook handler).
    #
    # processing_status  — lifecycle of this event through the async
    #                      pipeline.  Valid values are enforced by a CHECK
    #                      constraint:
    #
    #   RECEIVED          — persisted by the synchronous HTTP handler; the
    #                       outbox record is about to be written in the same
    #                       transaction.
    #   PERSISTED         — webhook_events and outbox_events rows committed
    #                       atomically; HTTP 200 returned to Razorpay.
    #   QUEUED            — outbox poller published the Celery task and
    #                       marked outbox_events.enqueued = TRUE.
    #   PROCESSING        — async worker has claimed the task and is
    #                       applying the event to the domain layer.
    #   PROCESSED         — the event was successfully applied (case created
    #                       or updated).  Does NOT imply the payment has been
    #                       recovered; that is tracked in revenue_risk_cases.
    #   FAILED_RETRYABLE  — transient failure; will be retried with
    #                       exponential backoff (max 5 attempts).
    #   DEAD_LETTERED     — max retries exhausted; requires manual
    #                       intervention.
    # ------------------------------------------------------------------
    op.create_table(
        "webhook_events",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "merchant_id",
            sa.UUID(),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Value of the x-razorpay-event-id header; primary dedup key
        # (see UNIQUE constraint uq_webhook_merchant_event below).
        sa.Column(
            "provider_event_id",
            sa.String(255),
            nullable=False,
        ),
        # e.g. "payment.failed", "subscription.charged"
        sa.Column(
            "event_type",
            sa.String(100),
            nullable=False,
        ),
        # Lifecycle status — see comment block above for semantics.
        sa.Column(
            "processing_status",
            sa.String(20),
            nullable=False,
            server_default="RECEIVED",
        ),
        # Razorpay entity ID extracted from the webhook payload
        # (payment ID, subscription ID, etc.).
        sa.Column("entity_id", sa.String(255), nullable=True),
        # "payment", "subscription", "order", …
        sa.Column("entity_type", sa.String(50), nullable=True),
        # Timestamp from the Razorpay payload (provider time, UTC).
        sa.Column(
            "event_timestamp",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        # Retry counter for FAILED_RETRYABLE → eventual DEAD_LETTERED.
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        # Human-readable description of the last processing error.
        sa.Column("last_error", sa.Text(), nullable=True),
        # Server time at which Averiq received the event (UTC).
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        # ----------------------------------------------------------
        # Constraints
        # ----------------------------------------------------------
        # Primary dedup key — at-most-one row per (merchant, provider event).
        sa.UniqueConstraint(
            "merchant_id",
            "provider_event_id",
            name="uq_webhook_merchant_event",
        ),
        # Enum-style guard on processing_status.
        sa.CheckConstraint(
            "processing_status IN ("
            "'RECEIVED','PERSISTED','QUEUED','PROCESSING',"
            "'PROCESSED','FAILED_RETRYABLE','DEAD_LETTERED')",
            name="ck_webhook_events_processing_status",
        ),
    )

    # ------------------------------------------------------------------
    # Indexes on webhook_events
    #
    # idx_webhook_pending (partial)
    #   Used by the outbox beat task and async processor to poll for
    #   events that still need work.  The WHERE clause keeps the index
    #   small by excluding PROCESSED and DEAD_LETTERED rows.
    # ------------------------------------------------------------------
    op.create_index(
        "idx_webhook_pending",
        "webhook_events",
        ["merchant_id", "received_at"],
        postgresql_where=sa.text(
            "processing_status IN ('RECEIVED','PERSISTED','FAILED_RETRYABLE')"
        ),
    )

    # idx_webhook_entity (partial)
    #   Correlates incoming events to existing domain entities
    #   (e.g. payment ID → RevenueRiskCase lookup).  The WHERE clause
    #   avoids indexing rows where entity_id was never set.
    op.create_index(
        "idx_webhook_entity",
        "webhook_events",
        ["merchant_id", "entity_id"],
        postgresql_where=sa.text("entity_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # webhook_raw_payloads  (optional retention table)
    #
    # Application-level feature flag: WEBHOOK_RETAIN_RAW_PAYLOAD
    # (default: false).  When disabled, the application never writes
    # rows to this table; no data is stored, but the schema is always
    # present so that enabling the flag requires no migration.
    #
    # Design constraints:
    #   • raw_body is BYTEA — never TEXT — to preserve exact byte sequence
    #     required for signature re-verification.
    #   • UNIQUE(webhook_event_id) ensures at-most-one raw payload per event.
    #   • expires_at drives a future background purge job; the index
    #     idx_raw_payload_expires makes range deletes efficient.
    #   • No public API endpoint exposes this table (restricted DB role).
    #
    # Requirements: FR-WEBHOOK-012, DATA-006
    # ------------------------------------------------------------------
    op.create_table(
        "webhook_raw_payloads",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "webhook_event_id",
            sa.UUID(),
            sa.ForeignKey("webhook_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Raw bytes of the original HTTP request body — required for
        # re-verification of the HMAC-SHA256 signature (FR-WEBHOOK-002).
        sa.Column("raw_body", sa.LargeBinary(), nullable=False),
        sa.Column(
            "content_type",
            sa.String(100),
            nullable=False,
            server_default="application/json",
        ),
        # Retention deadline; records beyond this date may be purged.
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        # At-most-one raw payload per webhook event.
        sa.UniqueConstraint(
            "webhook_event_id",
            name="uq_raw_payload_event",
        ),
    )

    # Supports efficient range-delete purges when retaining raw payloads.
    op.create_index(
        "idx_raw_payload_expires",
        "webhook_raw_payloads",
        ["expires_at"],
    )

    # ------------------------------------------------------------------
    # outbox_events  (transactional outbox)
    #
    # The synchronous webhook handler writes webhook_events + outbox_events
    # in a single DB transaction (FR-WEBHOOK-007).  The Celery beat task
    # polls for rows WHERE enqueued = FALSE, publishes a Celery task, and
    # marks enqueued = TRUE.
    #
    # Delivery semantics: at-least-once + idempotent (not exactly-once).
    #
    #   Case A: commit succeeds, beat hasn't run yet
    #           → row stays enqueued=FALSE; picked up within 30 s.
    #
    #   Case B: beat publishes task, crashes before marking enqueued=TRUE
    #           → duplicate Celery delivery; the async processor's own
    #             idempotency guard (processing_status = PROCESSED check)
    #             absorbs the duplicate.
    #
    #   Case C: Redis/Celery unavailable
    #           → row stays enqueued=FALSE; beat retries until reachable.
    #
    # Locking strategy for the beat poller:
    #   SELECT FOR UPDATE SKIP LOCKED — appropriate for queue consumption
    #   semantics (multiple beat instances should not claim the same row).
    #   Do NOT use FOR UPDATE without SKIP LOCKED here.
    #
    # UNIQUE(webhook_event_id) — one outbox record per webhook event;
    # prevents double-publishing on webhook re-delivery.
    # ------------------------------------------------------------------
    op.create_table(
        "outbox_events",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "webhook_event_id",
            sa.UUID(),
            sa.ForeignKey("webhook_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "merchant_id",
            sa.UUID(),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # FALSE until the beat task successfully publishes to Celery.
        sa.Column(
            "enqueued",
            sa.Boolean(),
            nullable=False,
            server_default="FALSE",
        ),
        # Timestamp at which enqueued was set to TRUE.
        sa.Column(
            "enqueued_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        # Celery task ID returned by apply_async(); stored for observability.
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        # One outbox record per webhook event — prevents double-publish.
        sa.UniqueConstraint(
            "webhook_event_id",
            name="uq_outbox_webhook",
        ),
    )

    # idx_outbox_unenqueued (partial)
    #   The beat poller queries: SELECT … WHERE enqueued = FALSE ORDER BY …
    #   This partial index contains ONLY un-enqueued rows, making the scan
    #   O(pending) rather than O(total).  Once enqueued = TRUE the row is
    #   automatically excluded from the index and no longer scanned.
    op.create_index(
        "idx_outbox_unenqueued",
        "outbox_events",
        ["enqueued", "created_at"],
        postgresql_where=sa.text("enqueued = FALSE"),
    )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    # Drop in reverse dependency order so FK constraints are satisfied.

    # outbox_events and its index
    op.drop_index("idx_outbox_unenqueued", table_name="outbox_events")
    op.drop_table("outbox_events")

    # webhook_raw_payloads and its index
    op.drop_index("idx_raw_payload_expires", table_name="webhook_raw_payloads")
    op.drop_table("webhook_raw_payloads")

    # webhook_events and its indexes (drop indexes before table)
    op.drop_index("idx_webhook_entity", table_name="webhook_events")
    op.drop_index("idx_webhook_pending", table_name="webhook_events")
    op.drop_table("webhook_events")
