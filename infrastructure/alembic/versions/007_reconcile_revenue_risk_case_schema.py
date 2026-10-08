"""reconcile revenue risk case schema with ORM

Revision ID: a7b8c9d0e1f2
Revises: f6a1b2c3d4e5
Create Date: 2026-09-03 00:00:00.000000

Reconciles the live revenue_risk_cases schema with the current
RevenueRiskCase ORM model.

Changes:
- Rename confidence_score -> diagnosis_confidence
- Add customer_id FK -> customers.id
- Add subscription_id FK -> subscriptions.id
- Add recovery_probability
- Add attribution_class
- Add recovered_amount
- Add recovered_currency
- Add operating_mode
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# ---------------------------------------------------------------------------
# Revision identifiers
# ---------------------------------------------------------------------------

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a1b2c3d4e5"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    # ------------------------------------------------------------------
    # Diagnosis field reconciliation
    #
    # The existing migration created "confidence_score", while the ORM
    # model uses "diagnosis_confidence". The API layer intentionally
    # exposes this as "confidence_score"; the database/ORM name should
    # remain diagnosis_confidence.
    # ------------------------------------------------------------------
    op.alter_column(
        "revenue_risk_cases",
        "confidence_score",
        new_column_name="diagnosis_confidence",
        existing_type=sa.Float(),
        existing_nullable=True,
    )

    # ------------------------------------------------------------------
    # Optional customer relationship
    # ------------------------------------------------------------------
    op.add_column(
        "revenue_risk_cases",
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "revenue_risk_cases_customer_id_fkey",
        "revenue_risk_cases",
        "customers",
        ["customer_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ------------------------------------------------------------------
    # Optional subscription relationship
    # ------------------------------------------------------------------
    op.add_column(
        "revenue_risk_cases",
        sa.Column(
            "subscription_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "revenue_risk_cases_subscription_id_fkey",
        "revenue_risk_cases",
        "subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ------------------------------------------------------------------
    # Recoverability model output
    # ------------------------------------------------------------------
    op.add_column(
        "revenue_risk_cases",
        sa.Column(
            "recovery_probability",
            sa.Float(),
            nullable=True,
        ),
    )

    # ------------------------------------------------------------------
    # Recovery attribution
    # ------------------------------------------------------------------
    op.add_column(
        "revenue_risk_cases",
        sa.Column(
            "attribution_class",
            sa.String(32),
            nullable=True,
        ),
    )

    op.add_column(
        "revenue_risk_cases",
        sa.Column(
            "recovered_amount",
            sa.BigInteger(),
            nullable=True,
        ),
    )

    op.add_column(
        "revenue_risk_cases",
        sa.Column(
            "recovered_currency",
            sa.String(3),
            nullable=True,
        ),
    )

    # ------------------------------------------------------------------
    # Operating mode
    #
    # Existing rows are safe because SIMULATED is the current local/demo
    # operating mode and matches the environment configuration.
    # ------------------------------------------------------------------
    op.add_column(
        "revenue_risk_cases",
        sa.Column(
            "operating_mode",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'SIMULATED'"),
        ),
    )

    op.create_check_constraint(
        "chk_cases_operating_mode_valid",
        "revenue_risk_cases",
        "operating_mode IN ('SIMULATED', 'REAL_TEST_MODE', 'DEMO_FIXTURE')",
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    op.drop_constraint(
        "chk_cases_operating_mode_valid",
        "revenue_risk_cases",
        type_="check",
    )

    op.drop_column(
        "revenue_risk_cases",
        "operating_mode",
    )

    op.drop_column(
        "revenue_risk_cases",
        "recovered_currency",
    )

    op.drop_column(
        "revenue_risk_cases",
        "recovered_amount",
    )

    op.drop_column(
        "revenue_risk_cases",
        "attribution_class",
    )

    op.drop_column(
        "revenue_risk_cases",
        "recovery_probability",
    )

    op.drop_constraint(
        "revenue_risk_cases_subscription_id_fkey",
        "revenue_risk_cases",
        type_="foreignkey",
    )

    op.drop_column(
        "revenue_risk_cases",
        "subscription_id",
    )

    op.drop_constraint(
        "revenue_risk_cases_customer_id_fkey",
        "revenue_risk_cases",
        type_="foreignkey",
    )

    op.drop_column(
        "revenue_risk_cases",
        "customer_id",
    )

    op.alter_column(
        "revenue_risk_cases",
        "diagnosis_confidence",
        new_column_name="confidence_score",
        existing_type=sa.Float(),
        existing_nullable=True,
    )