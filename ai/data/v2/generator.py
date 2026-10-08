"""Dataset 2.0 Generator — High-fidelity synthetic payment failure simulator.

Features:
  1. Multilevel entity generator (Merchants, Customers, Payments).
  2. Realistic latent variables (customer payment intent, liquidity, issuer availability).
  3. Realistic error taxonomies correlated with payment methods and amount tiers.
  4. Potential outcomes framework:
       - Y(0): Natural organic recovery without intervention.
       - Y(PAYMENT_LINK): Outcome if payment link intervention is executed.
       - Y(RETRY): Outcome if intelligent retry is scheduled.
       - Y(CUSTOMER_ACTION): Outcome if customer prompt/auth action is requested.
  5. Policy-guided treatment assignment with randomized exploration (prevents confounding).
  6. Strict reproducibility (deterministic given RNG seed).
  7. Strict time ordering with explicit decision timestamps to avoid train/serve skew.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Final

from ai.data.v2.schema import SyntheticPaymentRecord

# ---------------------------------------------------------------------------
# Simulator Constants & Baselines
# ---------------------------------------------------------------------------
GENERATOR_VERSION: Final[str] = "2.0.0"
DATASET_VERSION: Final[str] = "averiq-recovery-v2"
BASE_EPOCH: Final[datetime] = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
HORIZON_DAYS: Final[int] = 365

FAILURE_CATEGORIES: Final[list[str]] = [
    "INSUFFICIENT_FUNDS",
    "CARD_DECLINED",
    "EXPIRED_CARD",
    "AUTHENTICATION_FAILURE",
    "TECHNICAL_FAILURE",
    "NETWORK_TIMEOUT",
    "FRAUD_SUSPECTED",
    "LIMIT_EXCEEDED",
    "UNKNOWN",
]

ERROR_CODE_MAP: Final[dict[str, list[str]]] = {
    "INSUFFICIENT_FUNDS": ["BAD_REQUEST_PAYMENT_ACCOUNT_INSUFFICIENT_BALANCE", "PAYMENT_BANK_ACCOUNT_LOW_FUNDS"],
    "CARD_DECLINED": ["BAD_REQUEST_PAYMENT_CARD_DECLINED", "ISSUER_DECLINED_TRANSACTION"],
    "EXPIRED_CARD": ["BAD_REQUEST_PAYMENT_CARD_EXPIRED", "CARD_VALIDITY_EXPIRED"],
    "AUTHENTICATION_FAILURE": ["BAD_REQUEST_PAYMENT_OTP_VALIDATION_FAILED", "3DS_AUTHENTICATION_TIMEOUT"],
    "TECHNICAL_FAILURE": ["GATEWAY_ERROR_INTERNAL_FAILURE", "PAYMENT_PROCESSING_HALTED"],
    "NETWORK_TIMEOUT": ["GATEWAY_TIMEOUT_ISSUER_UNREACHABLE", "NETWORK_SOCKET_TIMEOUT"],
    "FRAUD_SUSPECTED": ["RISK_CHECK_FAILED_SUSPECTED_FRAUD", "VELOCITY_RULE_BLOCKED"],
    "LIMIT_EXCEEDED": ["BAD_REQUEST_PAYMENT_DAILY_LIMIT_EXCEEDED", "ISSUER_CARD_LIMIT_REACHED"],
    "UNKNOWN": ["PAYMENT_FAILED_UNKNOWN_REASON", "GENERIC_FAILURE"],
}

PAYMENT_METHODS: Final[list[tuple[str, float]]] = [
    ("card", 0.50),
    ("upi", 0.30),
    ("netbanking", 0.12),
    ("wallet", 0.05),
    ("emi", 0.03),
]

INTERVENTIONS: Final[list[str]] = [
    "NO_INTERVENTION",
    "PAYMENT_LINK",
    "RETRY",
    "CUSTOMER_ACTION",
]


class PaymentEnvironmentSimulator:
    """Simulates realistic payments, failures, latent customer states, and counterfactual outcomes."""

    def __init__(self, seed: int = 42, num_merchants: int = 25, num_customers: int = 2000):
        self.seed = seed
        self.num_merchants = num_merchants
        self.num_customers = num_customers
        self.rng = random.Random(seed)

    def generate_dataset(self, count: int = 20000) -> list[SyntheticPaymentRecord]:
        """Generate a deterministic dataset of `count` payment failures."""
        merchants = self._generate_merchants()
        customers = self._generate_customers(merchants)

        records: list[SyntheticPaymentRecord] = []
        for i in range(count):
            record = self._simulate_payment_failure(i, customers)
            records.append(record)

        # Ensure strict chronological order by created_at
        records.sort(key=lambda r: r.created_at)
        return records

    def _generate_merchants(self) -> list[dict[str, Any]]:
        merchants = []
        risk_tiers = ["LOW", "MEDIUM", "HIGH"]
        tier_weights = [0.70, 0.25, 0.05]

        for i in range(self.num_merchants):
            tier = self.rng.choices(risk_tiers, weights=tier_weights, k=1)[0]
            baseline_failure = 0.03 if tier == "LOW" else (0.08 if tier == "MEDIUM" else 0.18)
            baseline_failure += self.rng.uniform(-0.01, 0.02)

            merchants.append({
                "merchant_id": f"merch_{i:03d}",
                "risk_tier": tier,
                "failure_baseline": max(0.01, round(baseline_failure, 4)),
            })
        return merchants

    def _generate_customers(self, merchants: list[dict[str, Any]]) -> list[dict[str, Any]]:
        customers = []
        for i in range(self.num_customers):
            m = self.rng.choice(merchants)
            tenure_days = self.rng.randint(5, 730)
            prior_attempts = max(1, int(self.rng.expovariate(1.0 / 15.0)))
            # Latent intent and liquidity
            intent = self.rng.betavariate(3.0, 1.5)  # typically high intent for genuine users
            liquidity = self.rng.betavariate(2.0, 2.0)  # evenly distributed liquidity

            success_rate = min(0.98, max(0.20, 0.40 + 0.50 * intent + 0.10 * (liquidity - 0.5)))
            prior_failures = max(0, int(prior_attempts * (1.0 - success_rate)))
            recovery_rate = min(0.95, max(0.10, 0.30 + 0.50 * liquidity))

            customers.append({
                "customer_id": f"cust_{i:05d}",
                "merchant_id": m["merchant_id"],
                "merchant_risk_tier": m["risk_tier"],
                "merchant_failure_baseline": m["failure_baseline"],
                "tenure_days": tenure_days,
                "prior_attempts": prior_attempts,
                "historical_success_rate": round(success_rate, 4),
                "historical_failure_count": prior_failures,
                "prior_recovery_rate": round(recovery_rate, 4),
                "latent_intent": intent,
                "latent_liquidity": liquidity,
            })
        return customers

    def _simulate_payment_failure(self, idx: int, customers: list[dict[str, Any]]) -> SyntheticPaymentRecord:
        cust = self.rng.choice(customers)

        # 1. Temporal context
        seconds_back = self.rng.randint(0, HORIZON_DAYS * 86400)
        created_at = BASE_EPOCH - timedelta(seconds=seconds_back)
        time_since_failure_sec = self.rng.uniform(60.0, 7200.0)
        decision_ts = created_at + timedelta(seconds=time_since_failure_sec)

        hour_of_day = created_at.hour
        day_of_week = created_at.weekday()

        # Gateway health (with periodic simulated transient dips)
        gw_health = 0.98 - 0.08 * math.sin(hour_of_day / 3.0) + self.rng.gauss(0, 0.02)
        gw_health = min(1.0, max(0.50, round(gw_health, 3)))

        # Latent issuer availability
        latent_issuer = min(1.0, max(0.40, gw_health + self.rng.gauss(0, 0.05)))

        # 2. Payment context
        is_sub = self.rng.random() < 0.35
        # Lognormal amount distribution: mode ~₹600, long tail
        log_amt = self.rng.gauss(11.0, 1.1)
        amount_paise = max(100, round(math.exp(log_amt)))
        sub_billing_paise = amount_paise if is_sub else None

        methods, weights = zip(*PAYMENT_METHODS)
        method = self.rng.choices(methods, weights=weights, k=1)[0]
        attempt_count = 1 + int(self.rng.expovariate(1.0 / 1.2))  # majority attempt 1, some 2 or 3

        # 3. Failure Category (Correlated with latent liquidity and method)
        if cust["latent_liquidity"] < 0.25 and self.rng.random() < 0.65:
            cat = "INSUFFICIENT_FUNDS"
        elif method == "card" and self.rng.random() < 0.15:
            cat = "EXPIRED_CARD"
        elif method in ("card", "upi") and self.rng.random() < 0.22:
            cat = "AUTHENTICATION_FAILURE"
        elif latent_issuer < 0.70:
            cat = self.rng.choice(["TECHNICAL_FAILURE", "NETWORK_TIMEOUT"])
        elif amount_paise > 200_000 and self.rng.random() < 0.30:
            cat = "LIMIT_EXCEEDED"
        else:
            cat = self.rng.choice(FAILURE_CATEGORIES)

        is_transient = cat in ("TECHNICAL_FAILURE", "NETWORK_TIMEOUT", "AUTHENTICATION_FAILURE")
        err_code = self.rng.choice(ERROR_CODE_MAP.get(cat, ["GENERIC_FAILURE"]))

        # 4. POTENTIAL OUTCOMES COMPUTATION (Structural Latent Response)
        # Base organic recovery propensity (Y(0))
        # Depends on intent, liquidity, issuer availability, and failure category
        cat_organic_multiplier = {
            "TECHNICAL_FAILURE": 0.60,
            "NETWORK_TIMEOUT": 0.50,
            "AUTHENTICATION_FAILURE": 0.40,
            "CARD_DECLINED": 0.20,
            "INSUFFICIENT_FUNDS": 0.10,
            "EXPIRED_CARD": 0.05,
            "FRAUD_SUSPECTED": 0.02,
            "LIMIT_EXCEEDED": 0.25,
            "UNKNOWN": 0.15,
        }.get(cat, 0.20)

        # Baseline recovery probability P(Y(0) = 1)
        p_y0 = cat_organic_multiplier * (0.3 + 0.4 * cust["latent_intent"] + 0.3 * cust["latent_liquidity"])
        p_y0 = min(0.85, max(0.02, p_y0 * (1.0 - 0.05 * (attempt_count - 1))))
        y_0 = self.rng.random() < p_y0

        # Intervention potential outcomes:
        # PAYMENT_LINK: strongly boosts expired card, auth failure, card declines
        uplift_plink = 0.0
        if cat in ("EXPIRED_CARD", "AUTHENTICATION_FAILURE", "CARD_DECLINED", "LIMIT_EXCEEDED"):
            uplift_plink = 0.35 * cust["latent_intent"] + 0.15 * cust["latent_liquidity"]
        elif cat == "INSUFFICIENT_FUNDS":
            uplift_plink = 0.10 * cust["latent_liquidity"]
        else:
            uplift_plink = 0.08
        p_plink = min(0.95, p_y0 + uplift_plink)
        y_plink = True if y_0 else (self.rng.random() < (uplift_plink / (1.0 - p_y0 + 1e-6)))

        # RETRY: strongly boosts technical failures & network timeouts
        uplift_retry = 0.0
        if cat in ("TECHNICAL_FAILURE", "NETWORK_TIMEOUT"):
            uplift_retry = 0.40 * latent_issuer
        elif cat == "INSUFFICIENT_FUNDS":
            # delayed retry after liquidity replenishment
            uplift_retry = 0.12 * cust["latent_liquidity"]
        else:
            uplift_retry = 0.04
        p_retry = min(0.95, p_y0 + uplift_retry)
        y_retry = True if y_0 else (self.rng.random() < (uplift_retry / (1.0 - p_y0 + 1e-6)))

        # CUSTOMER_ACTION: boosts auth failures and expired cards
        uplift_action = 0.0
        if cat in ("AUTHENTICATION_FAILURE", "EXPIRED_CARD"):
            uplift_action = 0.45 * cust["latent_intent"]
        else:
            uplift_action = 0.05
        p_action = min(0.95, p_y0 + uplift_action)
        y_action = True if y_0 else (self.rng.random() < (uplift_action / (1.0 - p_y0 + 1e-6)))

        # 5. Treatment Assignment (Controlled assignment with exploration)
        # 30% random exploration, 70% policy-guided heuristic
        explore = self.rng.random() < 0.30
        if explore:
            treatment = self.rng.choice(INTERVENTIONS)
            propensity = 0.25
        else:
            # Policy-guided propensity
            if cat in ("TECHNICAL_FAILURE", "NETWORK_TIMEOUT"):
                treatment = "RETRY"
                propensity = 0.65
            elif cat in ("EXPIRED_CARD", "AUTHENTICATION_FAILURE"):
                treatment = "PAYMENT_LINK" if self.rng.random() < 0.6 else "CUSTOMER_ACTION"
                propensity = 0.60
            elif amount_paise > 50_000:
                treatment = "PAYMENT_LINK"
                propensity = 0.55
            else:
                treatment = "NO_INTERVENTION"
                propensity = 0.40

        # Observed outcome under assigned treatment
        if treatment == "NO_INTERVENTION":
            observed_recovered = y_0
            true_uplift = 0.0
        elif treatment == "PAYMENT_LINK":
            observed_recovered = y_plink
            true_uplift = max(0.0, p_plink - p_y0)
        elif treatment == "RETRY":
            observed_recovered = y_retry
            true_uplift = max(0.0, p_retry - p_y0)
        elif treatment == "CUSTOMER_ACTION":
            observed_recovered = y_action
            true_uplift = max(0.0, p_action - p_y0)
        else:
            observed_recovered = y_0
            true_uplift = 0.0

        recovered_amount = amount_paise if observed_recovered else 0
        recovery_time_sec = self.rng.uniform(120.0, 86400.0) if observed_recovered else None

        return SyntheticPaymentRecord(
            case_id=f"case_v2_{idx:08d}",
            merchant_id=cust["merchant_id"],
            customer_id=cust["customer_id"],
            payment_id=f"pay_v2_{idx:08d}",
            created_at=created_at,
            decision_timestamp=decision_ts,
            amount_paise=amount_paise,
            currency="INR",
            payment_method=method,
            payment_attempt_count=attempt_count,
            is_subscription=is_sub,
            subscription_billing_amount_paise=sub_billing_paise,
            failure_category=cat,
            error_code=err_code,
            is_transient_error=is_transient,
            customer_tenure_days=cust["tenure_days"],
            customer_historical_success_rate=cust["historical_success_rate"],
            customer_historical_failure_count=cust["historical_failure_count"],
            customer_prior_recovery_rate=cust["prior_recovery_rate"],
            merchant_risk_tier=cust["merchant_risk_tier"],
            merchant_failure_baseline=cust["merchant_failure_baseline"],
            time_since_failure_seconds=round(time_since_failure_sec, 2),
            hour_of_day=hour_of_day,
            day_of_week=day_of_week,
            gateway_health_score=gw_health,
            intervention_assigned=treatment,
            treatment_propensity=round(propensity, 4),
            recovered=observed_recovered,
            recovered_amount=recovered_amount,
            recovery_time_seconds=round(recovery_time_sec, 2) if recovery_time_sec else None,
            y_0_baseline=y_0,
            y_payment_link=y_plink,
            y_retry=y_retry,
            y_customer_action=y_action,
            true_incremental_uplift=round(true_uplift, 4),
            latent_customer_intent=round(cust["latent_intent"], 4),
            latent_temporary_liquidity=round(cust["latent_liquidity"], 4),
            latent_issuer_availability=round(latent_issuer, 4),
        )


def generate_v2_dataset(seed: int = 42, count: int = 20000) -> list[SyntheticPaymentRecord]:
    """Top-level convenience generator."""
    sim = PaymentEnvironmentSimulator(seed=seed)
    return sim.generate_dataset(count=count)
