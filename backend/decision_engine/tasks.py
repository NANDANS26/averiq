"""Decision engine Celery tasks.

Orchestrates the decision pipeline:
  AT_RISK -> DIAGNOSED -> RECOVERY_ELIGIBLE -> ACTION_PENDING -> ACTION_APPROVED / STOPPED

Task name: backend.decision_engine.tasks.run_decision_pipeline
Queue: decisions
"""
from __future__ import annotations

import asyncio
import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select

from ai.providers.stub import StubAIProvider
from backend.config import settings
from backend.database import async_session_factory
from backend.decision_engine.fallback_classifier import DeterministicFallbackClassifier
from backend.decision_engine.intervention_engine import InterventionEngine
from backend.decision_engine.ranking import rank_candidates
from backend.decision_engine.risk_assessor import RevenueRiskAssessor
from backend.decision_engine.root_cause_reasoner import RootCauseReasoner
from backend.domain.models import Customer, HumanReview, Intervention, Payment, Policy, RevenueRiskCase, Subscription
from backend.policy_engine.engine import PolicyEngine
from backend.state_machine.machine import StateMachine
from backend.worker import celery_app
from shared.contracts.enums import (
    ExecutionCapability,
    FailureCategory,
    InterventionType,
    PolicyDecisionOutcome,
)
from shared.contracts.schemas import CaseContext, PolicyConfig, RecoverabilityFeatures

logger = structlog.get_logger(__name__)


def _load_calibrated_lgbm_class():
    try:
        from ai.models.calibrated_lgbm import CalibratedLightGBMModel
        return CalibratedLightGBMModel
    except Exception:
        return None


def _load_action_value_estimator():
    try:
        from ai.models.action_value_model import ActionValueEstimator
        return ActionValueEstimator
    except Exception:
        return None


def _load_lightgbm_class():
    try:
        spec = importlib.util.spec_from_file_location("lightgbm_model", Path("ai/models/lightgbm_model.py"))
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, "LightGBMModel", None)
    except Exception:
        return None
    return None


@celery_app.task(
    name="backend.decision_engine.tasks.run_decision_pipeline",
    bind=True,
    queue="decisions",
    acks_late=True,
)
def run_decision_pipeline(
    self: object,
    case_id: str,
    merchant_id: str,
) -> dict[str, Any]:
    """Celery task entrypoint for driving the full decision pipeline."""
    return asyncio.run(_run_pipeline_async(case_id, merchant_id))


async def _run_pipeline_async(case_id_str: str, merchant_id_str: str) -> dict[str, Any]:
    case_id = uuid.UUID(case_id_str)
    merchant_id = uuid.UUID(merchant_id_str)
    correlation_id = str(uuid.uuid4())

    log = logger.bind(
        case_id=str(case_id),
        merchant_id=str(merchant_id),
        correlation_id=correlation_id,
    )
    log.info("decision_pipeline_started")

    state_machine = StateMachine()

    async with async_session_factory() as session:
        async with session.begin():
            # 1. Load case with lock
            case_stmt = (
                select(RevenueRiskCase)
                .where(
                    RevenueRiskCase.id == case_id,
                    RevenueRiskCase.merchant_id == merchant_id,
                )
                .with_for_update()
            )
            case_res = await session.execute(case_stmt)
            case = case_res.scalar_one_or_none()
            if not case:
                log.error("decision_pipeline_case_not_found")
                return {"status": "case_not_found"}

            if case.state != "AT_RISK":
                log.info("decision_pipeline_skipping_state", state=case.state)
                return {"status": "skipped", "state": case.state}

            # 2. Load payment, customer, subscription, policy
            payment_res = await session.execute(
                select(Payment).where(Payment.id == case.payment_id)
            )
            payment = payment_res.scalar_one_or_none()

            customer: Customer | None = None
            if case.customer_id:
                cust_res = await session.execute(
                    select(Customer).where(Customer.id == case.customer_id)
                )
                customer = cust_res.scalar_one_or_none()

            subscription: Subscription | None = None
            if case.subscription_id:
                sub_res = await session.execute(
                    select(Subscription).where(Subscription.id == case.subscription_id)
                )
                subscription = sub_res.scalar_one_or_none()

            policy_res = await session.execute(
                select(Policy)
                .where(
                    Policy.merchant_id == merchant_id,
                    Policy.lifecycle_state == "active",
                )
                .limit(1)
            )
            active_policy_row = policy_res.scalar_one_or_none()

            # Record decision timestamp
            now = datetime.now(timezone.utc)
            case.decision_timestamp = now

            # Build PolicyConfig
            if active_policy_row and active_policy_row.configuration:
                cfg = active_policy_row.configuration
                policy_cfg = PolicyConfig(
                    policy_id=str(active_policy_row.id),
                    merchant_id=str(merchant_id),
                    version=str(active_policy_row.version),
                    min_confidence=cfg.get("min_confidence", 0.5),
                    cooldown_seconds=cfg.get("cooldown_seconds", 300),
                    max_retries=cfg.get("max_retries", {"PAYMENT_LINK": 3, "RETRY": 3}),
                    max_amount=cfg.get("max_amount", {"INR": 10000000}),
                    human_approval_threshold=cfg.get("human_approval_threshold", 5000000),
                    attribution_window_seconds=cfg.get("attribution_window_seconds", 86400),
                    exposure_thresholds=cfg.get("exposure_thresholds", {"HIGH": 100000, "MEDIUM": 20000}),
                )
                policy_version_id = active_policy_row.id
            else:
                # Default safety fallback
                policy_cfg = PolicyConfig(
                    policy_id=str(uuid.uuid4()),
                    merchant_id=str(merchant_id),
                    version="default-v1",
                    min_confidence=0.5,
                    cooldown_seconds=300,
                    max_retries={"PAYMENT_LINK": 3, "RETRY": 3},
                    max_amount={"INR": 10000000},
                    human_approval_threshold=5000000,
                    attribution_window_seconds=86400,
                    exposure_thresholds={"HIGH": 100000, "MEDIUM": 20000},
                )
                # Create default policy row if needed
                default_policy = Policy(
                    merchant_id=merchant_id,
                    version=1,
                    lifecycle_state="active",
                    configuration=policy_cfg.model_dump(),
                    created_at=now,
                )
                session.add(default_policy)
                await session.flush()
                policy_version_id = default_policy.id

            # Step 1: Transition to DIAGNOSED and run Root Cause Reasoner
            await state_machine.transition(
                session,
                case_id=case.id,
                to_state="DIAGNOSED",
                actor="DECISION_ENGINE",
                reason="Starting root-cause diagnosis",
                correlation_id=correlation_id,
            )

            error_code = payment.error_code if payment else None
            error_description = payment.error_description if payment else None

            case_context = CaseContext(
                case_id=str(case.id),
                merchant_id=str(merchant_id),
                payment_id=str(payment.id) if payment else "pay_unknown",
                amount_paise=int(case.payment_amount_at_risk),
                currency=case.currency,
                error_code=error_code,
                previous_failure_count=0,
                created_at=now,
            )

            ai_provider = StubAIProvider()
            reasoner = RootCauseReasoner(ai_provider=ai_provider)
            diagnosis_res = await reasoner.diagnose(case_context)

            case.failure_category = diagnosis_res.diagnosis.failure_category.value
            case.diagnosis_confidence = diagnosis_res.diagnosis.confidence_score
            case.ai_source = diagnosis_res.ai_provider_id
            if case.case_metadata is None:
                case.case_metadata = {}
            case.case_metadata.update(diagnosis_res.metadata_updates)

            # Step 2: Transition to RECOVERY_ELIGIBLE, run ML & Risk Assessor
            await state_machine.transition(
                session,
                case_id=case.id,
                to_state="RECOVERY_ELIGIBLE",
                actor="DECISION_ENGINE",
                reason="Diagnosis complete; assessing risk and predict recoverability",
                correlation_id=correlation_id,
            )

            # Load ML model or fallback
            v2_artifact_path = Path("model_artifacts/lgbm_v2_calibrated.pkl")
            legacy_artifact_path = Path("model_artifacts/lgbm_42_500.pkl")
            action_value_artifact_path = Path("model_artifacts/action_value_v3.pkl")

            # Fallback is explicitly calibrated to empirical population base rate (0.35)
            # and tagged as 'fallback-heuristic' to prevent confusion with genuine model inference
            rec_prob: float = 0.35
            model_version = "fallback-heuristic"
            feature_version = "v1.0"

            CalibratedLGBMClass = _load_calibrated_lgbm_class()
            ActionValueClass = _load_action_value_estimator()
            LightGBMClass = _load_lightgbm_class()

            action_probs: dict[str, float] | None = None
            if ActionValueClass and action_value_artifact_path.exists():
                try:
                    action_estimator = ActionValueClass.load(action_value_artifact_path)
                    try:
                        failure_cat_enum = FailureCategory(case.failure_category) if case.failure_category else FailureCategory.UNKNOWN
                    except ValueError:
                        failure_cat_enum = FailureCategory.UNKNOWN

                    features = RecoverabilityFeatures(
                        amount_paise=int(case.payment_amount_at_risk),
                        currency=case.currency,
                        failure_category=failure_cat_enum,
                        payment_method=payment.method if payment and hasattr(payment, "method") else "card",
                        time_since_failure_seconds=0.0,
                        hour_of_day=now.hour,
                        day_of_week=now.weekday(),
                        previous_failure_count=0,
                        has_subscription=bool(subscription),
                        subscription_billing_amount_paise=int(subscription.billing_amount) if (subscription and hasattr(subscription, "billing_amount") and subscription.billing_amount) else None,
                        feature_version="v1.0",
                    )
                    action_probs = action_estimator.predict_action_probabilities(features)
                    rec_prob = action_probs.get("NO_INTERVENTION", 0.35)
                    model_version = action_estimator.model_version
                    log.info("action_value_inference_success", model_version=model_version, probs=action_probs)
                except Exception as ex:
                    log.warning("action_value_inference_fallback", error=str(ex))

            if action_probs is None:
                if CalibratedLGBMClass and v2_artifact_path.exists():
                    try:
                        cal_model = CalibratedLGBMClass.load(v2_artifact_path)
                        model_version = cal_model.model_version
                        try:
                            failure_cat_enum = FailureCategory(case.failure_category) if case.failure_category else FailureCategory.UNKNOWN
                        except ValueError:
                            failure_cat_enum = FailureCategory.UNKNOWN

                        features = RecoverabilityFeatures(
                            amount_paise=int(case.payment_amount_at_risk),
                            currency=case.currency,
                            failure_category=failure_cat_enum,
                            payment_method=payment.method if payment and hasattr(payment, "method") else "card",
                            time_since_failure_seconds=0.0,
                            hour_of_day=now.hour,
                            day_of_week=now.weekday(),
                            previous_failure_count=0,
                            has_subscription=bool(subscription),
                            subscription_billing_amount_paise=int(subscription.billing_amount) if (subscription and hasattr(subscription, "billing_amount") and subscription.billing_amount) else None,
                            feature_version="v1.0",
                        )
                        rec_prob = float(cal_model.predict(features))
                        log.info("calibrated_ml_inference_success", model_version=model_version, rec_prob=rec_prob)
                    except Exception as ex:
                        log.warning("calibrated_ml_inference_fallback", error=str(ex))
                        rec_prob = 0.35
                        model_version = "fallback-heuristic"
                elif LightGBMClass and legacy_artifact_path.exists():
                    try:
                        lgbm = LightGBMClass.load(legacy_artifact_path)
                        model_version = lgbm.model_version
                        try:
                            failure_cat_enum = FailureCategory(case.failure_category) if case.failure_category else FailureCategory.UNKNOWN
                        except ValueError:
                            failure_cat_enum = FailureCategory.UNKNOWN

                        features = RecoverabilityFeatures(
                            amount_paise=int(case.payment_amount_at_risk),
                            currency=case.currency,
                            failure_category=failure_cat_enum,
                            payment_method="card",
                            time_since_failure_seconds=0.0,
                            hour_of_day=now.hour,
                            day_of_week=now.weekday(),
                            previous_failure_count=0,
                            has_subscription=bool(subscription),
                            subscription_billing_amount_paise=None,
                            feature_version="v1.0",
                        )
                        rec_prob = float(lgbm.predict(features))
                    except Exception as ex:
                        log.warning("ml_inference_fallback", error=str(ex))
                        rec_prob = 0.35
                        model_version = "fallback-heuristic"

            case.recovery_probability = rec_prob

            risk_assessor = RevenueRiskAssessor()
            sub_amount = subscription.billing_amount if subscription else None
            sub_status = subscription.status if subscription else None
            try:
                failure_cat = FailureCategory(case.failure_category) if case.failure_category else None
            except ValueError:
                failure_cat = FailureCategory.UNKNOWN

            risk_res = risk_assessor.assess(
                payment_amount_paise=int(case.payment_amount_at_risk),
                currency=case.currency,
                subscription_billing_amount_paise=sub_amount,
                subscription_status=sub_status,
                policy_config=policy_cfg,
                recovery_probability=rec_prob,
                failure_category=failure_cat,
            )

            case.exposure_tier = risk_res.exposure_tier.value
            case.recovery_risk_tier = risk_res.recovery_risk_tier.value
            case.risk_calculation_path = risk_res.risk_calculation_path
            case.projected_subscription_revenue_at_risk = risk_res.projected_subscription_revenue_at_risk

            # Step 3: Transition to ACTION_PENDING, generate interventions with ContextAwareRanker
            await state_machine.transition(
                session,
                case_id=case.id,
                to_state="ACTION_PENDING",
                actor="DECISION_ENGINE",
                reason="Risk assessment complete; generating candidates",
                correlation_id=correlation_id,
            )

            from backend.decision_engine.context_aware_ranker import ContextAwareInterventionRanker

            ranker = ContextAwareInterventionRanker()
            ranked_candidates, decision_trace = ranker.generate_and_rank_candidates(
                context=case_context,
                policy_config=policy_cfg,
                operating_mode=case.operating_mode,
                payment_link_count=0,
                model_version=model_version,
                action_probabilities=action_probs,
                failure_category=failure_cat,
            )

            # Persist decision trace in case metadata
            if case.case_metadata is None:
                case.case_metadata = {}
            case.case_metadata["decision_trace"] = decision_trace

            policy_engine = PolicyEngine()
            chosen_candidate = None
            chosen_decision = None
            chosen_intervention = None

            for c in ranked_candidates:
                p_decision = policy_engine.evaluate(
                    candidate=c,
                    policy=policy_cfg,
                    payment_amount_at_risk=int(case.payment_amount_at_risk),
                    currency=case.currency,
                    failure_category=failure_cat,
                    seconds_since_last_intervention=3600.0,
                    intervention_count=0,
                    case_state="ACTION_PENDING",
                    is_recovered=False,
                )

                # Map check constraints
                type_str = "ESCALATE" if c.intervention_type == InterventionType.ESCALATE_TO_HUMAN else c.intervention_type.value
                exec_cap_str = "EXECUTABLE" if c.execution_capability == ExecutionCapability.CREATE_PAYMENT_LINK else "INTERNAL_ACTION"

                prov_status = getattr(c, "provider_support_status", None)
                prov_status_str = prov_status.value if prov_status is not None and hasattr(prov_status, "value") else (str(prov_status) if prov_status is not None else "VERIFIED")

                intv_row = Intervention(
                    case_id=case.id,
                    merchant_id=merchant_id,
                    policy_version_id=policy_version_id,
                    model_version=model_version,
                    feature_version=feature_version,
                    intervention_type=type_str,
                    execution_capability=exec_cap_str,
                    provider_support_status=prov_status_str,
                    requires_human_approval=(p_decision.outcome == PolicyDecisionOutcome.HUMAN_REVIEW),
                    expected_recovery_value=c.expected_recovery_value,
                    policy_decision=p_decision.outcome.value,
                    policy_blocking_rules={"rules": p_decision.blocking_rules} if p_decision.blocking_rules else None,
                    policy_evaluated_values=p_decision.evaluated_values or None,
                    execution_status="PENDING",
                    created_at=now,
                    updated_at=now,
                )
                session.add(intv_row)
                await session.flush()

                if chosen_candidate is None and p_decision.outcome in (PolicyDecisionOutcome.APPROVED, PolicyDecisionOutcome.HUMAN_REVIEW):
                    chosen_candidate = c
                    chosen_decision = p_decision
                    chosen_intervention = intv_row

            # If no candidate was approved/human review, fallback to STOP candidate
            if chosen_candidate is None:
                for c, intv_row in [(c, i) for c in ranked_candidates]: # fallback
                    pass
                await state_machine.transition(
                    session,
                    case_id=case.id,
                    to_state="STOPPED",
                    actor="POLICY_ENGINE",
                    reason="No candidate intervention was approved by policy",
                    correlation_id=correlation_id,
                )
                return {"status": "stopped", "case_id": str(case.id)}

            # If the chosen compliant intervention is STOP, transition to STOPPED
            if chosen_candidate.intervention_type == InterventionType.STOP:
                await state_machine.transition(
                    session,
                    case_id=case.id,
                    to_state="STOPPED",
                    actor="POLICY_ENGINE",
                    reason="STOP intervention selected as optimal policy-compliant action",
                    correlation_id=correlation_id,
                )
                return {"status": "stopped", "case_id": str(case.id)}

            if chosen_decision.outcome == PolicyDecisionOutcome.HUMAN_REVIEW:
                hr = HumanReview(
                    merchant_id=merchant_id,
                    case_id=case.id,
                    intervention_id=chosen_intervention.id,
                    status="OPEN",
                    created_at=now,
                    updated_at=now,
                )
                session.add(hr)
                log.info("human_review_created", intervention_id=str(chosen_intervention.id))
                return {"status": "human_review", "case_id": str(case.id)}

            if chosen_decision.outcome == PolicyDecisionOutcome.APPROVED:
                await state_machine.transition(
                    session,
                    case_id=case.id,
                    to_state="ACTION_APPROVED",
                    actor="POLICY_ENGINE",
                    reason="Top candidate approved by policy rules",
                    correlation_id=correlation_id,
                )

                # Dispatch execution task
                celery_app.send_task(
                    "backend.execution_engine.tasks.execute_intervention",
                    args=[str(case.id), str(chosen_intervention.id), str(merchant_id)],
                    queue="default",
                    headers={"correlation_id": correlation_id},
                )
                return {"status": "action_approved", "case_id": str(case.id)}

    return {"status": "completed", "case_id": case_id_str}