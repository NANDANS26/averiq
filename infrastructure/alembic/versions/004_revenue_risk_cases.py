"""004 - revenue risk cases

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2024-01-01 00:00:00.000000

Creates the `revenue_risk_cases` table — the central entity of the Averiq
decision engine.  Each row represents one lifecycle of attempting to recover
a failed payment.

Key design decisions
--------------------
* **14-state machine** encoded as a VARCHAR(20) CHECK constraint (not a
  PostgreSQL ENUM) so that states can be added in a future migration without
  requiring a full table rewrite.
* **Financial amounts as BIGINT** — `payment_amount_at_risk` and
  `projected_subscription_revenue_at_risk` are stored as integer minor units
  (paise) to avoid floating-point rounding errors (DATA-001).
* **`decision_timestamp`** records the instant the AI feature vector was
  built, enabling reproducible `time_since_failure_seconds` computation
  (`decision_timestamp - payment.failed_at`) instead of relying on
  runtime `NOW()`.  This prevents train/serve skew.
* **`previous_case_id`** self-referential FK allows re-failure chains to be
  traversed without joins to external tables.
* **Partial UNIQUE index** `idx_cases_active_payment` enforces that only one
  *active* case exists per (merchant, payment) pair while permitting new
  cases to be opened after a terminal state is reached.

Requirements: FR-STATE-001, DATA-001, DATA-004
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
# Revision identifiers (used by Alembic)
# ---------------------------------------------------------------------------

revision: str = "d4e5f6a1b2c3"
down_revision: str = "c3d4e5f6a1b2"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Terminal states that are excluded from the active-case uniqueness constraint.
# When a case reaches one of these states, a fresh case may be opened for the
# same (merchant_id, payment_id) pair (re-failure scenario).
_TERMINAL_STATES = "('RECOVERED','STOPPED','ESCALATED','FAILED','UNKNOWN_TERMINAL')"


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    op.create_table(
        "revenue_risk_cases",
        # ------------------------------------------------------------------
        # Primary key
        # ------------------------------------------------------------------
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # ------------------------------------------------------------------
        # Foreign keys
        # ------------------------------------------------------------------
        # Merchant FK — CASCADE so orphaned case rows are removed with the merchant
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Payment FK — RESTRICT so a payment with an active case cannot be deleted
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # ------------------------------------------------------------------
        # State machine — FR-STATE-001
        # 14 states: 9 non-terminal + 5 terminal
        # ------------------------------------------------------------------
        sa.Column(
            "state",
            sa.VARCHAR(20),
            nullable=False,
            server_default=sa.text("'OBSERVED'"),
        ),
        sa.CheckConstraint(
            "state IN ("
            "'OBSERVED','AT_RISK','DIAGNOSED','RECOVERY_ELIGIBLE',"
            "'ACTION_PENDING','ACTION_APPROVED','ACTION_EXECUTING',"
            "'VERIFYING','RETRYABLE',"
            "'RECOVERED','STOPPED','ESCALATED','FAILED','UNKNOWN_TERMINAL'"
            ")",
            name="chk_cases_state_valid",
        ),
        # ------------------------------------------------------------------
        # Financial exposure — DATA-001 (integer minor units, never float)
        # ------------------------------------------------------------------
        sa.Column(
            "payment_amount_at_risk",
            sa.BIGINT(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "payment_amount_at_risk > 0",
            name="chk_cases_payment_amount_positive",
        ),
        # ISO 4217 three-letter currency code
        sa.Column(
            "currency",
            sa.CHAR(3),
            nullable=False,
            server_default=sa.text("'INR'"),
        ),
        # NULL for non-subscription cases
        sa.Column(
            "projected_subscription_revenue_at_risk",
            sa.BIGINT(),
            nullable=True,
        ),
        # ------------------------------------------------------------------
        # Risk classification
        # ------------------------------------------------------------------
        sa.Column(
            "risk_calculation_path",
            sa.VARCHAR(25),
            nullable=False,
            server_default=sa.text("'SINGLE_PAYMENT'"),
        ),
        sa.CheckConstraint(
            "risk_calculation_path IN ('SINGLE_PAYMENT', 'SUBSCRIPTION_PROJECTED')",
            name="chk_cases_risk_calculation_path_valid",
        ),
        sa.Column(
            "exposure_tier",
            sa.VARCHAR(6),
            nullable=True,
        ),
        sa.CheckConstraint(
            "exposure_tier IS NULL OR exposure_tier IN ('HIGH', 'MEDIUM', 'LOW')",
            name="chk_cases_exposure_tier_valid",
        ),
        sa.Column(
            "recovery_risk_tier",
            sa.VARCHAR(6),
            nullable=True,
        ),
        sa.CheckConstraint(
            "recovery_risk_tier IS NULL OR recovery_risk_tier IN ('HIGH', 'MEDIUM', 'LOW')",
            name="chk_cases_recovery_risk_tier_valid",
        ),
        # ------------------------------------------------------------------
        # Diagnosis output (populated after AI diagnosis step)
        # ------------------------------------------------------------------
        sa.Column(
            "failure_category",
            sa.VARCHAR(25),
            nullable=True,
        ),
        sa.CheckConstraint(
            "failure_category IS NULL OR failure_category IN ("
            "'INSUFFICIENT_FUNDS','CARD_DECLINED','EXPIRED_CARD',"
            "'AUTHENTICATION_FAILURE','TECHNICAL_FAILURE','NETWORK_TIMEOUT',"
            "'FRAUD_SUSPECTED','LIMIT_EXCEEDED','UNKNOWN'"
            ")",
            name="chk_cases_failure_category_valid",
        ),
        # confidence_score: 0.0–1.0 (float is acceptable here — not a financial amount)
        sa.Column(
            "confidence_score",
            sa.FLOAT(),
            nullable=True,
        ),
        # Which AI provider produced the diagnosis
        sa.Column(
            "ai_source",
            sa.VARCHAR(50),
            nullable=True,
        ),
        # ------------------------------------------------------------------
        # decision_timestamp — DATA-004
        # Records WHEN the feature vector was extracted for inference.
        # Enables reproducible: time_since_failure_seconds =
        #     EXTRACT(EPOCH FROM (decision_timestamp - payment.failed_at))
        # Using a stored timestamp instead of runtime NOW() prevents
        # train/serve skew in time-dependent features.
        # ------------------------------------------------------------------
        sa.Column(
            "decision_timestamp",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        # ------------------------------------------------------------------
        # Re-failure linkage
        # Self-referential FK — SET NULL if the referenced (previous) case
        # is somehow deleted, preserving the current row.
        # ------------------------------------------------------------------
        sa.Column(
            "previous_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("revenue_risk_cases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # ------------------------------------------------------------------
        # Demo / fixture flag
        # ------------------------------------------------------------------
        sa.Column(
            "is_demo_fixture",
            sa.BOOLEAN(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        # ------------------------------------------------------------------
        # Case metadata (calculation assumptions, policy snapshot version, …)
        # ------------------------------------------------------------------
        sa.Column(
            "case_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # ------------------------------------------------------------------
        # Audit timestamps — DATA-004 (always TIMESTAMP WITH TIME ZONE / UTC)
        # ------------------------------------------------------------------
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
    )

    # ------------------------------------------------------------------
    # Standard indexes
    # ------------------------------------------------------------------

    # Merchant-scoped lookup (most queries are tenant-scoped)
    op.create_index(
        "idx_cases_merchant",
        "revenue_risk_cases",
        ["merchant_id"],
    )

    # Merchant + state composite — supports state-filtered queue queries
    op.create_index(
        "idx_cases_state",
        "revenue_risk_cases",
        ["merchant_id", "state"],
    )

    # Merchant + created_at DESC — supports dashboard timeline queries
    op.create_index(
        "idx_cases_created",
        "revenue_risk_cases",
        ["merchant_id", sa.text("created_at DESC")],
    )

    # Payment-scoped lookup — used when a new webhook arrives for a payment
    op.create_index(
        "idx_cases_payment",
        "revenue_risk_cases",
        ["payment_id"],
    )

    # ------------------------------------------------------------------
    # Partial UNIQUE index — only ONE active case per (merchant, payment)
    #
    # Terminal states are excluded so that re-failures can open a new case
    # for the same (merchant_id, payment_id) pair after the prior case
    # reaches a terminal state.
    # ------------------------------------------------------------------
    op.create_index(
        "idx_cases_active_payment",
        "revenue_risk_cases",
        ["merchant_id", "payment_id"],
        unique=True,
        postgresql_where=sa.text(
            f"state NOT IN {_TERMINAL_STATES}"
        ),
    )


# ---------------------------------------------------------------------------
# Downgrade — drop all indexes then the table
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # Drop indexes in reverse creation order
    op.drop_index("idx_cases_active_payment", table_name="revenue_risk_cases")
    op.drop_index("idx_cases_payment", table_name="revenue_risk_cases")
    op.drop_index("idx_cases_created", table_name="revenue_risk_cases")
    op.drop_index("idx_cases_state", table_name="revenue_risk_cases")
    op.drop_index("idx_cases_merchant", table_name="revenue_risk_cases")

    # Drop the table (all indexes and constraints are dropped automatically)
    op.drop_table("revenue_risk_cases")
