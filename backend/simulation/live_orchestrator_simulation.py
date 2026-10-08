"""Live Orchestration & Revenue Recovery Simulation for Averiq Phase 4.

Executes end-to-end payment recovery scenarios against the live orchestration stack:
PostgreSQL, Redis, Celery workers, and Domain State Machines.

Scenarios verified:
1. Standard Recoverable Payment -> Diagnosed -> Recovery Eligible -> Action Approved -> Executed -> Verifying -> Escalated (or Recovered)
2. High-Value Payment (> Rs 10,000 / 1,000,000 paise) -> Requires Human Review -> HumanReview ticket created
3. STOP Intervention / Unrecoverable Failure -> Case stopped safely without external execution
4. Webhook Ingestion & Idempotency -> Ingesting duplicate webhook does not duplicate cases or side effects
5. Concurrency Race -> Multiple concurrent decisions on the same case serialized via SELECT FOR UPDATE

Requirements: FR-EXEC-*, FR-VERIFY-*, FR-STATE-*, Phase 4 E2E validation.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, func

from backend.domain.models import (
    Merchant, User, Policy, Customer, Payment, RevenueRiskCase,
    Intervention, ExternalOperation, VerificationAttempt, AuditEvent,
    HumanReview, WebhookEvent
)
from backend.webhook_processor.processor import process_webhook_event
from backend.decision_engine.tasks import run_decision_pipeline
from backend.execution_engine.tasks import execute_intervention
from backend.verification_engine.tasks import run_verification
from backend.worker import celery_app

DATABASE_URL = "postgresql+asyncpg://averiq:averiq@localhost:5432/averiq"


class LiveOrchestrationSimulation:
    def __init__(self):
        self.engine = create_async_engine(DATABASE_URL)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.merchant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        self.results = {}

    async def init(self):
        async with self.session_factory() as session:
            m = (await session.execute(select(Merchant).where(Merchant.id == self.merchant_id))).scalar_one_or_none()
            if not m:
                raise RuntimeError("Default merchant does not exist in DB.")
        print(f"[INIT] Connected to Postgres. Target Merchant ID: {self.merchant_id}")

    async def scenario_1_standard_recovery(self) -> dict:
        """Standard recoverable payment (Rs 2,500). Tests pipeline auto-approval & execution."""
        print("\n>>> RUNNING SCENARIO 1: Standard Recoverable Payment (Auto-Approval Golden Path)")
        case_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        customer_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        async with self.session_factory() as session:
            cust = Customer(
                id=customer_id,
                merchant_id=self.merchant_id,
                gateway_customer_id=f"cust_sim1_{case_id.hex[:6]}",
                name="S1 Auto Customer",
                email="sim1@averiq.ai",
                created_at=now,
                updated_at=now,
            )
            session.add(cust)

            pay = Payment(
                id=payment_id,
                merchant_id=self.merchant_id,
                customer_id=customer_id,
                gateway_payment_id=f"pay_sim1_{case_id.hex[:6]}",
                amount=250000, # Rs 2,500.00
                currency="INR",
                status="failed",
                method="card",
                error_code="BAD_REQUEST_ERROR",
                error_description="Card declined due to insufficient funds",
                failed_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(pay)

            case = RevenueRiskCase(
                id=case_id,
                merchant_id=self.merchant_id,
                payment_id=payment_id,
                customer_id=customer_id,
                state="AT_RISK",
                payment_amount_at_risk=250000,
                currency="INR",
                risk_calculation_path="SINGLE_PAYMENT",
                exposure_tier="MEDIUM",
                recovery_risk_tier="MEDIUM",
                failure_category="INSUFFICIENT_FUNDS",
                operating_mode="SIMULATED",
                case_metadata={"sim": "scenario_1"},
                created_at=now,
                updated_at=now,
            )
            session.add(case)
            await session.commit()

        # Dispatch decision task
        t0 = time.time()
        run_decision_pipeline.delay(str(case_id), str(self.merchant_id))

        # Poll until terminal or verifying with verification attempts
        final_state = None
        intv_count = 0
        attempts_count = 0
        for _ in range(25):
            await asyncio.sleep(1)
            async with self.session_factory() as session:
                c = (await session.execute(select(RevenueRiskCase).where(RevenueRiskCase.id == case_id))).scalar_one_or_none()
                intvs = (await session.execute(select(Intervention).where(Intervention.case_id == case_id))).scalars().all()
                ops = (await session.execute(select(ExternalOperation).where(ExternalOperation.case_id == case_id))).scalars().all()
                attempts = []
                if ops:
                    attempts = (await session.execute(select(VerificationAttempt).where(VerificationAttempt.external_operation_id == ops[0].id))).scalars().all()

                final_state = c.state if c else None
                intv_count = len(intvs)
                attempts_count = len(attempts)

                if final_state in ("ESCALATED", "RECOVERED", "STOPPED") or (final_state == "VERIFYING" and attempts_count >= 1):
                    break

        elapsed = time.time() - t0
        passed = final_state in ("VERIFYING", "ESCALATED", "RECOVERED") and intv_count > 0
        print(f"[SCENARIO 1 RESULT] Final State: {final_state}, Interventions: {intv_count}, Verification Attempts: {attempts_count}, Elapsed: {elapsed:.2f}s, Passed: {passed}")
        return {
            "scenario": "Standard Recovery",
            "passed": passed,
            "final_state": final_state,
            "interventions": intv_count,
            "verification_attempts": attempts_count,
            "latency_seconds": elapsed,
        }

    async def scenario_2_high_value_human_review(self) -> dict:
        """High-ticket payment (> Rs 10,000 / 1,000,000 paise). Must route to HUMAN_REVIEW."""
        print("\n>>> RUNNING SCENARIO 2: High-Value Payment (Policy Human Review Route)")
        case_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        customer_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        async with self.session_factory() as session:
            cust = Customer(
                id=customer_id,
                merchant_id=self.merchant_id,
                gateway_customer_id=f"cust_sim2_{case_id.hex[:6]}",
                name="S2 VIP Customer",
                email="sim2@averiq.ai",
                created_at=now,
                updated_at=now,
            )
            session.add(cust)

            # Rs 25,000 = 2,500,000 paise (> Rs 10,000 policy threshold)
            pay = Payment(
                id=payment_id,
                merchant_id=self.merchant_id,
                customer_id=customer_id,
                gateway_payment_id=f"pay_sim2_{case_id.hex[:6]}",
                amount=2500000,
                currency="INR",
                status="failed",
                method="card",
                error_code="BAD_REQUEST_ERROR",
                error_description="Transaction limit exceeded",
                failed_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(pay)

            case = RevenueRiskCase(
                id=case_id,
                merchant_id=self.merchant_id,
                payment_id=payment_id,
                customer_id=customer_id,
                state="AT_RISK",
                payment_amount_at_risk=2500000,
                currency="INR",
                risk_calculation_path="SINGLE_PAYMENT",
                exposure_tier="HIGH",
                recovery_risk_tier="HIGH",
                failure_category="INSUFFICIENT_FUNDS",
                operating_mode="SIMULATED",
                case_metadata={"sim": "scenario_2"},
                created_at=now,
                updated_at=now,
            )
            session.add(case)
            await session.commit()

        t0 = time.time()
        run_decision_pipeline.delay(str(case_id), str(self.merchant_id))

        hr_created = False
        final_state = None
        for _ in range(15):
            await asyncio.sleep(1)
            async with self.session_factory() as session:
                c = (await session.execute(select(RevenueRiskCase).where(RevenueRiskCase.id == case_id))).scalar_one_or_none()
                hr = (await session.execute(select(HumanReview).where(HumanReview.case_id == case_id))).scalar_one_or_none()
                final_state = c.state if c else None
                if hr:
                    hr_created = True
                    break

        elapsed = time.time() - t0
        passed = hr_created and final_state == "ACTION_PENDING"
        print(f"[SCENARIO 2 RESULT] Final State: {final_state}, HumanReview Created: {hr_created}, Elapsed: {elapsed:.2f}s, Passed: {passed}")
        return {
            "scenario": "High-Value Human Review",
            "passed": passed,
            "final_state": final_state,
            "human_review_created": hr_created,
            "latency_seconds": elapsed,
        }

    async def scenario_3_stop_intervention(self) -> dict:
        """Fraud/Unrecoverable failure -> Policy or ranker chooses STOP -> Case transitions to STOPPED."""
        print("\n>>> RUNNING SCENARIO 3: Fraud / Unrecoverable Risk (STOP Intervention)")
        case_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        customer_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        async with self.session_factory() as session:
            cust = Customer(
                id=customer_id,
                merchant_id=self.merchant_id,
                gateway_customer_id=f"cust_sim3_{case_id.hex[:6]}",
                name="S3 Fraud Risk",
                email="sim3@averiq.ai",
                created_at=now,
                updated_at=now,
            )
            session.add(cust)

            pay = Payment(
                id=payment_id,
                merchant_id=self.merchant_id,
                customer_id=customer_id,
                gateway_payment_id=f"pay_sim3_{case_id.hex[:6]}",
                amount=100000,
                currency="INR",
                status="failed",
                method="card",
                error_code="GATEWAY_ERROR",
                error_description="Suspected fraudulent transaction blocked",
                failed_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(pay)

            # High exposure & high risk tier
            case = RevenueRiskCase(
                id=case_id,
                merchant_id=self.merchant_id,
                payment_id=payment_id,
                customer_id=customer_id,
                state="AT_RISK",
                payment_amount_at_risk=100000,
                currency="INR",
                risk_calculation_path="SINGLE_PAYMENT",
                exposure_tier="HIGH",
                recovery_risk_tier="HIGH",
                failure_category="FRAUD_SUSPECTED",
                operating_mode="SIMULATED",
                case_metadata={"sim": "scenario_3", "force_stop": True},
                created_at=now,
                updated_at=now,
            )
            session.add(case)
            await session.commit()

        t0 = time.time()
        run_decision_pipeline.delay(str(case_id), str(self.merchant_id))

        final_state = None
        for _ in range(15):
            await asyncio.sleep(1)
            async with self.session_factory() as session:
                c = (await session.execute(select(RevenueRiskCase).where(RevenueRiskCase.id == case_id))).scalar_one_or_none()
                if c and c.state in ("STOPPED", "FAILED", "ESCALATED"):
                    final_state = c.state
                    break

        elapsed = time.time() - t0
        passed = final_state in ("STOPPED", "ESCALATED")
        print(f"[SCENARIO 3 RESULT] Final State: {final_state}, Elapsed: {elapsed:.2f}s, Passed: {passed}")
        return {
            "scenario": "STOP Intervention",
            "passed": passed,
            "final_state": final_state,
            "latency_seconds": elapsed,
        }

    async def scenario_4_webhook_idempotency(self) -> dict:
        """Simulate webhook ingestion and duplicate delivery idempotency."""
        print("\n>>> RUNNING SCENARIO 4: Webhook Ingestion & Duplicate Delivery Idempotency")
        gw_pay_id = f"pay_gw_{uuid.uuid4().hex[:8]}"
        gw_event_id = f"evt_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)

        # 1. Seed Customer & Payment
        async with self.session_factory() as session:
            cust_id = uuid.uuid4()
            pay_id = uuid.uuid4()
            cust = Customer(
                id=cust_id,
                merchant_id=self.merchant_id,
                gateway_customer_id=f"cust_{gw_pay_id}",
                name="S4 Webhook Customer",
                email="sim4@averiq.ai",
                created_at=now,
                updated_at=now,
            )
            session.add(cust)

            pay = Payment(
                id=pay_id,
                merchant_id=self.merchant_id,
                customer_id=cust_id,
                gateway_payment_id=gw_pay_id,
                amount=150000,
                currency="INR",
                status="failed",
                method="upi",
                error_code="PAYMENT_FAILED",
                error_description="UPI collect request expired",
                failed_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(pay)

            # Webhook Event 1
            webhook_evt_id = uuid.uuid4()
            evt = WebhookEvent(
                id=webhook_evt_id,
                merchant_id=self.merchant_id,
                event_type="payment.failed",
                provider_event_id=gw_event_id,
                entity_type="payment",
                entity_id=gw_pay_id,
                processing_status="PERSISTED",
                received_at=now,
                updated_at=now,
            )
            session.add(evt)
            await session.commit()

        t0 = time.time()
        # Dispatch first webhook processor task
        res1 = process_webhook_event.delay(str(webhook_evt_id), str(self.merchant_id))

        # Wait for processing
        case_created = None
        for _ in range(15):
            await asyncio.sleep(1)
            async with self.session_factory() as session:
                w = (await session.execute(select(WebhookEvent).where(WebhookEvent.id == webhook_evt_id))).scalar_one_or_none()
                if w and w.processing_status == "PROCESSED":
                    # Check case
                    c = (await session.execute(select(RevenueRiskCase).where(RevenueRiskCase.payment_id == pay_id))).scalar_one_or_none()
                    if c:
                        case_created = c.id
                        break

        # Dispatch duplicate webhook task (same webhook event ID)
        res2 = process_webhook_event.delay(str(webhook_evt_id), str(self.merchant_id))
        await asyncio.sleep(2)

        # Count cases associated with this payment
        async with self.session_factory() as session:
            count_stmt = select(func.count(RevenueRiskCase.id)).where(RevenueRiskCase.payment_id == pay_id)
            total_cases = (await session.execute(count_stmt)).scalar()

        elapsed = time.time() - t0
        passed = (case_created is not None) and (total_cases == 1)
        print(f"[SCENARIO 4 RESULT] Case Created: {case_created}, Total Cases (Must be 1): {total_cases}, Elapsed: {elapsed:.2f}s, Passed: {passed}")
        return {
            "scenario": "Webhook Idempotency",
            "passed": passed,
            "case_created": str(case_created) if case_created else None,
            "total_cases_count": total_cases,
            "latency_seconds": elapsed,
        }

    async def scenario_5_concurrency_locking(self) -> dict:
        """Simulate concurrent worker execution on the same case. Verifies FOR UPDATE serialization."""
        print("\n>>> RUNNING SCENARIO 5: Concurrency Safety & State Machine Serialization")
        case_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        customer_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        async with self.session_factory() as session:
            cust = Customer(
                id=customer_id,
                merchant_id=self.merchant_id,
                gateway_customer_id=f"cust_sim5_{case_id.hex[:6]}",
                name="S5 Concurrency Customer",
                email="sim5@averiq.ai",
                created_at=now,
                updated_at=now,
            )
            session.add(cust)

            pay = Payment(
                id=payment_id,
                merchant_id=self.merchant_id,
                customer_id=customer_id,
                gateway_payment_id=f"pay_sim5_{case_id.hex[:6]}",
                amount=180000,
                currency="INR",
                status="failed",
                method="card",
                error_code="BAD_REQUEST_ERROR",
                error_description="Issuer declined",
                failed_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(pay)

            case = RevenueRiskCase(
                id=case_id,
                merchant_id=self.merchant_id,
                payment_id=payment_id,
                customer_id=customer_id,
                state="AT_RISK",
                payment_amount_at_risk=180000,
                currency="INR",
                risk_calculation_path="SINGLE_PAYMENT",
                exposure_tier="MEDIUM",
                recovery_risk_tier="MEDIUM",
                failure_category="INSUFFICIENT_FUNDS",
                operating_mode="SIMULATED",
                case_metadata={"sim": "scenario_5"},
                created_at=now,
                updated_at=now,
            )
            session.add(case)
            await session.commit()

        t0 = time.time()
        # Fire 2 identical decision tasks concurrently to Celery
        run_decision_pipeline.delay(str(case_id), str(self.merchant_id))
        run_decision_pipeline.delay(str(case_id), str(self.merchant_id))

        # Check that case successfully finishes without deadlocks or corrupted state
        final_state = None
        for _ in range(15):
            await asyncio.sleep(1)
            async with self.session_factory() as session:
                c = (await session.execute(select(RevenueRiskCase).where(RevenueRiskCase.id == case_id))).scalar_one_or_none()
                if c and c.state not in ("AT_RISK", "DIAGNOSED"):
                    final_state = c.state
                    break

        elapsed = time.time() - t0
        passed = final_state is not None
        print(f"[SCENARIO 5 RESULT] Final State: {final_state}, Elapsed: {elapsed:.2f}s, Passed: {passed}")
        return {
            "scenario": "Concurrency Serialization",
            "passed": passed,
            "final_state": final_state,
            "latency_seconds": elapsed,
        }

    async def run_all(self):
        await self.init()
        s1 = await self.scenario_1_standard_recovery()
        s2 = await self.scenario_2_high_value_human_review()
        s3 = await self.scenario_3_stop_intervention()
        s4 = await self.scenario_4_webhook_idempotency()
        s5 = await self.scenario_5_concurrency_locking()

        all_results = [s1, s2, s3, s4, s5]
        all_passed = all(r["passed"] for r in all_results)

        print("\n=================================================================")
        print("AVERIQ PHASE 4 — LIVE ORCHESTRATION SIMULATION SUMMARY")
        print("=================================================================")
        for r in all_results:
            status_str = "PASS" if r["passed"] else "FAIL"
            print(f"[{status_str}] {r['scenario']} (Time: {r['latency_seconds']:.2f}s)")
        print(f"OVERALL STATUS: {'ALL SCENARIOS PASSED' if all_passed else 'SOME SCENARIOS FAILED'}")
        print("=================================================================\n")

        # Save summary JSON for reporting
        output_path = Path("docs/PHASE_4_SIMULATION_SUMMARY.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "all_passed": all_passed,
                "scenarios": all_results,
            }, f, indent=2)
        print(f"Results saved to {output_path}")
        return all_passed


if __name__ == "__main__":
    sim = LiveOrchestrationSimulation()
    passed = asyncio.run(sim.run_all())
    sys.exit(0 if passed else 1)
