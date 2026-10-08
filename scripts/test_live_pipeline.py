import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from backend.domain.models import (
    Merchant, User, Policy, Customer, Payment, RevenueRiskCase,
    Intervention, ExternalOperation, VerificationAttempt, AuditEvent
)
from backend.decision_engine.tasks import run_decision_pipeline
from backend.execution_engine.tasks import execute_intervention
from backend.verification_engine.tasks import run_verification

DATABASE_URL = "postgresql+asyncpg://averiq:averiq@localhost:5432/averiq"

async def test_live_pipeline():
    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    # IDs
    case_id = uuid.uuid4()
    merchant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    customer_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    
    print(f"\n=======================================================")
    print(f"STEP 1: Seeding active payment & case in AT_RISK")
    print(f"Case ID: {case_id}")
    print(f"Merchant ID: {merchant_id}")
    print(f"Payment ID: {payment_id}")
    print(f"=======================================================")

    async with session_factory() as session:
        # Check merchant exists
        m = (await session.execute(select(Merchant).where(Merchant.id == merchant_id))).scalar_one_or_none()
        if not m:
            print("ERROR: Demo merchant not found. Did you run seed.py?")
            return False
            
        # Create Customer
        cust = Customer(
            id=customer_id,
            merchant_id=merchant_id,
            gateway_customer_id=f"cust_{case_id.hex[:8]}",
            name="Live Pipeline Tester",
            email="tester@example.com",
            phone="+919876543210",
            created_at=now,
            updated_at=now,
        )
        session.add(cust)
        
        # Create Payment
        pay = Payment(
            id=payment_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            gateway_payment_id=f"pay_{case_id.hex[:8]}",
            amount=250000, # Rs 2,500.00
            currency="INR",
            status="failed",
            method="card",
            error_code="BAD_REQUEST_ERROR",
            error_description="Card declined due to insufficient funds",
            failed_at=now,
            extra_metadata={"test": True},
            created_at=now,
            updated_at=now,
        )
        session.add(pay)
        
        # Create RevenueRiskCase in AT_RISK
        case = RevenueRiskCase(
            id=case_id,
            merchant_id=merchant_id,
            payment_id=payment_id,
            customer_id=customer_id,
            state="AT_RISK",
            payment_amount_at_risk=250000,
            currency="INR",
            risk_calculation_path="SINGLE_PAYMENT",
            exposure_tier="MEDIUM",
            recovery_risk_tier="MEDIUM",
            failure_category="INSUFFICIENT_FUNDS",
            is_demo_fixture=False,
            operating_mode="SIMULATED",
            case_metadata={"live_test": True},
            created_at=now,
            updated_at=now,
        )
        session.add(case)
        await session.commit()
    print("Database seeded with AT_RISK case.")

    print(f"\n=======================================================")
    print(f"STEP 2: Dispatching Celery run_decision_pipeline task")
    print(f"=======================================================")
    res = run_decision_pipeline.delay(str(case_id), str(merchant_id))
    print(f"Celery Task ID: {res.id}")

    # Poll for case decision
    max_wait = 15
    waited = 0
    decision_state = None
    selected_intervention_id = None
    
    while waited < max_wait:
        await asyncio.sleep(1)
        waited += 1
        async with session_factory() as session:
            c = (await session.execute(select(RevenueRiskCase).where(RevenueRiskCase.id == case_id))).scalar_one_or_none()
            if c and c.state != "AT_RISK":
                decision_state = c.state
                print(f"Decision reached! State: {decision_state} (waited {waited}s)")
                # Check interventions
                intv_stmt = select(Intervention).where(Intervention.case_id == case_id)
                intvs = (await session.execute(intv_stmt)).scalars().all()
                print(f"Generated interventions count: {len(intvs)}")
                for i in intvs:
                    print(f"  -> Type: {i.intervention_type}, PolicyDecision: {i.policy_decision}, ExpectedValue: {i.expected_recovery_value}")
                    if i.policy_decision == "APPROVED" and not selected_intervention_id:
                        selected_intervention_id = i.id
                break
            print(f"Waiting for decision... ({waited}s)")

    if not decision_state:
        print("FAIL: Decision pipeline timed out.")
        return False

    print(f"\n=======================================================")
    print(f"STEP 3: Checking Execution & Verification Transitions")
    print(f"=======================================================")
    # Celery automatically enqueues execution if APPROVED!
    # Let's wait for execution and verification transitions
    waited = 0
    while waited < max_wait:
        await asyncio.sleep(1)
        waited += 1
        async with session_factory() as session:
            c = (await session.execute(select(RevenueRiskCase).where(RevenueRiskCase.id == case_id))).scalar_one_or_none()
            ops = (await session.execute(select(ExternalOperation).where(ExternalOperation.case_id == case_id))).scalars().all()
            print(f"Current State: {c.state}, External Operations: {len(ops)} (waited {waited}s)")
            if c.state in ("ACTION_EXECUTING", "VERIFYING", "RECOVERED", "STOPPED", "FAILED", "ESCALATED"):
                if len(ops) > 0:
                    op = ops[0]
                    print(f"External Operation details: id={op.id}, action_type={op.action_type}, status={op.status}, action_id={op.action_id}")
                    
                    if c.state in ("RECOVERED", "STOPPED", "FAILED", "ESCALATED"):
                        print(f"Terminal state achieved: {c.state}!")
                        break
                    elif c.state == "VERIFYING":
                        # Only wait for Celery worker to process scheduled attempts
                        pass

    # Final summary of case and audits
    async with session_factory() as session:
        c = (await session.execute(select(RevenueRiskCase).where(RevenueRiskCase.id == case_id))).scalar_one_or_none()
        audits = (await session.execute(select(AuditEvent).where(AuditEvent.affected_entity_id == str(case_id)))).scalars().all()
        print(f"\n=======================================================")
        print(f"FINAL AUDIT TRAIL FOR CASE {case_id}")
        print(f"Final State: {c.state}")
        print(f"Audit Events Count: {len(audits)}")
        for a in audits:
            reason_str = a.payload.get("reason", "") if a.payload else ""
            print(f"  [{a.created_at}] actor={a.actor} action={a.action_type} ({a.from_state} -> {a.to_state}) reason='{reason_str}'")
        print(f"=======================================================")

    return True

if __name__ == '__main__':
    asyncio.run(test_live_pipeline())
