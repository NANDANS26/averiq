#!/usr/bin/env python3
"""
make seed — seed the database with DEMO_FIXTURE data.

Creates a deterministic set of demo records for local development and demos.
All records are marked is_demo_fixture=True.
Idempotent: running twice does not create duplicates.

Named fixtures:
  DEMO-TRANSIENT-001   — standalone payment failure; expected path: PAYMENT_LINK → RECOVERED
  DEMO-SUBSCRIPTION-001 — subscription failure; expected path: CUSTOMER_ACTION → per domain logic
  DEMO-DUPLICATE-001   — duplicate webhook demo; no duplicate domain events on second delivery
  DEMO-ESCALATED-001   — verification budget exhausted → ESCALATED
  DEMO-STOPPED-001     — policy blocked all candidates → STOPPED
  DEMO-FAILED-001      — known execution failure → FAILED

CRITICAL: Case states here represent "already processed" demo snapshots. The
domain logic (state machine, intervention engine, policy engine) determines all
valid transitions. This script seeds known terminal/intermediate states for
dashboard demonstration. No fixture IDs are special-cased in domain code.

Usage:
    python scripts/seed.py   (or: make seed)

Requirements: DEMO_FIXTURE mode, NFR-DEV-003, DEMO-001 through DEMO-006
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from backend.config import settings

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ──────────────────────────────────────────────────────────────────────────────
# Deterministic fixture identifiers (fixed UUIDs for idempotency)
# ──────────────────────────────────────────────────────────────────────────────

DEMO_MERCHANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEMO_MERCHANT_NAME = "Averiq Demo Merchant"

DEMO_ADMIN_EMAIL = "admin@averiq.demo"
DEMO_OPERATOR_EMAIL = "operator@averiq.demo"

# bcrypt hash of "demo_password_123" (cost factor 12)
DEMO_PASSWORD_HASH = (
    "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQyCXvBxsHf.m12P8IVhtlPo2"
)

DEMO_POLICY_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")

# Fixed payment IDs for named fixtures (idempotency)
_PAYMENT_IDS: dict[str, uuid.UUID] = {
    "DEMO-TRANSIENT-001":    uuid.UUID("00000000-0000-0000-0001-000000000001"),
    "DEMO-SUBSCRIPTION-001": uuid.UUID("00000000-0000-0000-0001-000000000002"),
    "DEMO-DUPLICATE-001":    uuid.UUID("00000000-0000-0000-0001-000000000003"),
    "DEMO-ESCALATED-001":    uuid.UUID("00000000-0000-0000-0001-000000000004"),
    "DEMO-STOPPED-001":      uuid.UUID("00000000-0000-0000-0001-000000000005"),
    "DEMO-FAILED-001":       uuid.UUID("00000000-0000-0000-0001-000000000006"),
}

# Fixed case IDs for named fixtures
_CASE_IDS: dict[str, uuid.UUID] = {
    "DEMO-TRANSIENT-001":    uuid.UUID("00000000-0000-0000-0002-000000000001"),
    "DEMO-SUBSCRIPTION-001": uuid.UUID("00000000-0000-0000-0002-000000000002"),
    "DEMO-DUPLICATE-001":    uuid.UUID("00000000-0000-0000-0002-000000000003"),
    "DEMO-ESCALATED-001":    uuid.UUID("00000000-0000-0000-0002-000000000004"),
    "DEMO-STOPPED-001":      uuid.UUID("00000000-0000-0000-0002-000000000005"),
    "DEMO-FAILED-001":       uuid.UUID("00000000-0000-0000-0002-000000000006"),
}

# Fixed customer IDs
_CUSTOMER_IDS: dict[str, uuid.UUID] = {
    "DEMO-TRANSIENT-001":    uuid.UUID("00000000-0000-0000-0003-000000000001"),
    "DEMO-SUBSCRIPTION-001": uuid.UUID("00000000-0000-0000-0003-000000000002"),
    "DEMO-DUPLICATE-001":    uuid.UUID("00000000-0000-0000-0003-000000000003"),
    "DEMO-ESCALATED-001":    uuid.UUID("00000000-0000-0000-0003-000000000004"),
    "DEMO-STOPPED-001":      uuid.UUID("00000000-0000-0000-0003-000000000005"),
    "DEMO-FAILED-001":       uuid.UUID("00000000-0000-0000-0003-000000000006"),
}

DEMO_SUBSCRIPTION_ID = uuid.UUID("00000000-0000-0000-0004-000000000001")

# ──────────────────────────────────────────────────────────────────────────────
# Demo case definitions
#
# NOTE: States here represent "already processed" demo snapshots for dashboard
# demonstration.  The full domain pipeline (webhook → AT_RISK → ... → terminal)
# runs when the Playwright E2E golden path posts a live webhook and the
# FakeRazorpayGateway processes it.  No fixture ID is special-cased in any
# domain code — these are plain DB records in whatever state they were
# seeded into.
# ──────────────────────────────────────────────────────────────────────────────

_FIXTURE_SPECS: list[dict] = [
    {
        "name": "DEMO-TRANSIENT-001",
        "description": "Standalone payment failure — PAYMENT_LINK intervention path",
        "expected_outcome": (
            "RECOVERED via PAYMENT_LINK (EXECUTABLE, VERIFIED) — "
            "FakeRazorpayGateway confirms link paid + payment captured"
        ),
        # Seeded in OBSERVED so the golden-path E2E test can run the full pipeline
        "state": "OBSERVED",
        "amount": 250_000,          # 2500 INR in paise
        "currency": "INR",
        "failure_category": "INSUFFICIENT_FUNDS",
        "risk_calculation_path": "SINGLE_PAYMENT",
        "exposure_tier": "HIGH",
        "recovery_risk_tier": "LOW",
        "error_code": "BAD_REQUEST_ERROR",
        "gateway_payment_id": "pay_demo_transient_001",
        "gateway_customer_id": "cust_demo_transient_001",
    },
    {
        "name": "DEMO-SUBSCRIPTION-001",
        "description": "Subscription failure — CUSTOMER_ACTION intervention path",
        "expected_outcome": (
            "RECOMMENDATION_DELIVERED or RECOVERED — depends on CUSTOMER_ACTION "
            "capability. If RECOMMENDATION_ONLY: RECOMMENDATION_DELIVERED. "
            "If EXECUTABLE: RECOVERED after subscription goes active + "
            "overdue payment captured (FakeRazorpayGateway)."
        ),
        "state": "DIAGNOSED",
        "amount": 149_900,          # 1499 INR in paise
        "currency": "INR",
        "failure_category": "CARD_DECLINED",
        "risk_calculation_path": "SUBSCRIPTION_PROJECTED",
        "exposure_tier": "MEDIUM",
        "recovery_risk_tier": "MEDIUM",
        "error_code": "GATEWAY_ERROR",
        "gateway_payment_id": "pay_demo_subscription_001",
        "gateway_customer_id": "cust_demo_subscription_001",
        "has_subscription": True,
    },
    {
        "name": "DEMO-DUPLICATE-001",
        "description": "Duplicate webhook demonstration",
        "expected_outcome": (
            "HTTP 200 on second delivery of same provider_event_id; "
            "no duplicate domain events; single WebhookEvent row in DB"
        ),
        "state": "AT_RISK",
        "amount": 99_900,           # 999 INR in paise
        "currency": "INR",
        "failure_category": "TECHNICAL_FAILURE",
        "risk_calculation_path": "SINGLE_PAYMENT",
        "exposure_tier": "LOW",
        "recovery_risk_tier": "LOW",
        "error_code": "SERVER_ERROR",
        "gateway_payment_id": "pay_demo_duplicate_001",
        "gateway_customer_id": "cust_demo_duplicate_001",
    },
    {
        "name": "DEMO-ESCALATED-001",
        "description": "Max re-verification attempts exhausted",
        "expected_outcome": (
            "ESCALATED — VerificationEngine exhausted 3 re-verification "
            "attempts with UNKNOWN gateway status"
        ),
        "state": "ESCALATED",
        "amount": 500_000,          # 5000 INR in paise
        "currency": "INR",
        "failure_category": "NETWORK_TIMEOUT",
        "risk_calculation_path": "SINGLE_PAYMENT",
        "exposure_tier": "HIGH",
        "recovery_risk_tier": "HIGH",
        "error_code": "GATEWAY_ERROR",
        "gateway_payment_id": "pay_demo_escalated_001",
        "gateway_customer_id": "cust_demo_escalated_001",
    },
    {
        "name": "DEMO-STOPPED-001",
        "description": "PolicyEngine blocked all candidate interventions",
        "expected_outcome": (
            "STOPPED — PolicyEngine applied confidence_threshold + amount_limit "
            "rules; no positive EV candidate remained; STOP intervention selected"
        ),
        "state": "STOPPED",
        "amount": 25_000,           # 250 INR in paise
        "currency": "INR",
        "failure_category": "FRAUD_SUSPECTED",
        "risk_calculation_path": "SINGLE_PAYMENT",
        "exposure_tier": "LOW",
        "recovery_risk_tier": "HIGH",
        "error_code": "BAD_REQUEST_ERROR",
        "gateway_payment_id": "pay_demo_stopped_001",
        "gateway_customer_id": "cust_demo_stopped_001",
    },
    {
        "name": "DEMO-FAILED-001",
        "description": "Known unrecoverable execution failure",
        "expected_outcome": (
            "FAILED — ExecutionEngine received a definitive gateway error "
            "after reconciliation confirmed provider-side failure"
        ),
        "state": "FAILED",
        "amount": 175_000,          # 1750 INR in paise
        "currency": "INR",
        "failure_category": "EXPIRED_CARD",
        "risk_calculation_path": "SINGLE_PAYMENT",
        "exposure_tier": "MEDIUM",
        "recovery_risk_tier": "HIGH",
        "error_code": "BAD_REQUEST_ERROR",
        "gateway_payment_id": "pay_demo_failed_001",
        "gateway_customer_id": "cust_demo_failed_001",
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────


async def seed() -> None:  # noqa: C901 — intentionally long orchestrator
    """Seed demo fixture data into the database.

    Steps:
      1. Merchant (SIMULATED mode)
      2. Admin + Operator users
      3. Active policy with sensible defaults
      4. Customers (one per fixture)
      5. Optional subscription (DEMO-SUBSCRIPTION-001)
      6. Payments (one per fixture)
      7. RevenueRiskCases (one per fixture, in the seeded state)

    Idempotency: each resource is inserted only when absent (checked by fixed
    UUID or unique constraint key).  Running twice is safe.
    """
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://averiq:averiq@localhost:5432/averiq",
    )

    try:
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )
    except ImportError:
        print(
            "ERROR: SQLAlchemy not installed. "
            "Run: pip install sqlalchemy[asyncio] asyncpg"
        )
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    try:
        async with session_factory() as session:
            async with session.begin():
                await _seed_merchant(session)
                await _seed_users(session)
                await _seed_policy(session)
                await _seed_customers(session)
                await _seed_subscription(session)
                await _seed_payments(session)
                await _seed_cases(session)

        _print_summary()

    except Exception as exc:  # noqa: BLE001
        print(f"\nWARNING: Seeding failed — {exc}")
        print(
            "  Is the database running?  "
            "Try: docker compose up -d postgres redis"
        )
        # Exit 0 so `make setup` is not interrupted by a missing DB
        sys.exit(0)
    finally:
        await engine.dispose()


# ──────────────────────────────────────────────────────────────────────────────
# Seeding helpers
# ──────────────────────────────────────────────────────────────────────────────


async def _seed_merchant(session: AsyncSession) -> None:
    """Create the demo merchant in SIMULATED mode (idempotent)."""
    from sqlalchemy import select

    from backend.domain.crypto import encrypt_secret
    from backend.domain.models import Merchant

    existing = (
        await session.execute(
            select(Merchant).where(Merchant.id == DEMO_MERCHANT_ID)
        )
    ).scalar_one_or_none()
    if existing:
        return

    # Use APP_SECRET_KEY if set; fall back to a fixed demo key so that the
    # seed never fails purely because the env var is absent.
    app_key = settings.APP_SECRET_KEY
    encrypted_secret = encrypt_secret("whsec_demo_webhook_secret_for_averiq", app_key)

    now = _now()
    merchant = Merchant(
        id=DEMO_MERCHANT_ID,
        name=DEMO_MERCHANT_NAME,
        webhook_secret=encrypted_secret,
        currency="INR",
        operating_mode="SIMULATED",
        created_at=now,
        updated_at=now,
    )
    session.add(merchant)
    print(f"  ✓ Merchant:  {DEMO_MERCHANT_NAME} (SIMULATED mode)")


async def _seed_users(session: AsyncSession) -> None:
    """Create demo ADMIN and OPERATOR users (idempotent)."""
    from sqlalchemy import select

    from backend.domain.models import User

    for email, role in [
        (DEMO_ADMIN_EMAIL, "ADMIN"),
        (DEMO_OPERATOR_EMAIL, "OPERATOR"),
    ]:
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing:
            continue

        now = _now()
        user = User(
            id=uuid.uuid4(),
            merchant_id=DEMO_MERCHANT_ID,
            email=email,
            password_hash=DEMO_PASSWORD_HASH,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        print(f"  ✓ User:      {email} ({role})")


async def _seed_policy(session: AsyncSession) -> None:
    """Create an active policy with sensible defaults (idempotent)."""
    from sqlalchemy import select

    from backend.domain.models import Policy

    existing = (
        await session.execute(
            select(Policy).where(Policy.id == DEMO_POLICY_ID)
        )
    ).scalar_one_or_none()
    if existing:
        return

    now = _now()
    policy = Policy(
        id=DEMO_POLICY_ID,
        merchant_id=DEMO_MERCHANT_ID,
        version=1,
        lifecycle_state="active",
        configuration={
            # Confidence threshold below which interventions are blocked
            "min_confidence": 0.5,
            # Minimum seconds between interventions on the same case
            "cooldown_seconds": 300,
            # Maximum retries per intervention type
            "max_retries": {"RETRY": 3, "PAYMENT_LINK": 2},
            # Maximum amount (paise) per intervention type
            "max_amount": {"INR": 10_000_000},
            # Route to human review above this threshold (paise)
            "human_approval_threshold": 1_000_000,
            # Attribution window (seconds) after intervention execution
            "attribution_window_seconds": 86_400,
            # Exposure tiers (paise thresholds)
            "exposure_thresholds": {"HIGH": 200_000, "MEDIUM": 50_000},
            # Per-intervention EV cost and risk_penalty (paise)
            "intervention_costs": {
                "PAYMENT_LINK": {"cost": 0, "risk_penalty": 0},
                "CUSTOMER_ACTION": {"cost": 0, "risk_penalty": 0},
                "RETRY": {"cost": 0, "risk_penalty": 0},
            },
        },
        activated_at=now,
        created_at=now,
    )
    session.add(policy)
    print("  ✓ Policy:    v1 (active) — sensible defaults for INR merchant")


async def _seed_customers(session: AsyncSession) -> None:
    """Create one Customer per demo fixture (idempotent)."""
    from sqlalchemy import select

    from backend.domain.models import Customer

    for spec in _FIXTURE_SPECS:
        name = spec["name"]
        customer_id = _CUSTOMER_IDS[name]
        gateway_id = spec["gateway_customer_id"]

        existing = (
            await session.execute(
                select(Customer).where(Customer.id == customer_id)
            )
        ).scalar_one_or_none()
        if existing:
            continue

        now = _now()
        idx = list(_CUSTOMER_IDS.keys()).index(name) + 1
        customer = Customer(
            id=customer_id,
            merchant_id=DEMO_MERCHANT_ID,
            gateway_customer_id=gateway_id,
            name=f"Demo Customer {idx} ({name})",
            email=f"customer{idx}@demo.averiq.example",
            phone=f"+9190000000{idx:02d}",
            extra_metadata={"fixture": name, "is_demo": True},
            created_at=now,
            updated_at=now,
        )
        session.add(customer)


async def _seed_subscription(session: AsyncSession) -> None:
    """Create a demo subscription for DEMO-SUBSCRIPTION-001 (idempotent)."""
    from sqlalchemy import select

    from backend.domain.models import Subscription

    existing = (
        await session.execute(
            select(Subscription).where(Subscription.id == DEMO_SUBSCRIPTION_ID)
        )
    ).scalar_one_or_none()
    if existing:
        return

    now = _now()
    subscription = Subscription(
        id=DEMO_SUBSCRIPTION_ID,
        merchant_id=DEMO_MERCHANT_ID,
        customer_id=_CUSTOMER_IDS["DEMO-SUBSCRIPTION-001"],
        gateway_subscription_id="sub_demo_subscription_001",
        plan_id="plan_demo_monthly_001",
        status="halted",
        quantity=1,
        billing_amount=149_900,     # 1499 INR in paise (DATA-001)
        currency="INR",
        extra_metadata={"fixture": "DEMO-SUBSCRIPTION-001", "is_demo": True},
        created_at=now,
        updated_at=now,
    )
    session.add(subscription)
    print("  ✓ Subscription: sub_demo_subscription_001 (halted — DEMO-SUBSCRIPTION-001)")


async def _seed_payments(session: AsyncSession) -> None:
    """Create one failed Payment per demo fixture (idempotent)."""
    from sqlalchemy import select

    from backend.domain.models import Payment

    for spec in _FIXTURE_SPECS:
        name = spec["name"]
        payment_id = _PAYMENT_IDS[name]

        existing = (
            await session.execute(
                select(Payment).where(Payment.id == payment_id)
            )
        ).scalar_one_or_none()
        if existing:
            continue

        now = _now()
        subscription_id = (
            DEMO_SUBSCRIPTION_ID if spec.get("has_subscription") else None
        )
        payment = Payment(
            id=payment_id,
            merchant_id=DEMO_MERCHANT_ID,
            customer_id=_CUSTOMER_IDS[name],
            subscription_id=subscription_id,
            gateway_payment_id=spec["gateway_payment_id"],
            amount=spec["amount"],
            currency=spec["currency"],
            status="failed",
            method="card",
            error_code=spec["error_code"],
            error_description=f"Demo failure for {name}",
            failed_at=now - timedelta(minutes=5),
            extra_metadata={"fixture": name, "is_demo": True},
            created_at=now - timedelta(minutes=5),
            updated_at=now,
        )
        session.add(payment)


async def _seed_cases(session: AsyncSession) -> None:
    """Create one RevenueRiskCase per demo fixture in its seeded state (idempotent).

    Cases are created in the state that best demonstrates each fixture's
    intended scenario:

    - DEMO-TRANSIENT-001:    OBSERVED  — ready for the golden-path E2E pipeline
    - DEMO-SUBSCRIPTION-001: DIAGNOSED — shows diagnosis output in the UI
    - DEMO-DUPLICATE-001:    AT_RISK   — shows early lifecycle
    - DEMO-ESCALATED-001:    ESCALATED — shows terminal escalated state
    - DEMO-STOPPED-001:      STOPPED   — shows terminal stopped state
    - DEMO-FAILED-001:       FAILED    — shows terminal failed state

    No state is hardcoded as RECOVERED here; recovery outcomes are produced
    only by the domain pipeline (StateMachine + VerificationEngine +
    FakeRazorpayGateway) — never injected directly.
    """
    from sqlalchemy import select

    from backend.domain.models import RevenueRiskCase

    now = _now()

    for spec in _FIXTURE_SPECS:
        name = spec["name"]
        case_id = _CASE_IDS[name]

        existing = (
            await session.execute(
                select(RevenueRiskCase).where(RevenueRiskCase.id == case_id)
            )
        ).scalar_one_or_none()
        if existing:
            continue

        subscription_id = (
            DEMO_SUBSCRIPTION_ID if spec.get("has_subscription") else None
        )

        case = RevenueRiskCase(
            id=case_id,
            merchant_id=DEMO_MERCHANT_ID,
            payment_id=_PAYMENT_IDS[name],
            customer_id=_CUSTOMER_IDS[name],
            subscription_id=subscription_id,
            state=spec["state"],
            payment_amount_at_risk=spec["amount"],
            currency=spec["currency"],
            risk_calculation_path=spec["risk_calculation_path"],
            exposure_tier=spec["exposure_tier"],
            recovery_risk_tier=spec["recovery_risk_tier"],
            failure_category=spec["failure_category"],
            is_demo_fixture=True,
            operating_mode="SIMULATED",
            case_metadata={
                "fixture_name": name,
                "fixture_description": spec["description"],
                "expected_outcome": spec["expected_outcome"],
                "seeded_by": "make seed",
                "is_demo": True,
            },
            created_at=now - timedelta(minutes=5),
            updated_at=now,
        )
        session.add(case)

    print(
        f"  ✓ Cases:     {len(_FIXTURE_SPECS)} demo cases seeded "
        "(DEMO FIXTURE — is_demo_fixture=True)"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime (DATA-004)."""
    return datetime.now(timezone.utc)


def _print_summary() -> None:
    """Print a human-readable summary of all seeded fixtures."""
    print("\n✓ Demo fixtures seeded successfully.\n")
    print("  Credentials:")
    print(f"    Admin:    {DEMO_ADMIN_EMAIL}  /  demo_password_123")
    print(f"    Operator: {DEMO_OPERATOR_EMAIL}  /  demo_password_123")
    print()
    print("  Demo fixtures (all labeled DEMO FIXTURE in UI, logs, and API):")
    print()
    for spec in _FIXTURE_SPECS:
        print(f"  [{spec['name']}]")
        print(f"    Description:      {spec['description']}")
        print(f"    Seeded state:     {spec['state']}")
        print(f"    Expected outcome: {spec['expected_outcome']}")
        amount_inr = spec["amount"] / 100
        print(f"    Amount at risk:   ₹{amount_inr:.2f} ({spec['amount']} paise)")
        print()
    print(
        "  NOTE: Outcomes for OBSERVED/AT_RISK/DIAGNOSED cases are produced by the\n"
        "  domain pipeline (StateMachine + FakeRazorpayGateway), not seeded directly.\n"
        "  Run the Playwright E2E golden-path test to see the full pipeline execute."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    asyncio.run(seed())
