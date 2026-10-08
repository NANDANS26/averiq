"""Deterministic Demo Fixtures for Averiq (Workstream M).

Scenarios:
- DEMO-001: Normal payment failure -> successful recovery (Golden path)
- DEMO-002: High-value payment -> policy human approval required
- DEMO-003: Policy violation / fraud / stop condition -> STOPPED
- DEMO-004: Gateway execution failure -> FAILED
- DEMO-005: Verification retry exhaustion -> ESCALATED
- DEMO-006: Duplicate webhook -> deduplicated / idempotent
- DEMO-007: Duplicate execution -> execution lock prevents race
- DEMO-008: Model unavailable -> fallback probability 0.35

All fixtures flag cases with `is_demo_fixture = True`.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import settings
from backend.domain.models import (
    AuditEvent,
    Customer,
    ExternalOperation,
    HumanReview,
    Intervention,
    Merchant,
    Payment,
    Policy,
    RevenueRiskCase,
    VerificationAttempt,
    WebhookEvent,
)
from backend.state_machine import StateMachine

state_machine = StateMachine()


class DemoFixtureManager:
    """Creates and manages deterministic demo scenarios."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        self.merchant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    async def ensure_merchant_and_policy(self) -> uuid.UUID:
        async with self.session_factory() as session:
            async with session.begin():
                m = (await session.execute(select(Merchant).where(Merchant.id == self.merchant_id))).scalar_one_or_none()
                now = datetime.now(timezone.utc)
                if not m:
                    m = Merchant(
                        id=self.merchant_id,
                        name="Averiq Demo Merchant",
                        webhook_secret="demo_secret_encrypted",
                        currency="INR",
                        operating_mode="SIMULATED",
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(m)

                p = (await session.execute(
                    select(Policy).where(Policy.merchant_id == self.merchant_id, Policy.lifecycle_state == "active").limit(1)
                )).scalar_one_or_none()
                if not p:
                    p = Policy(
                        id=uuid.uuid4(),
                        merchant_id=self.merchant_id,
                        version=1,
                        lifecycle_state="active",
                        configuration={
                            "confidence_threshold": 0.70,
                            "cooldown_seconds": 3600,
                            "human_approval_threshold_minor_units": 1000000,
                            "max_amount_paise": 50000000,
                            "max_retries": 3,
                        },
                        created_at=now,
                    )
                    session.add(p)
                return p.id

    async def seed_demo_001_successful_recovery(self) -> str:
        """DEMO-001: Normal payment failure -> successful recovery."""
        policy_id = await self.ensure_merchant_and_policy()
        async with self.session_factory() as session:
            async with session.begin():
                now = datetime.now(timezone.utc)
                case_id = uuid.uuid4()
                pay_id = uuid.uuid4()
                cust_id = uuid.uuid4()

                cust = Customer(
                    id=cust_id, merchant_id=self.merchant_id,
                    gateway_customer_id=f"cust_demo001_{case_id.hex[:6]}",
                    name="Arun Kumar", email="arun@example.com",
                    created_at=now, updated_at=now,
                )
                session.add(cust)

                pay = Payment(
                    id=pay_id, merchant_id=self.merchant_id, customer_id=cust_id,
                    gateway_payment_id=f"pay_demo001_{case_id.hex[:6]}",
                    amount=250000, currency="INR", status="failed", method="card",
                    error_code="BAD_REQUEST_ERROR", error_description="Card declined due to insufficient funds",
                    failed_at=now, created_at=now, updated_at=now,
                )
                session.add(pay)

                case = RevenueRiskCase(
                    id=case_id, merchant_id=self.merchant_id, payment_id=pay_id, customer_id=cust_id,
                    state="RECOVERED", payment_amount_at_risk=250000, currency="INR",
                    risk_calculation_path="SINGLE_PAYMENT", exposure_tier="MEDIUM", recovery_risk_tier="LOW",
                    failure_category="INSUFFICIENT_FUNDS", diagnosis_confidence=0.92, ai_source="StubAIProvider",
                    recovery_probability=0.78, recovered_amount=250000, recovered_currency="INR",
                    is_demo_fixture=True, operating_mode="SIMULATED",
                    case_metadata={
                        "demo_scenario": "DEMO-001",
                        "scenario_title": "Normal payment failure -> successful recovery",
                        "decision_trace": {
                            "baseline_recovery_probability": 0.35,
                            "model_preferred_action": "PAYMENT_LINK",
                            "reason_codes": ["POSITIVE_INCREMENTAL_VALUE", "PAYMENT_LINK_MAX_VALUE"],
                            "candidate_evaluations": [
                                {
                                    "action": "PAYMENT_LINK",
                                    "probability": 0.78,
                                    "incremental_uplift": 0.43,
                                    "cost_paise": 500,
                                    "expected_net_value_paise": 107000,
                                }
                            ]
                        }
                    },
                    decision_timestamp=now, created_at=now, updated_at=now,
                )
                session.add(case)

                inv = Intervention(
                    id=uuid.uuid4(), case_id=case_id, merchant_id=self.merchant_id, policy_version_id=policy_id,
                    intervention_type="PAYMENT_LINK", execution_capability="EXECUTABLE", provider_support_status="VERIFIED",
                    expected_recovery_value=107000, policy_decision="APPROVED", execution_status="SUCCESS",
                    created_at=now, updated_at=now,
                )
                session.add(inv)

                op = ExternalOperation(
                    id=uuid.uuid4(), case_id=case_id, merchant_id=self.merchant_id, intervention_id=inv.id,
                    action_id=uuid.uuid4(), action_type="PAYMENT_LINK", provider="razorpay",
                    status="SUCCEEDED", external_operation_id=f"plink_demo_{case_id.hex[:6]}",
                    submitted_at=now, completed_at=now, created_at=now, updated_at=now,
                )
                session.add(op)

                ver = VerificationAttempt(
                    id=uuid.uuid4(), external_operation_id=op.id, attempt_number=1, outcome="RECOVERED",
                    provider="razorpay", gateway_reported_status="paid", verification_timestamp=now,
                )
                session.add(ver)

                audit_event = AuditEvent(
                    id=uuid.uuid4(), merchant_id=self.merchant_id, case_id=case_id,
                    actor="DECISION_ENGINE", action_type="INTERVENTION_EXECUTED",
                    affected_entity_type="case", affected_entity_id=case_id,
                    from_state="ACTION_EXECUTING", to_state="RECOVERED",
                    correlation_id=uuid.uuid4(), payload={}, created_at=now,
                )
                session.add(audit_event)
                return str(case_id)

    async def seed_demo_002_human_approval(self) -> str:
        """DEMO-002: High-value payment (> Rs 10,000) -> human approval required."""
        policy_id = await self.ensure_merchant_and_policy()
        async with self.session_factory() as session:
            async with session.begin():
                now = datetime.now(timezone.utc)
                case_id = uuid.uuid4()
                pay_id = uuid.uuid4()
                cust_id = uuid.uuid4()

                cust = Customer(
                    id=cust_id, merchant_id=self.merchant_id,
                    gateway_customer_id=f"cust_demo002_{case_id.hex[:6]}",
                    name="Priya Sharma", email="priya@enterprise.com",
                    created_at=now, updated_at=now,
                )
                session.add(cust)

                pay = Payment(
                    id=pay_id, merchant_id=self.merchant_id, customer_id=cust_id,
                    gateway_payment_id=f"pay_demo002_{case_id.hex[:6]}",
                    amount=1500000, currency="INR", status="failed", method="card",
                    error_code="BAD_REQUEST_ERROR", error_description="High-value subscription charge failed",
                    failed_at=now, created_at=now, updated_at=now,
                )
                session.add(pay)

                case = RevenueRiskCase(
                    id=case_id, merchant_id=self.merchant_id, payment_id=pay_id, customer_id=cust_id,
                    state="ACTION_PENDING", payment_amount_at_risk=1500000, currency="INR",
                    risk_calculation_path="SINGLE_PAYMENT", exposure_tier="HIGH", recovery_risk_tier="HIGH",
                    failure_category="INSUFFICIENT_FUNDS", diagnosis_confidence=0.88, ai_source="StubAIProvider",
                    recovery_probability=0.72, is_demo_fixture=True, operating_mode="SIMULATED",
                    case_metadata={
                        "demo_scenario": "DEMO-002",
                        "scenario_title": "High-value payment -> human approval required",
                        "requires_human_review": True,
                        "decision_trace": {
                            "baseline_recovery_probability": 0.35,
                            "model_preferred_action": "PAYMENT_LINK",
                            "reason_codes": ["POSITIVE_INCREMENTAL_VALUE", "HIGH_EXPOSURE_TIER"],
                            "candidate_evaluations": [
                                {
                                    "action": "PAYMENT_LINK",
                                    "probability": 0.72,
                                    "incremental_uplift": 0.37,
                                    "cost_paise": 500,
                                    "expected_net_value_paise": 554500,
                                }
                            ]
                        }
                    },
                    decision_timestamp=now, created_at=now, updated_at=now,
                )
                session.add(case)

                inv = Intervention(
                    id=uuid.uuid4(), case_id=case_id, merchant_id=self.merchant_id, policy_version_id=policy_id,
                    intervention_type="PAYMENT_LINK", execution_capability="EXECUTABLE", provider_support_status="VERIFIED",
                    requires_human_approval=True, expected_recovery_value=554500, policy_decision="HUMAN_REVIEW",
                    policy_blocking_rules={"rules": ["HIGH_VALUE_THRESHOLD_EXCEEDED"]},
                    execution_status="PENDING", created_at=now, updated_at=now,
                )
                session.add(inv)

                hr = HumanReview(
                    id=uuid.uuid4(), merchant_id=self.merchant_id, case_id=case_id, intervention_id=inv.id,
                    status="OPEN", created_at=now, updated_at=now,
                )
                session.add(hr)
                return str(case_id)

    async def seed_demo_003_policy_stopped(self) -> str:
        """DEMO-003: Policy violation / fraud / stop condition -> STOPPED."""
        policy_id = await self.ensure_merchant_and_policy()
        async with self.session_factory() as session:
            async with session.begin():
                now = datetime.now(timezone.utc)
                case_id = uuid.uuid4()
                pay_id = uuid.uuid4()

                pay = Payment(
                    id=pay_id, merchant_id=self.merchant_id,
                    gateway_payment_id=f"pay_demo003_{case_id.hex[:6]}",
                    amount=500000, currency="INR", status="failed", method="card",
                    error_code="FRAUD_SUSPECTED", error_description="Suspicious payment activity reported",
                    failed_at=now, created_at=now, updated_at=now,
                )
                session.add(pay)

                case = RevenueRiskCase(
                    id=case_id, merchant_id=self.merchant_id, payment_id=pay_id,
                    state="STOPPED", payment_amount_at_risk=500000, currency="INR",
                    risk_calculation_path="SINGLE_PAYMENT", exposure_tier="MEDIUM", recovery_risk_tier="HIGH",
                    failure_category="FRAUD_SUSPECTED", diagnosis_confidence=0.99, ai_source="StubAIProvider",
                    recovery_probability=0.05, is_demo_fixture=True, operating_mode="SIMULATED",
                    case_metadata={
                        "demo_scenario": "DEMO-003",
                        "scenario_title": "Policy violation -> STOPPED",
                        "decision_trace": {
                            "baseline_recovery_probability": 0.05,
                            "model_preferred_action": "STOP",
                            "reason_codes": ["NEGATIVE_VALUE_TERMINATION", "FRAUD_BLOCK"],
                            "candidate_evaluations": [
                                {
                                    "action": "STOP",
                                    "probability": 0.0,
                                    "incremental_uplift": 0.0,
                                    "cost_paise": 0,
                                    "expected_net_value_paise": 0,
                                }
                            ]
                        }
                    },
                    decision_timestamp=now, created_at=now, updated_at=now,
                )
                session.add(case)

                inv = Intervention(
                    id=uuid.uuid4(), case_id=case_id, merchant_id=self.merchant_id, policy_version_id=policy_id,
                    intervention_type="STOP", execution_capability="INTERNAL_ACTION", provider_support_status="VERIFIED",
                    expected_recovery_value=0, policy_decision="APPROVED", execution_status="SUCCESS",
                    created_at=now, updated_at=now,
                )
                session.add(inv)
                return str(case_id)

    async def seed_demo_004_execution_failure(self) -> str:
        """DEMO-004: Gateway execution failure -> FAILED."""
        policy_id = await self.ensure_merchant_and_policy()
        async with self.session_factory() as session:
            async with session.begin():
                now = datetime.now(timezone.utc)
                case_id = uuid.uuid4()
                pay_id = uuid.uuid4()

                pay = Payment(
                    id=pay_id, merchant_id=self.merchant_id,
                    gateway_payment_id=f"pay_demo004_{case_id.hex[:6]}",
                    amount=100000, currency="INR", status="failed", method="card",
                    error_code="GATEWAY_ERROR", error_description="Payment link generation failed at gateway",
                    failed_at=now, created_at=now, updated_at=now,
                )
                session.add(pay)

                case = RevenueRiskCase(
                    id=case_id, merchant_id=self.merchant_id, payment_id=pay_id,
                    state="FAILED", payment_amount_at_risk=100000, currency="INR",
                    risk_calculation_path="SINGLE_PAYMENT", exposure_tier="LOW", recovery_risk_tier="MEDIUM",
                    failure_category="TECHNICAL_FAILURE", diagnosis_confidence=0.85, ai_source="StubAIProvider",
                    recovery_probability=0.45, is_demo_fixture=True, operating_mode="SIMULATED",
                    case_metadata={
                        "demo_scenario": "DEMO-004",
                        "scenario_title": "Gateway execution failure -> FAILED",
                    },
                    decision_timestamp=now, created_at=now, updated_at=now,
                )
                session.add(case)

                inv = Intervention(
                    id=uuid.uuid4(), case_id=case_id, merchant_id=self.merchant_id, policy_version_id=policy_id,
                    intervention_type="PAYMENT_LINK", execution_capability="EXECUTABLE", provider_support_status="VERIFIED",
                    expected_recovery_value=4500, policy_decision="APPROVED", execution_status="FAILED",
                    created_at=now, updated_at=now,
                )
                session.add(inv)

                op = ExternalOperation(
                    id=uuid.uuid4(), case_id=case_id, merchant_id=self.merchant_id, intervention_id=inv.id,
                    action_id=uuid.uuid4(), action_type="PAYMENT_LINK", provider="razorpay",
                    status="FAILED",
                    submitted_at=now, completed_at=now, created_at=now, updated_at=now,
                )
                session.add(op)
                return str(case_id)

    async def seed_demo_005_retry_exhaustion_escalated(self) -> str:
        """DEMO-005: Verification retry exhaustion -> ESCALATED."""
        policy_id = await self.ensure_merchant_and_policy()
        async with self.session_factory() as session:
            async with session.begin():
                now = datetime.now(timezone.utc)
                case_id = uuid.uuid4()
                pay_id = uuid.uuid4()

                pay = Payment(
                    id=pay_id, merchant_id=self.merchant_id,
                    gateway_payment_id=f"pay_demo005_{case_id.hex[:6]}",
                    amount=350000, currency="INR", status="failed", method="card",
                    error_code="NETWORK_TIMEOUT", error_description="Verification expired without customer response",
                    failed_at=now, created_at=now, updated_at=now,
                )
                session.add(pay)

                case = RevenueRiskCase(
                    id=case_id, merchant_id=self.merchant_id, payment_id=pay_id,
                    state="ESCALATED", payment_amount_at_risk=350000, currency="INR",
                    risk_calculation_path="SINGLE_PAYMENT", exposure_tier="MEDIUM", recovery_risk_tier="HIGH",
                    failure_category="NETWORK_TIMEOUT", diagnosis_confidence=0.75, ai_source="StubAIProvider",
                    recovery_probability=0.50, is_demo_fixture=True, operating_mode="SIMULATED",
                    case_metadata={
                        "demo_scenario": "DEMO-005",
                        "scenario_title": "Verification retry exhaustion -> ESCALATED",
                    },
                    decision_timestamp=now, created_at=now, updated_at=now,
                )
                session.add(case)

                inv = Intervention(
                    id=uuid.uuid4(), case_id=case_id, merchant_id=self.merchant_id, policy_version_id=policy_id,
                    intervention_type="PAYMENT_LINK", execution_capability="EXECUTABLE", provider_support_status="VERIFIED",
                    expected_recovery_value=52000, policy_decision="APPROVED", execution_status="SUCCESS",
                    created_at=now, updated_at=now,
                )
                session.add(inv)

                op = ExternalOperation(
                    id=uuid.uuid4(), case_id=case_id, merchant_id=self.merchant_id, intervention_id=inv.id,
                    action_id=uuid.uuid4(), action_type="PAYMENT_LINK", provider="razorpay",
                    status="SUCCEEDED", external_operation_id=f"plink_demo_{case_id.hex[:6]}",
                    submitted_at=now, completed_at=now, created_at=now, updated_at=now,
                )
                session.add(op)

                # 3 failed verification attempts
                for att in range(1, 4):
                    ver = VerificationAttempt(
                        id=uuid.uuid4(), external_operation_id=op.id, attempt_number=att, outcome="STILL_FAILED",
                        provider="razorpay", gateway_reported_status="expired", verification_timestamp=now,
                    )
                    session.add(ver)
                return str(case_id)

    async def seed_demo_006_duplicate_webhook(self) -> str:
        """DEMO-006: Duplicate webhook -> deduplicated / idempotent."""
        async with self.session_factory() as session:
            async with session.begin():
                now = datetime.now(timezone.utc)
                event_id = f"evt_demo006_{uuid.uuid4().hex[:8]}"
                we = WebhookEvent(
                    id=uuid.uuid4(), merchant_id=self.merchant_id,
                    provider_event_id=event_id, event_type="payment.failed",
                    processing_status="PROCESSED", received_at=now, updated_at=now,
                )
                session.add(we)
                return event_id

    async def seed_demo_007_duplicate_execution_blocked(self) -> str:
        """DEMO-007: Duplicate execution -> execution lock prevents race."""
        policy_id = await self.ensure_merchant_and_policy()
        async with self.session_factory() as session:
            async with session.begin():
                now = datetime.now(timezone.utc)
                case_id = uuid.uuid4()
                pay_id = uuid.uuid4()

                pay = Payment(
                    id=pay_id, merchant_id=self.merchant_id,
                    gateway_payment_id=f"pay_demo007_{case_id.hex[:6]}",
                    amount=120000, currency="INR", status="failed", method="card",
                    error_code="BAD_REQUEST_ERROR", error_description="Duplicate race prevention test",
                    failed_at=now, created_at=now, updated_at=now,
                )
                session.add(pay)

                case = RevenueRiskCase(
                    id=case_id, merchant_id=self.merchant_id, payment_id=pay_id,
                    state="ACTION_EXECUTING", payment_amount_at_risk=120000, currency="INR",
                    risk_calculation_path="SINGLE_PAYMENT", exposure_tier="LOW", recovery_risk_tier="LOW",
                    failure_category="INSUFFICIENT_FUNDS", diagnosis_confidence=0.90, ai_source="StubAIProvider",
                    recovery_probability=0.60, is_demo_fixture=True, operating_mode="SIMULATED",
                    case_metadata={"demo_scenario": "DEMO-007", "scenario_title": "Duplicate execution blocked"},
                    decision_timestamp=now, created_at=now, updated_at=now,
                )
                session.add(case)

                inv = Intervention(
                    id=uuid.uuid4(), case_id=case_id, merchant_id=self.merchant_id, policy_version_id=policy_id,
                    intervention_type="PAYMENT_LINK", execution_capability="EXECUTABLE", provider_support_status="VERIFIED",
                    expected_recovery_value=25000, policy_decision="APPROVED", execution_status="EXECUTING",
                    created_at=now, updated_at=now,
                )
                session.add(inv)
                return str(case_id)

    async def seed_demo_008_fallback_probability(self) -> str:
        """DEMO-008: Model unavailable -> fallback probability 0.35."""
        policy_id = await self.ensure_merchant_and_policy()
        async with self.session_factory() as session:
            async with session.begin():
                now = datetime.now(timezone.utc)
                case_id = uuid.uuid4()
                pay_id = uuid.uuid4()

                pay = Payment(
                    id=pay_id, merchant_id=self.merchant_id,
                    gateway_payment_id=f"pay_demo008_{case_id.hex[:6]}",
                    amount=80000, currency="INR", status="failed", method="card",
                    error_code="UNKNOWN_ERROR", error_description="Unclassified payment failure triggering fallback",
                    failed_at=now, created_at=now, updated_at=now,
                )
                session.add(pay)

                case = RevenueRiskCase(
                    id=case_id, merchant_id=self.merchant_id, payment_id=pay_id,
                    state="ACTION_PENDING", payment_amount_at_risk=80000, currency="INR",
                    risk_calculation_path="SINGLE_PAYMENT", exposure_tier="LOW", recovery_risk_tier="MEDIUM",
                    failure_category="UNKNOWN", diagnosis_confidence=0.50, ai_source="DeterministicFallback",
                    recovery_probability=0.35, is_demo_fixture=True, operating_mode="SIMULATED",
                    case_metadata={
                        "demo_scenario": "DEMO-008",
                        "scenario_title": "Model unavailable -> fallback 0.35",
                        "fallback_reason": "ML_MODEL_UNAVAILABLE",
                        "decision_trace": {
                            "baseline_recovery_probability": 0.35,
                            "model_preferred_action": "RETRY",
                            "reason_codes": ["FALLBACK_BASE_RATE"],
                            "candidate_evaluations": [
                                {
                                    "action": "RETRY",
                                    "probability": 0.35,
                                    "incremental_uplift": 0.0,
                                    "cost_paise": 100,
                                    "expected_net_value_paise": -100,
                                }
                            ]
                        }
                    },
                    decision_timestamp=now, created_at=now, updated_at=now,
                )
                session.add(case)
                return str(case_id)

    async def seed_all_scenarios(self) -> dict[str, str]:
        """Seed all 8 deterministic demo scenarios."""
        return {
            "DEMO-001": await self.seed_demo_001_successful_recovery(),
            "DEMO-002": await self.seed_demo_002_human_approval(),
            "DEMO-003": await self.seed_demo_003_policy_stopped(),
            "DEMO-004": await self.seed_demo_004_execution_failure(),
            "DEMO-005": await self.seed_demo_005_retry_exhaustion_escalated(),
            "DEMO-006": await self.seed_demo_006_duplicate_webhook(),
            "DEMO-007": await self.seed_demo_007_duplicate_execution_blocked(),
            "DEMO-008": await self.seed_demo_008_fallback_probability(),
        }
