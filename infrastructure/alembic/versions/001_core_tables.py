"""core tables — merchants, users, token_revocations

Revision ID: 001
Revises: None
Create Date: 2024-01-01 00:00:00.000000

Creates the three core authentication and multi-tenancy tables that all
subsequent migrations depend on:

    merchants          — business entities using Averiq; ``webhook_secret``
                         is stored as Fernet-encrypted ciphertext so it can
                         be retrieved for HMAC-SHA256 signature verification
                         (never stored plaintext, never hashed with bcrypt).

    users              — operator/admin/viewer accounts scoped to a merchant;
                         passwords stored as bcrypt/Argon2 hashes (SEC-AUTH-012).

    token_revocations  — revoked JWT tokens tracked by their ``jti`` claim;
                         enables logout-before-expiry (SEC-AUTH-009).

Constraints & indexes
─────────────────────
  users.role CHECK IN ('ADMIN','OPERATOR','VIEWER','SYSTEM')
  merchants.operating_mode CHECK IN ('SIMULATED','REAL_TEST_MODE','DEMO_FIXTURE')
  uq_users_email          — UNIQUE(email)  [global uniqueness across all merchants]
  idx_users_merchant      — INDEX ON users(merchant_id)
  idx_token_rev_expires   — INDEX ON token_revocations(expires_at)

Requirements: DATA-004, SEC-AUTH-012
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
# Revision identifiers — Alembic uses these to build the chain
# ---------------------------------------------------------------------------
revision: str = "001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


# ---------------------------------------------------------------------------
# upgrade() — create tables in dependency order (merchants first)
# ---------------------------------------------------------------------------

def upgrade() -> None:
    # ------------------------------------------------------------------
    # Table: merchants
    # ------------------------------------------------------------------
    op.create_table(
        "merchants",
        # Primary key — use gen_random_uuid() as the DB-side default so that
        # rows inserted directly (e.g. seed scripts using raw SQL) also get a
        # valid UUID. The ORM layer additionally sets uuid4() in Python.
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            comment="Primary key — gen_random_uuid() in DB",
        ),
        sa.Column("name", sa.String(255), nullable=False),
        # Fernet-encrypted ciphertext — decrypt with APP_SECRET_KEY for HMAC
        # verification.  Must never appear in API responses, structured logs,
        # or exception messages.
        sa.Column(
            "webhook_secret",
            sa.Text,
            nullable=False,
            comment="Application-level encrypted ciphertext; never stored plaintext",
        ),
        # ISO 4217 three-letter currency code; single-currency MVP defaults to INR
        sa.Column(
            "currency",
            sa.String(3),
            nullable=False,
            server_default="INR",
        ),
        # Operating mode governs which gateway implementation is active for this
        # merchant.  DEMO_FIXTURE is included so seed fixtures can self-identify.
        sa.Column(
            "operating_mode",
            sa.String(32),
            nullable=False,
            server_default="SIMULATED",
        ),
        # DATA-004 — all timestamps are TIMESTAMP WITH TIME ZONE (UTC)
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        # CHECK constraint: only permitted operating modes
        sa.CheckConstraint(
            "operating_mode IN ('SIMULATED', 'REAL_TEST_MODE', 'DEMO_FIXTURE')",
            name="ck_merchants_operating_mode",
        ),
    )

    # ------------------------------------------------------------------
    # Table: users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        # bcrypt/Argon2 hash — SEC-AUTH-012: never plaintext, never reversible
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        # DATA-004 — timezone-aware UTC
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        # CHECK constraint: only permitted role values
        # SYSTEM is reserved for internal service-to-service use only;
        # it must never be assignable to a human user via any API endpoint.
        sa.CheckConstraint(
            "role IN ('ADMIN', 'OPERATOR', 'VIEWER', 'SYSTEM')",
            name="ck_users_role",
        ),
        # Global uniqueness of email across all merchants (mirrors ORM)
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    # Index on merchant_id for fast per-merchant user lookups
    op.create_index(
        "idx_users_merchant",
        "users",
        ["merchant_id"],
    )

    # ------------------------------------------------------------------
    # Table: token_revocations
    # ------------------------------------------------------------------
    op.create_table(
        "token_revocations",
        # jti (JWT ID claim) is the primary key and the fast lookup key.
        # Using it as PK rather than a surrogate UUID avoids a redundant
        # UNIQUE index while keeping the table compact.
        sa.Column(
            "jti",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            comment="JWT ID claim from the token",
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # DATA-004 — timezone-aware UTC
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        # Background cleanup purges rows where expires_at < NOW()
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )

    # Index for fast cleanup and lookup of expired revocations
    op.create_index(
        "idx_token_rev_expires",
        "token_revocations",
        ["expires_at"],
    )


# ---------------------------------------------------------------------------
# downgrade() — drop tables in reverse dependency order
# ---------------------------------------------------------------------------

def downgrade() -> None:
    # Drop in reverse creation order to satisfy FK constraints
    op.drop_index("idx_token_rev_expires", table_name="token_revocations")
    op.drop_table("token_revocations")

    op.drop_index("idx_users_merchant", table_name="users")
    op.drop_table("users")

    op.drop_table("merchants")
