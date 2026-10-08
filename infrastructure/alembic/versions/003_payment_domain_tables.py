"""003 - payment domain tables

Revision ID: c3d4e5f6a1b2
Revises: 002
Create Date: 2024-01-01 00:00:00.000000

Creates the three core payment domain tables:

- customers   — payment gateway customer records (UNIQUE per merchant + gateway ID)
- subscriptions — recurring billing agreements (UNIQUE per merchant + gateway ID)
- payments    — individual payment transaction records (UNIQUE per merchant + gateway ID)

Financial amounts are stored as BIGINT (integer paise / minor currency units) with an
explicit CHAR(3) currency code on every financial record. All timestamps are
TIMESTAMP WITH TIME ZONE (UTC). No float columns for financial data.

Requirements: DATA-001, DATA-004, DATA-005
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
# Revision identifiers (used by Alembic)
# ---------------------------------------------------------------------------

revision: str = "c3d4e5f6a1b2"
down_revision: str = "002"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # ------------------------------------------------------------------
    # customers
    # ------------------------------------------------------------------
    op.create_table(
        "customers",
        # Primary key
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # Merchant FK — CASCADE so orphaned customer rows are cleaned up with the merchant
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Razorpay customer ID (e.g., cust_XXXX)
        sa.Column("gateway_customer_id", sa.VARCHAR(255), nullable=False),
        # Optional profile fields
        sa.Column("name", sa.VARCHAR(255), nullable=True),
        sa.Column("email", sa.VARCHAR(255), nullable=True),
        sa.Column("phone", sa.VARCHAR(50), nullable=True),
        # Extensible metadata — default to empty JSON object
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Timestamps — TIMESTAMP WITH TIME ZONE, always UTC (DATA-004)
        sa.Column(
            "created_at",
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
        # Unique constraint: a Razorpay customer can only appear once per merchant
        sa.UniqueConstraint(
            "merchant_id",
            "gateway_customer_id",
            name="uq_customers_merchant_gateway",
        ),
    )

    # Index for merchant-scoped lookups
    op.create_index(
        "idx_customers_merchant",
        "customers",
        ["merchant_id"],
    )

    # ------------------------------------------------------------------
    # subscriptions
    # ------------------------------------------------------------------
    op.create_table(
        "subscriptions",
        # Primary key
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # Merchant FK
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Optional customer FK — SET NULL so the subscription row survives if customer is removed
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Razorpay subscription ID (e.g., sub_XXXX)
        sa.Column("gateway_subscription_id", sa.VARCHAR(255), nullable=False),
        # Razorpay plan identifier
        sa.Column("plan_id", sa.VARCHAR(255), nullable=True),
        # Lifecycle state mirroring Razorpay subscription status
        sa.Column(
            "status",
            sa.VARCHAR(50),
            nullable=False,
            server_default=sa.text("'created'"),
        ),
        # Seat / quantity multiplier (must be at least 1)
        sa.Column(
            "quantity",
            sa.INTEGER(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        # Billing amount per cycle in paise (integer, never float) — DATA-001, DATA-003
        sa.Column("billing_amount", sa.BIGINT(), nullable=False),
        # ISO 4217 three-letter currency code — DATA-002, DATA-005
        sa.Column(
            "currency",
            sa.CHAR(3),
            nullable=False,
            server_default=sa.text("'INR'"),
        ),
        # Billing window timestamps — TIMESTAMP WITH TIME ZONE (DATA-004)
        sa.Column("current_start", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("current_end", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("charge_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Extensible metadata
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Audit timestamps — TIMESTAMP WITH TIME ZONE (DATA-004)
        sa.Column(
            "created_at",
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
        # Unique constraint: a Razorpay subscription can only appear once per merchant
        sa.UniqueConstraint(
            "merchant_id",
            "gateway_subscription_id",
            name="uq_subscriptions_merchant_gateway",
        ),
        # CHECK: quantity must be positive
        sa.CheckConstraint("quantity > 0", name="chk_subscriptions_quantity_positive"),
        # CHECK: billing amount must be positive (financial safety — DATA-001)
        sa.CheckConstraint(
            "billing_amount > 0", name="chk_subscriptions_billing_amount_positive"
        ),
    )

    # Indexes for merchant-scoped and customer-scoped lookups
    op.create_index(
        "idx_subscriptions_merchant",
        "subscriptions",
        ["merchant_id"],
    )
    # Partial index — only rows that actually have a customer_id are indexed,
    # avoiding NULL entries in the index.
    op.create_index(
        "idx_subscriptions_customer",
        "subscriptions",
        ["customer_id"],
        postgresql_where=sa.text("customer_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # payments
    # ------------------------------------------------------------------
    op.create_table(
        "payments",
        # Primary key
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # Merchant FK
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Optional customer FK
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Optional subscription FK
        sa.Column(
            "subscription_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subscriptions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Razorpay payment ID (e.g., pay_XXXX)
        sa.Column("gateway_payment_id", sa.VARCHAR(255), nullable=False),
        # Amount always stored as integer paise (BIGINT) — DATA-001, DATA-003
        sa.Column("amount", sa.BIGINT(), nullable=False),
        # ISO 4217 three-letter currency code — DATA-002, DATA-005
        sa.Column(
            "currency",
            sa.CHAR(3),
            nullable=False,
            server_default=sa.text("'INR'"),
        ),
        # Mirrors Razorpay payment status: created | authorized | captured | refunded | failed
        sa.Column(
            "status",
            sa.VARCHAR(50),
            nullable=False,
            server_default=sa.text("'created'"),
        ),
        # Payment method: card, netbanking, upi, wallet, etc.
        sa.Column("method", sa.VARCHAR(50), nullable=True),
        # Razorpay error fields — populated on failure
        sa.Column("error_code", sa.VARCHAR(100), nullable=True),
        sa.Column("error_description", sa.TEXT(), nullable=True),
        # Identifies the error origin: bank, gateway, customer, etc.
        sa.Column("error_source", sa.VARCHAR(50), nullable=True),
        sa.Column("error_step", sa.VARCHAR(50), nullable=True),
        sa.Column("error_reason", sa.VARCHAR(100), nullable=True),
        # Event timestamps from Razorpay webhook payloads — TIMESTAMP WITH TIME ZONE (DATA-004)
        sa.Column("failed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Extensible metadata
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Audit timestamps — TIMESTAMP WITH TIME ZONE (DATA-004)
        sa.Column(
            "created_at",
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
        # Unique constraint: a Razorpay payment can only appear once per merchant
        sa.UniqueConstraint(
            "merchant_id",
            "gateway_payment_id",
            name="uq_payments_merchant_gateway",
        ),
        # CHECK: amount must be positive (financial safety — DATA-001)
        sa.CheckConstraint("amount > 0", name="chk_payments_amount_positive"),
    )

    # Indexes for common query patterns
    op.create_index(
        "idx_payments_merchant",
        "payments",
        ["merchant_id"],
    )
    # Partial indexes — skip NULL FK values to keep them lean
    op.create_index(
        "idx_payments_customer",
        "payments",
        ["customer_id"],
        postgresql_where=sa.text("customer_id IS NOT NULL"),
    )
    op.create_index(
        "idx_payments_subscription",
        "payments",
        ["subscription_id"],
        postgresql_where=sa.text("subscription_id IS NOT NULL"),
    )
    # Composite index for merchant-scoped status filtering (queue, dashboard)
    op.create_index(
        "idx_payments_status",
        "payments",
        ["merchant_id", "status"],
    )
    # Partial index for failure timeline queries — only rows that have actually failed
    op.create_index(
        "idx_payments_failed_at",
        "payments",
        ["merchant_id", "failed_at"],
        postgresql_where=sa.text("failed_at IS NOT NULL"),
    )


# ---------------------------------------------------------------------------
# Downgrade — drop tables in reverse dependency order
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # Drop payments first (depends on subscriptions and customers)
    op.drop_index("idx_payments_failed_at", table_name="payments")
    op.drop_index("idx_payments_status", table_name="payments")
    op.drop_index("idx_payments_subscription", table_name="payments")
    op.drop_index("idx_payments_customer", table_name="payments")
    op.drop_index("idx_payments_merchant", table_name="payments")
    op.drop_table("payments")

    # Drop subscriptions next (depends on customers)
    op.drop_index("idx_subscriptions_customer", table_name="subscriptions")
    op.drop_index("idx_subscriptions_merchant", table_name="subscriptions")
    op.drop_table("subscriptions")

    # Drop customers last (no dependencies within this migration)
    op.drop_index("idx_customers_merchant", table_name="customers")
    op.drop_table("customers")
