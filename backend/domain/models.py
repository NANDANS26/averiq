"""SQLAlchemy 2.x async ORM models for all Averiq database tables.

Every model follows these invariants:
  - Inherits from ``Base`` (defined in ``backend.database``)
  - Uses ``Mapped[T]`` type annotations with ``mapped_column()`` (SQLAlchemy 2.x style)
  - All TIMESTAMP WITH TIME ZONE columns → ``Mapped[datetime]`` / ``Mapped[datetime | None]``
    with ``DateTime(timezone=True)``; naive datetimes are forbidden (DATA-004)
  - All financial amount columns → ``Mapped[int]`` backed by ``BigInteger``; never ``float``
    (DATA-001, DATA-003)
  - ``float`` is used ONLY for non-financial probability / score columns:
    confidence_score, recovery_probability, diagnosis_confidence,
    brier_score, auc_roc, min_confidence
  - All UUID columns → ``Mapped[uuid.UUID]`` with ``PGUUID(as_uuid=True)``
  - All JSONB columns → ``Mapped[dict[str, Any]]``
  - Bidirectional relationships are declared ``lazy="raise_on_sql"`` to surface
    accidental N+1 queries at development time (callers must use ``selectinload``
    or ``joinedload`` explicitly)

Tables covered (migrations 001–006)
------------------------------------
Migration 001 — core auth tables
  1.  Merchant
  2.  User
  3.  TokenRevocation

Migration 002 — webhook + outbox tables
  4.  WebhookEvent
  5.  WebhookRawPayload
  6.  OutboxEvent

Migration 003 — payment domain tables
  7.  Customer
  8.  Subscription
  9.  Payment

Migration 004 — revenue risk cases
  10. RevenueRiskCase

Migration 005 — policy, interventions, human reviews
  11. Policy
  12. Intervention
  13. HumanReview

Migration 006 — execution, verification, audit, model artifacts, payment link usage
  14. ExternalOperation
  15. VerificationAttempt
  16. AuditEvent
  17. ModelArtifact
  18. PaymentLinkUsage

Requirements: DATA-001, DATA-004
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

# ---------------------------------------------------------------------------
# Migration 001 — Core auth tables: merchants, users, token_revocations
# ---------------------------------------------------------------------------


class Merchant(Base):
    """A business entity that uses Averiq to recover failed payments.

    ``webhook_secret`` is stored as an application-level encrypted ciphertext
    (not a bcrypt hash) so it can be retrieved and used for HMAC-SHA256
    signature verification. It must never appear in API responses or logs.

    ``operating_mode`` mirrors the OPERATING_MODE setting at merchant
    registration time: "SIMULATED" or "REAL_TEST_MODE".
    """

    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Primary key — gen_random_uuid() in DB",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Encrypted-at-rest ciphertext — decrypt with APP_SECRET_KEY for HMAC verification
    webhook_secret: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Application-level encrypted ciphertext; never stored plaintext",
    )
    # ISO 4217 three-letter code; default INR for MVP
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="INR"
    )
    operating_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="SIMULATED"
    )
    # DATA-004 — timezone-aware UTC timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    users: Mapped[list[User]] = relationship(
        "User",
        back_populates="merchant",
        lazy="raise_on_sql",
        cascade="all, delete-orphan",
    )
    webhook_events: Mapped[list[WebhookEvent]] = relationship(
        "WebhookEvent",
        back_populates="merchant",
        lazy="raise_on_sql",
        cascade="all, delete-orphan",
    )
    revenue_risk_cases: Mapped[list[RevenueRiskCase]] = relationship(
        "RevenueRiskCase",
        back_populates="merchant",
        lazy="raise_on_sql",
        cascade="all, delete-orphan",
    )
    policies: Mapped[list[Policy]] = relationship(
        "Policy",
        back_populates="merchant",
        lazy="raise_on_sql",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Merchant id={self.id!s} name={self.name!r} mode={self.operating_mode!r}>"


class User(Base):
    """A human operator, admin, or viewer account belonging to a Merchant.

    The SYSTEM pseudo-role is used only by internal service-to-service calls
    and must never be assigned to a human user via any API endpoint.

    Passwords are stored as bcrypt or Argon2 hashes; never plaintext.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('ADMIN', 'OPERATOR', 'VIEWER', 'SYSTEM')",
            name="ck_users_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # bcrypt/Argon2 hash — never plaintext (SEC-AUTH-012)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # DATA-004
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    merchant: Mapped[Merchant] = relationship(
        "Merchant", back_populates="users", lazy="raise_on_sql"
    )
    token_revocations: Mapped[list[TokenRevocation]] = relationship(
        "TokenRevocation",
        back_populates="user",
        lazy="raise_on_sql",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id!s} email={self.email!r} role={self.role!r}>"


class TokenRevocation(Base):
    """Revoked JWT tokens tracked by their ``jti`` (JWT ID) claim.

    Checked on every authenticated request so that tokens invalidated by
    logout or role changes cannot be reused before their natural expiry.
    Background cleanup removes rows whose ``expires_at`` is in the past.
    """

    __tablename__ = "token_revocations"

    # jti UUID from the JWT payload — the primary lookup key
    jti: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, comment="JWT ID claim from the token"
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # DATA-004
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Used by background cleanup to purge expired revocations
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    user: Mapped[User] = relationship(
        "User", back_populates="token_revocations", lazy="raise_on_sql"
    )

    def __repr__(self) -> str:
        return (
            f"<TokenRevocation jti={self.jti!s} user_id={self.user_id!s}"
            f" expires_at={self.expires_at!r}>"
        )


# ---------------------------------------------------------------------------
# Migration 002 — Webhook + outbox tables
# ---------------------------------------------------------------------------


class WebhookEvent(Base):
    """A normalised inbound event received from Razorpay.

    ``provider_event_id`` is the value of the ``x-razorpay-event-id`` header
    and acts as the primary deduplication key (UNIQUE per merchant).
    When the header is absent, the application writes a deterministic fallback
    key (entity_id + event_type + time bucket) and logs a warning metric.

    ``processing_status`` tracks the event through the async pipeline:
      RECEIVED → PERSISTED → QUEUED → PROCESSING → PROCESSED
      or PROCESSING → FAILED_RETRYABLE → … → DEAD_LETTERED
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "provider_event_id", name="uq_webhook_merchant_event"
        ),
        CheckConstraint(
            "processing_status IN ("
            "'RECEIVED','PERSISTED','QUEUED','PROCESSING',"
            "'PROCESSED','FAILED_RETRYABLE','DEAD_LETTERED')",
            name="ck_webhook_events_processing_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Primary dedup key — x-razorpay-event-id header value
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="e.g. payment.failed, subscription.charged"
    )
    processing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="RECEIVED"
    )
    # Razorpay entity ID (payment ID, subscription ID, etc.)
    entity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="payment | subscription | order"
    )
    # Timestamp extracted from the Razorpay payload (DATA-004)
    event_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Server receipt time (DATA-004)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    merchant: Mapped[Merchant] = relationship(
        "Merchant", back_populates="webhook_events", lazy="raise_on_sql"
    )
    raw_payload: Mapped[WebhookRawPayload | None] = relationship(
        "WebhookRawPayload",
        back_populates="webhook_event",
        lazy="raise_on_sql",
        uselist=False,
        cascade="all, delete-orphan",
    )
    outbox_event: Mapped[OutboxEvent | None] = relationship(
        "OutboxEvent",
        back_populates="webhook_event",
        lazy="raise_on_sql",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookEvent id={self.id!s} type={self.event_type!r}"
            f" status={self.processing_status!r}>"
        )


class WebhookRawPayload(Base):
    """Optional byte-for-byte retention of the raw webhook request body.

    Only populated when ``WEBHOOK_RETAIN_RAW_PAYLOAD=true``. The table always
    exists in the schema; whether rows are written is controlled by the
    application-layer feature flag.

    ``raw_body`` is stored as BYTEA to preserve the exact byte sequence
    required for HMAC-SHA256 re-verification.
    """

    __tablename__ = "webhook_raw_payloads"
    __table_args__ = (
        UniqueConstraint("webhook_event_id", name="uq_raw_payload_event"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    webhook_event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("webhook_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    # BYTEA — exact bytes needed for signature re-verification
    raw_body: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default="application/json"
    )
    # Retention deadline — rows beyond this date may be purged (DATA-006)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    webhook_event: Mapped[WebhookEvent] = relationship(
        "WebhookEvent", back_populates="raw_payload", lazy="raise_on_sql"
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookRawPayload id={self.id!s}"
            f" webhook_event_id={self.webhook_event_id!s}>"
        )


class OutboxEvent(Base):
    """Transactional outbox record written atomically with ``WebhookEvent``.

    The Celery beat task polls for rows where ``enqueued=False``, publishes
    the Celery task, then marks ``enqueued=True``. Delivery is at-least-once
    + idempotent, not exactly-once.

    Locking: ``SELECT FOR UPDATE SKIP LOCKED`` (queue consumption semantics,
    not case-state serialization).
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("webhook_event_id", name="uq_outbox_webhook"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    webhook_event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("webhook_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
    )
    enqueued: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enqueued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Celery task ID for observability
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    webhook_event: Mapped[WebhookEvent] = relationship(
        "WebhookEvent", back_populates="outbox_event", lazy="raise_on_sql"
    )

    def __repr__(self) -> str:
        return (
            f"<OutboxEvent id={self.id!s}"
            f" webhook_event_id={self.webhook_event_id!s}"
            f" enqueued={self.enqueued}>"
        )


# ---------------------------------------------------------------------------
# Migration 003 — Payment domain tables: customers, subscriptions, payments
# ---------------------------------------------------------------------------


class Customer(Base):
    """A payer whose payment has failed and is a candidate for recovery.

    ``gateway_customer_id`` is the Razorpay customer ID (``cust_XXXX``).
    """

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id",
            "gateway_customer_id",
            name="uq_customers_merchant_gateway",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    gateway_customer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Extensible metadata (JSONB).  Named ``extra_metadata`` in Python to avoid
    # conflicting with SQLAlchemy's reserved ``DeclarativeBase.metadata`` attribute.
    # The underlying DB column is named ``metadata`` via the ``name`` kwarg.
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, name="metadata", nullable=False, server_default="{}"
    )
    # DATA-004
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    subscriptions: Mapped[list[Subscription]] = relationship(
        "Subscription",
        back_populates="customer",
        lazy="raise_on_sql",
    )
    payments: Mapped[list[Payment]] = relationship(
        "Payment",
        back_populates="customer",
        lazy="raise_on_sql",
    )
    revenue_risk_cases: Mapped[list[RevenueRiskCase]] = relationship(
        "RevenueRiskCase",
        back_populates="customer",
        lazy="raise_on_sql",
    )

    def __repr__(self) -> str:
        return (
            f"<Customer id={self.id!s}"
            f" gateway_customer_id={self.gateway_customer_id!r}>"
        )


class Subscription(Base):
    """A recurring billing agreement between a Merchant and a Customer.

    ``billing_amount`` is stored as integer paise (BIGINT) — never float
    (DATA-001). ``currency`` is an explicit ISO 4217 three-letter code
    (DATA-002, DATA-005).
    """

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id",
            "gateway_subscription_id",
            name="uq_subscriptions_merchant_gateway",
        ),
        CheckConstraint("quantity > 0", name="chk_subscriptions_quantity_positive"),
        CheckConstraint(
            "billing_amount > 0",
            name="chk_subscriptions_billing_amount_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Razorpay subscription ID (sub_XXXX)
    gateway_subscription_id: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="created"
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # DATA-001 — integer paise, never float
    billing_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # DATA-005 — explicit currency on every financial record
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="INR"
    )
    # DATA-004 — timezone-aware timestamps
    current_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    charge_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Extensible metadata (JSONB).  Named ``extra_metadata`` in Python to avoid
    # conflicting with SQLAlchemy's reserved ``DeclarativeBase.metadata`` attribute.
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, name="metadata", nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    customer: Mapped[Customer | None] = relationship(
        "Customer", back_populates="subscriptions", lazy="raise_on_sql"
    )
    payments: Mapped[list[Payment]] = relationship(
        "Payment",
        back_populates="subscription",
        lazy="raise_on_sql",
    )
    revenue_risk_cases: Mapped[list[RevenueRiskCase]] = relationship(
        "RevenueRiskCase",
        back_populates="subscription",
        lazy="raise_on_sql",
    )

    def __repr__(self) -> str:
        return (
            f"<Subscription id={self.id!s}"
            f" gateway_subscription_id={self.gateway_subscription_id!r}"
            f" status={self.status!r}>"
        )


class Payment(Base):
    """A financial transaction record from Razorpay.

    ``amount`` is stored as integer paise (BIGINT) — never float (DATA-001).
    ``currency`` is an explicit ISO 4217 code (DATA-002, DATA-005).
    ``status`` mirrors Razorpay payment lifecycle:
      created | authorized | captured | refunded | failed

    Razorpay error fields (``error_code``, ``error_description``,
    ``error_source``, ``error_step``, ``error_reason``) are populated on
    failure and used by the deterministic fallback classifier.
    """

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id",
            "gateway_payment_id",
            name="uq_payments_merchant_gateway",
        ),
        CheckConstraint("amount > 0", name="chk_payments_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Razorpay payment ID (pay_XXXX)
    gateway_payment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # DATA-001 — integer paise (minor currency units), never float
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # DATA-005 — explicit currency on every financial record
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="INR"
    )
    # Razorpay payment status: created | authorized | captured | refunded | failed
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="created"
    )
    # Payment method: card, netbanking, upi, wallet, etc.
    method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Razorpay error fields — populated on failure; used by fallback classifier
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_step: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # DATA-004 — timezone-aware failure / capture timestamps
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Extensible metadata (JSONB).  Named ``extra_metadata`` in Python to avoid
    # conflicting with SQLAlchemy's reserved ``DeclarativeBase.metadata`` attribute.
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, name="metadata", nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    customer: Mapped[Customer | None] = relationship(
        "Customer", back_populates="payments", lazy="raise_on_sql"
    )
    subscription: Mapped[Subscription | None] = relationship(
        "Subscription", back_populates="payments", lazy="raise_on_sql"
    )
    revenue_risk_cases: Mapped[list[RevenueRiskCase]] = relationship(
        "RevenueRiskCase",
        back_populates="payment",
        lazy="raise_on_sql",
    )

    def __repr__(self) -> str:
        return (
            f"<Payment id={self.id!s}"
            f" gateway_payment_id={self.gateway_payment_id!r}"
            f" amount={self.amount} {self.currency}"
            f" status={self.status!r}>"
        )


# ---------------------------------------------------------------------------
# Migration 004 — Revenue risk cases
# ---------------------------------------------------------------------------


class RevenueRiskCase(Base):
    """The central domain entity — one lifecycle of recovering a failed payment.

    State machine (13 states, 4 terminal):
      OBSERVED → AT_RISK → DIAGNOSED → RECOVERY_ELIGIBLE → ACTION_PENDING
      → ACTION_APPROVED → ACTION_EXECUTING → VERIFYING
      → RECOVERED (terminal) | RETRYABLE → RECOVERY_ELIGIBLE
      → STOPPED (terminal) | ESCALATED (terminal) | FAILED (terminal)

    Financial columns (DATA-001, DATA-003):
      ``payment_amount_at_risk``               — integer paise; never float
      ``projected_subscription_revenue_at_risk`` — integer paise; nullable
      ``recovered_amount``                     — integer paise; nullable

    Non-financial float columns (acceptable use of float):
      ``recovery_probability``   — ML model score 0.0–1.0
      ``diagnosis_confidence``   — AI diagnosis score 0.0–1.0

    ``decision_timestamp`` (DATA-004) records exactly WHEN the feature vector
    was built for inference, enabling reproducible
      ``time_since_failure_seconds = decision_timestamp - payment.failed_at``
    instead of relying on ``NOW()`` at serve time (prevents train/serve skew).

    ``previous_case_id`` links re-failure cases so the full chain can be
    traversed without joins to external tables.
    """

    __tablename__ = "revenue_risk_cases"
    __table_args__ = (
        CheckConstraint(
            "state IN ("
            "'OBSERVED','AT_RISK','DIAGNOSED','RECOVERY_ELIGIBLE',"
            "'ACTION_PENDING','ACTION_APPROVED','ACTION_EXECUTING',"
            "'VERIFYING','RETRYABLE',"
            "'RECOVERED','STOPPED','ESCALATED','FAILED','UNKNOWN_TERMINAL'"
            ")",
            name="chk_cases_state_valid",
        ),
        CheckConstraint(
            "payment_amount_at_risk > 0",
            name="chk_cases_payment_amount_positive",
        ),
        CheckConstraint(
            "risk_calculation_path IN ('SINGLE_PAYMENT', 'SUBSCRIPTION_PROJECTED')",
            name="chk_cases_risk_calculation_path_valid",
        ),
        CheckConstraint(
            "exposure_tier IS NULL OR exposure_tier IN ('HIGH', 'MEDIUM', 'LOW')",
            name="chk_cases_exposure_tier_valid",
        ),
        CheckConstraint(
            "recovery_risk_tier IS NULL OR recovery_risk_tier IN ('HIGH', 'MEDIUM', 'LOW')",
            name="chk_cases_recovery_risk_tier_valid",
        ),
        CheckConstraint(
            "failure_category IS NULL OR failure_category IN ("
            "'INSUFFICIENT_FUNDS','CARD_DECLINED','EXPIRED_CARD',"
            "'AUTHENTICATION_FAILURE','TECHNICAL_FAILURE','NETWORK_TIMEOUT',"
            "'FRAUD_SUSPECTED','LIMIT_EXCEEDED','UNKNOWN'"
            ")",
            name="chk_cases_failure_category_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Self-referential FK for re-failure chains
    previous_case_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("revenue_risk_cases.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="OBSERVED"
    )

    # ------------------------------------------------------------------
    # Financial exposure — DATA-001 (integer paise, never float)
    # ------------------------------------------------------------------
    # DATA-001, DATA-003 — integer paise
    payment_amount_at_risk: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # DATA-005 — explicit currency on every financial record
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="INR"
    )
    # Nullable — only set for subscription-linked cases
    projected_subscription_revenue_at_risk: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    # ------------------------------------------------------------------
    # Risk classification
    # ------------------------------------------------------------------
    risk_calculation_path: Mapped[str] = mapped_column(
        String(25), nullable=False, server_default="SINGLE_PAYMENT"
    )
    exposure_tier: Mapped[str | None] = mapped_column(String(6), nullable=True)
    recovery_risk_tier: Mapped[str | None] = mapped_column(String(6), nullable=True)

    # ------------------------------------------------------------------
    # Diagnosis output (operator explanation; NOT model features)
    # ------------------------------------------------------------------
    failure_category: Mapped[str | None] = mapped_column(String(25), nullable=True)
    # Float is acceptable here — confidence_score is not a financial value
    diagnosis_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_source: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="StubAIProvider | ExternalLLMProvider | DeterministicFallback",
    )

    # ------------------------------------------------------------------
    # Recoverability model output
    # Float is acceptable here — recovery_probability is not a financial value
    # ------------------------------------------------------------------
    recovery_probability: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ------------------------------------------------------------------
    # decision_timestamp — DATA-004
    # Records WHEN the feature vector was extracted for inference.
    # Enables reproducible:
    #   time_since_failure_seconds = EXTRACT(EPOCH FROM (decision_timestamp - payment.failed_at))
    # Must be set at the START of the Decision Engine run, before feature extraction,
    # and persisted on the case. Do NOT use NOW() during feature extraction — that
    # would create train/serve skew in time-dependent features.
    # ------------------------------------------------------------------
    decision_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "Instant when feature vector was built; used for time_since_failure_seconds. "
            "Do not substitute NOW() at serve time."
        ),
    )

    # ------------------------------------------------------------------
    # Recovery attribution — DATA-001 (integer paise)
    # ------------------------------------------------------------------
    attribution_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # DATA-001 — integer paise
    recovered_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    recovered_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # ------------------------------------------------------------------
    # Operating mode and demo flag
    # ------------------------------------------------------------------
    is_demo_fixture: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    operating_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="SIMULATED"
    )

    # ------------------------------------------------------------------
    # Extensible metadata (JSONB)
    # Stores: calculation assumptions, policy snapshot version, ai_timeout,
    # fallback_reason, and other case-level annotations.
    # ------------------------------------------------------------------
    case_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    # DATA-004
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    merchant: Mapped[Merchant] = relationship(
        "Merchant", back_populates="revenue_risk_cases", lazy="raise_on_sql"
    )
    payment: Mapped[Payment] = relationship(
        "Payment", back_populates="revenue_risk_cases", lazy="raise_on_sql"
    )
    customer: Mapped[Customer | None] = relationship(
        "Customer", back_populates="revenue_risk_cases", lazy="raise_on_sql"
    )
    subscription: Mapped[Subscription | None] = relationship(
        "Subscription", back_populates="revenue_risk_cases", lazy="raise_on_sql"
    )
    previous_case: Mapped[RevenueRiskCase | None] = relationship(
        "RevenueRiskCase",
        remote_side="RevenueRiskCase.id",
        foreign_keys="RevenueRiskCase.previous_case_id",
        lazy="raise_on_sql",
    )
    interventions: Mapped[list[Intervention]] = relationship(
        "Intervention",
        back_populates="case",
        lazy="raise_on_sql",
        cascade="all, delete-orphan",
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(
        "AuditEvent",
        back_populates="case",
        lazy="raise_on_sql",
    )
    human_reviews: Mapped[list[HumanReview]] = relationship(
        "HumanReview",
        back_populates="case",
        lazy="raise_on_sql",
    )
    external_operations: Mapped[list[ExternalOperation]] = relationship(
        "ExternalOperation",
        back_populates="case",
        lazy="raise_on_sql",
    )

    def __repr__(self) -> str:
        return (
            f"<RevenueRiskCase id={self.id!s}"
            f" state={self.state!r}"
            f" amount={self.payment_amount_at_risk} {self.currency}>"
        )


# ---------------------------------------------------------------------------
# Migration 005 — Policy, interventions, human reviews
# ---------------------------------------------------------------------------


class Policy(Base):
    """A versioned, deterministic ruleset governing intervention approval.

    Lifecycle: draft → validated → active → retired.
    At most one policy per merchant may be in ``active`` state (enforced by
    partial unique index ``idx_policies_active``).

    ``configuration`` (JSONB) stores all rule thresholds:
      confidence_threshold, max_amount per intervention_type, max_retries,
      cooldown_seconds, human_approval_threshold_minor_units, attribution_window,
      cost and risk_penalty per intervention_type.
    """

    __tablename__ = "policies"
    __table_args__ = (
        UniqueConstraint("merchant_id", "version", name="uq_policies_merchant_version"),
        CheckConstraint(
            "lifecycle_state IN ('draft', 'validated', 'active', 'retired')",
            name="ck_policies_lifecycle_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="draft"
    )
    # JSONB blob containing all rule thresholds (integers in minor units where applicable)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # DATA-004
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    merchant: Mapped[Merchant] = relationship(
        "Merchant", back_populates="policies", lazy="raise_on_sql"
    )
    interventions: Mapped[list[Intervention]] = relationship(
        "Intervention",
        back_populates="policy",
        lazy="raise_on_sql",
    )

    def __repr__(self) -> str:
        return (
            f"<Policy id={self.id!s}"
            f" merchant_id={self.merchant_id!s}"
            f" version={self.version}"
            f" state={self.lifecycle_state!r}>"
        )


class Intervention(Base):
    """A recovery action applied to a RevenueRiskCase.

    ``expected_recovery_value`` is in integer paise (DATA-001) — the output
    of ``compute_expected_value()`` which uses ``decimal.Decimal`` with
    ``ROUND_FLOOR`` and returns an ``int``.

    ``policy_decision`` records whether Policy_Engine said APPROVED, BLOCKED,
    or HUMAN_REVIEW. ``policy_blocking_rules`` and ``policy_evaluated_values``
    are JSONB blobs populated when BLOCKED.

    ``execution_status`` tracks where the intervention is in the Execution_Engine
    pipeline; ``human_review_status`` mirrors ``HumanReview.status`` for quick
    reads without a join.
    """

    __tablename__ = "interventions"
    __table_args__ = (
        CheckConstraint(
            "intervention_type IN ("
            "'RETRY','PAYMENT_LINK','CUSTOMER_ACTION','ESCALATE','STOP'"
            ")",
            name="ck_interventions_type",
        ),
        CheckConstraint(
            "execution_capability IN ("
            "'EXECUTABLE','RECOMMENDATION_ONLY','INTERNAL_ACTION'"
            ")",
            name="ck_interventions_exec_capability",
        ),
        CheckConstraint(
            "provider_support_status IN ('VERIFIED','UNVERIFIED','NOT_SUPPORTED')",
            name="ck_interventions_provider_support",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("revenue_risk_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("policies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Model version string — reference to model_artifacts.model_version
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    feature_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    intervention_type: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_capability: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_support_status: Mapped[str] = mapped_column(String(32), nullable=False)
    requires_human_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # DATA-001 — integer paise; output of compute_expected_value()
    expected_recovery_value: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    verification_strategy: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Policy_Engine decision output
    policy_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_blocking_rules: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    policy_evaluated_values: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    execution_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    human_review_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Human-readable operator recommendation (for RECOMMENDATION_ONLY interventions)
    recommendation_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # DATA-004
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    case: Mapped[RevenueRiskCase] = relationship(
        "RevenueRiskCase", back_populates="interventions", lazy="raise_on_sql"
    )
    policy: Mapped[Policy] = relationship(
        "Policy", back_populates="interventions", lazy="raise_on_sql"
    )
    human_review: Mapped[HumanReview | None] = relationship(
        "HumanReview",
        back_populates="intervention",
        lazy="raise_on_sql",
        uselist=False,
        cascade="all, delete-orphan",
    )
    external_operation: Mapped[ExternalOperation | None] = relationship(
        "ExternalOperation",
        back_populates="intervention",
        lazy="raise_on_sql",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Intervention id={self.id!s}"
            f" type={self.intervention_type!r}"
            f" capability={self.execution_capability!r}"
            f" decision={self.policy_decision!r}>"
        )


class HumanReview(Base):
    """Human reviewer workflow for interventions that exceed the approval threshold.

    Has its own lifecycle, independent of the terminal case state:
      OPEN → ASSIGNED → APPROVED | REJECTED | STOPPED → RESOLVED

    Reviewer actions do NOT mutate terminal case state.
    Only OPERATOR+ roles may approve or reject.
    """

    __tablename__ = "human_reviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN','ASSIGNED','APPROVED','REJECTED','STOPPED','RESOLVED')",
            name="ck_human_reviews_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    intervention_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("interventions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("revenue_risk_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="OPEN"
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # DATA-004
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    intervention: Mapped[Intervention] = relationship(
        "Intervention", back_populates="human_review", lazy="raise_on_sql"
    )
    case: Mapped[RevenueRiskCase] = relationship(
        "RevenueRiskCase", back_populates="human_reviews", lazy="raise_on_sql"
    )

    def __repr__(self) -> str:
        return (
            f"<HumanReview id={self.id!s}"
            f" intervention_id={self.intervention_id!s}"
            f" status={self.status!r}>"
        )


# ---------------------------------------------------------------------------
# Migration 006 — Execution, verification, audit, model artifacts, payment link usage
# ---------------------------------------------------------------------------


class ExternalOperation(Base):
    """Tracks a single external API call made by the Execution_Engine.

    ``action_id`` is the PRIMARY idempotency key — always set at creation,
    before any external call. It is UNIQUE across the table, enabling
    ``CanExecuteAction`` to detect previously-SUCCEEDED operations.

    A partial unique index (in the migration) on
    ``(case_id, action_type, external_operation_id)`` WHERE external_operation_id
    IS NOT NULL prevents duplicate gateway references once the external system
    returns a reference ID.

    TIMEOUT ≠ FAILED. A TIMEOUT means no definitive response was received.
    Do NOT re-execute until ``ReconciliationStrategy`` establishes a definitive
    outcome (SUCCEEDED, FAILED, or UNKNOWN).

    Status values:
      NOT_STARTED → REQUESTED → SUCCEEDED | FAILED | TIMEOUT | UNKNOWN
      TIMEOUT / UNKNOWN → reconciliation → SUCCEEDED | FAILED | UNKNOWN
      SUCCEEDED → VERIFIED (set by Verification_Engine)
    """

    __tablename__ = "external_operations"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'NOT_STARTED','REQUESTED','SUCCEEDED','FAILED',"
            "'TIMEOUT','UNKNOWN','VERIFIED'"
            ")",
            name="ck_ext_op_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    intervention_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("interventions.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("revenue_risk_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
    )
    # PRIMARY idempotency key — set before any external call; UNIQUE
    action_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        unique=True,
        comment="Primary idempotency key; always set before any external API call",
    )
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Nullable before the external system returns a reference ID
    external_operation_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Razorpay link_id or subscription_id; NULL until submission completes",
    )
    provider: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="razorpay"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="NOT_STARTED"
    )
    # DATA-004
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    timeout_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    intervention: Mapped[Intervention] = relationship(
        "Intervention", back_populates="external_operation", lazy="raise_on_sql"
    )
    case: Mapped[RevenueRiskCase] = relationship(
        "RevenueRiskCase", back_populates="external_operations", lazy="raise_on_sql"
    )
    verification_attempts: Mapped[list[VerificationAttempt]] = relationship(
        "VerificationAttempt",
        back_populates="external_operation",
        lazy="raise_on_sql",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<ExternalOperation id={self.id!s}"
            f" action_id={self.action_id!s}"
            f" action_type={self.action_type!r}"
            f" status={self.status!r}>"
        )


class VerificationAttempt(Base):
    """One attempt by the Verification_Engine to confirm the payment outcome.

    Per AUD-006, raw gateway response payloads are NOT stored here.
    Only normalised status fields are recorded.

    Maximum 3 attempts per ``ExternalOperation`` before escalation (FR-VERIFY-007).
    ``(external_operation_id, attempt_number)`` is UNIQUE.
    """

    __tablename__ = "verification_attempts"
    __table_args__ = (
        UniqueConstraint(
            "external_operation_id",
            "attempt_number",
            name="uq_verification_attempt_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    external_operation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("external_operations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # DATA-004 — timezone-aware UTC
    verification_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Normalised status from the gateway (NOT the raw response — AUD-006)
    gateway_reported_status: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # VERIFIED | FAILED | UNKNOWN
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    external_operation: Mapped[ExternalOperation] = relationship(
        "ExternalOperation",
        back_populates="verification_attempts",
        lazy="raise_on_sql",
    )

    def __repr__(self) -> str:
        return (
            f"<VerificationAttempt id={self.id!s}"
            f" attempt_number={self.attempt_number}"
            f" outcome={self.outcome!r}>"
        )


class AuditEvent(Base):
    """Append-only record of every significant state transition or decision.

    Per AUD-002, UPDATE and DELETE on this table are forbidden at the
    application and database access layer. The ``AuditEventRepository``
    exposes only ``append(event)``; the PostgreSQL role grants
    INSERT + SELECT only — no UPDATE or DELETE.

    IMPORTANT: There is intentionally NO ``updated_at`` column on this table.
    Adding one would suggest that records are mutable; they are not.

    Raw gateway response payloads are NOT stored in ``payload`` (AUD-006).
    The payload contains: operation_id, external_reference, normalised_status,
    policy_version, model_version, action, result, reason.

    ``correlation_id`` links all entries in one end-to-end processing chain.
    """

    __tablename__ = "audit_events"
    # NO updated_at column — append-only by design (AUD-002)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("revenue_risk_cases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # "SYSTEM" for internal operations; user_id as string for human actions
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action_type: Mapped[str] = mapped_column(String(128), nullable=False)
    affected_entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    affected_entity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    from_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Versioning — recorded for every Policy_Engine decision (AUD-005)
    policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feature_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # JSONB payload — NO raw gateway payloads or PII (AUD-006, NFR-OBS-007)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    # Links all log entries for one end-to-end processing chain (NFR-OBS-002)
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    # DATA-004 — timezone-aware UTC; the only timestamp column (no updated_at)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    case: Mapped[RevenueRiskCase | None] = relationship(
        "RevenueRiskCase", back_populates="audit_events", lazy="raise_on_sql"
    )

    def __repr__(self) -> str:
        return (
            f"<AuditEvent id={self.id!s}"
            f" action_type={self.action_type!r}"
            f" actor={self.actor!r}"
            f" created_at={self.created_at!r}>"
        )


class ModelArtifact(Base):
    """A trained ML model artifact with full provenance metadata.

    Lifecycle: trained → evaluated → candidate → active → retired.
    A model may NOT transition to ``active`` without passing the evaluation
    step with Brier score below ``BRIER_SCORE_WARNING_THRESHOLD`` (AI-MODEL-001).

    At most ONE model per ``model_type`` may be in ``active`` state, enforced
    by a partial unique index ``idx_model_active``.

    ``artifact_sha256`` is verified on every load to detect tampering or
    storage corruption before the model is used for inference.

    Float columns here (``brier_score``, ``auc_roc``) are calibration /
    evaluation metrics, NOT financial values — float is appropriate.
    """

    __tablename__ = "model_artifacts"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_state IN ('trained','evaluated','candidate','active','retired')",
            name="ck_model_lifecycle",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    model_version: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    model_type: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="lightgbm | logistic_regression"
    )
    lifecycle_state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="trained"
    )
    dataset_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    # SHA-256 hex digest of the serialised model file — verified before load
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Must match serve-time feature extraction version (prevents skew)
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False)
    # DATA-004
    training_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    random_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    hyperparameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Populated after make evaluate; stores all metric values (computed at runtime)
    evaluation_metrics: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    # Quality gate results — populated by make evaluate (float; nullable until evaluated)
    # Lower is better; threshold configurable via BRIER_SCORE_WARNING_THRESHOLD
    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    auc_roc: Mapped[float | None] = mapped_column(Float, nullable=True)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<ModelArtifact id={self.id!s}"
            f" model_version={self.model_version!r}"
            f" type={self.model_type!r}"
            f" state={self.lifecycle_state!r}>"
        )


class PaymentLinkUsage(Base):
    """Tracks Razorpay Payment Link creation count to enforce test-mode quotas.

    Razorpay test mode is limited to 30 Payment Links per business (INT-RAZORPAY-011).
    When the count approaches this limit, ``InterventionEngine`` substitutes a
    RECOMMENDATION_ONLY intervention instead of PAYMENT_LINK (FR-INTVN-007).

    ``(merchant_id, operating_mode, period_start)`` is UNIQUE so that counts
    are tracked per calendar period and per operating mode separately.

    ``count`` is stored as an integer counter (not a financial amount);
    ``Integer`` (not BigInteger) is appropriate here.
    """

    __tablename__ = "payment_link_usage"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id",
            "operating_mode",
            "period_start",
            name="uq_payment_link_usage",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Counter — not a financial amount; Integer is appropriate
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    operating_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    # Calendar period for grouping (e.g. first day of the billing month)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    # DATA-004
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<PaymentLinkUsage merchant_id={self.merchant_id!s}"
            f" mode={self.operating_mode!r}"
            f" period={self.period_start!r}"
            f" count={self.count}>"
        )


# ---------------------------------------------------------------------------
# Public API — explicit __all__ lists every model class
# ---------------------------------------------------------------------------

__all__ = [
    # Migration 001
    "Merchant",
    "User",
    "TokenRevocation",
    # Migration 002
    "WebhookEvent",
    "WebhookRawPayload",
    "OutboxEvent",
    # Migration 003
    "Customer",
    "Subscription",
    "Payment",
    # Migration 004
    "RevenueRiskCase",
    # Migration 005
    "Policy",
    "Intervention",
    "HumanReview",
    # Migration 006
    "ExternalOperation",
    "VerificationAttempt",
    "AuditEvent",
    "ModelArtifact",
    "PaymentLinkUsage",
]
