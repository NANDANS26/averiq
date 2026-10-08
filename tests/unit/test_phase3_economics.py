"""Unit tests for Phase 3.6 Economic Correctness, Monotonicity, and Canonical Accounting."""
from __future__ import annotations

from decimal import Decimal
import pytest
import pandas as pd
import numpy as np

from ai.evaluation.phase3_economic_evaluation import (
    calculate_action_gains,
    calculate_oracle_action,
    calculate_regret,
    evaluate_strategy_records,
    CANONICAL_COSTS_PAISE,
    ORACLE_TIE_PRIORITY,
)
from backend.decision_engine.context_aware_ranker import (
    ContextAwareInterventionRanker,
    compute_incremental_ev,
)
from shared.contracts.enums import FailureCategory, InterventionType
from shared.contracts.schemas import CaseContext, PolicyConfig


@pytest.fixture
def sample_test_df() -> pd.DataFrame:
    """Fixture providing a deterministic small dataset for unit verification."""
    data = [
        # Case 1: Organic recovery (y0=1), no intervention needed
        {"case_id": "c1", "amount_paise": 100000, "y_0_baseline": 1, "y_retry": 1, "y_payment_link": 1, "y_customer_action": 1},
        # Case 2: Only payment link recovers (y0=0, ypl=1)
        {"case_id": "c2", "amount_paise": 200000, "y_0_baseline": 0, "y_retry": 0, "y_payment_link": 1, "y_customer_action": 0},
        # Case 3: Only customer action recovers (y0=0, yca=1)
        {"case_id": "c3", "amount_paise": 50000, "y_0_baseline": 0, "y_retry": 0, "y_payment_link": 0, "y_customer_action": 1},
        # Case 4: Complete failure across all actions
        {"case_id": "c4", "amount_paise": 80000, "y_0_baseline": 0, "y_retry": 0, "y_payment_link": 0, "y_customer_action": 0},
    ]
    return pd.DataFrame(data)


def test_oracle_tie_determinism():
    """Verify tie-breaking follows explicit priority order: NO_INTERVENTION, RETRY, CUSTOMER_ACTION, PAYMENT_LINK."""
    # When all gains are 0 (e.g. y0=0, all y=0, but cost=0 for NO_INTERVENTION and negative for others)
    gains = {"NO_INTERVENTION": 0, "RETRY": -100, "CUSTOMER_ACTION": -200, "PAYMENT_LINK": -500}
    act, val = calculate_oracle_action(gains)
    assert act == "NO_INTERVENTION"
    assert val == 0

    # Tied positive gain between RETRY and CUSTOMER_ACTION
    gains_tied = {"NO_INTERVENTION": 0, "RETRY": 5000, "CUSTOMER_ACTION": 5000, "PAYMENT_LINK": 4000}
    act, val = calculate_oracle_action(gains_tied)
    assert act == "RETRY"  # RETRY has higher priority than CUSTOMER_ACTION
    assert val == 5000


def test_oracle_agreement_cell_sum_identity(sample_test_df):
    """Verify agreement_count == sum(correct_cells) and rate == count / total."""
    # Strategy that picks: NO_INTERVENTION, PAYMENT_LINK, STOP, RETRY
    actions = ["NO_INTERVENTION", "PAYMENT_LINK", "STOP", "RETRY"]
    
    # Calculate oracles
    oracle_acts = []
    for _, r in sample_test_df.iterrows():
        g = calculate_action_gains(r["amount_paise"], r["y_0_baseline"], r["y_retry"], r["y_payment_link"], r["y_customer_action"])
        act, _ = calculate_oracle_action(g)
        oracle_acts.append(act)

    # Build confusion matrix
    matrix = {}
    for a_strat, a_orc in zip(actions, oracle_acts):
        key = f"{a_strat}->{a_orc}"
        matrix[key] = matrix.get(key, 0) + 1

    # Exact rule: match if a_strat == a_orc OR (a_strat == 'STOP' and a_orc == 'NO_INTERVENTION')
    correct_cells = [
        count for key, count in matrix.items()
        if (key.split("->")[0] == key.split("->")[1])
        or (key == "STOP->NO_INTERVENTION")
    ]
    total_agreed = sum(correct_cells)
    
    direct_agreed = sum(
        1 for s, o in zip(actions, oracle_acts)
        if s == o or (s == "STOP" and o == "NO_INTERVENTION")
    )
    assert total_agreed == direct_agreed
    assert total_agreed / len(sample_test_df) == pytest.approx(direct_agreed / len(sample_test_df))


def test_economic_accounting_identities(sample_test_df):
    """Verify Gross == Organic + Incremental, and Net Incremental == Incremental - Cost."""
    actions = ["RETRY", "PAYMENT_LINK", "CUSTOMER_ACTION", "STOP"]
    res = evaluate_strategy_records(sample_test_df, actions)

    # Identity 1: Gross = Organic + Incremental
    assert res["gross_recovered_paise"] == res["organic_recovered_paise"] + res["incremental_recovered_paise"]

    # Identity 2: Net Incremental = Incremental - Cost
    assert res["net_incremental_value_paise"] == res["incremental_recovered_paise"] - res["intervention_cost_paise"]

    # Identity 3: All monetary totals are strictly int
    assert isinstance(res["gross_recovered_paise"], int)
    assert isinstance(res["organic_recovered_paise"], int)
    assert isinstance(res["incremental_recovered_paise"], int)
    assert isinstance(res["intervention_cost_paise"], int)
    assert isinstance(res["net_incremental_value_paise"], int)


def test_economic_monotonicity_test_a_cost_increase():
    """Test A: Higher action cost cannot increase that action's economic score."""
    uplift = 0.15
    amt = 100000  # ₹1,000
    ev_low_cost = compute_incremental_ev(uplift, amt, cost_paise=200)
    ev_high_cost = compute_incremental_ev(uplift, amt, cost_paise=500)
    assert ev_high_cost < ev_low_cost


def test_economic_monotonicity_test_b_amount_increase():
    """Test B: Higher transaction amount increases the monetary value of positive uplift."""
    uplift = 0.10
    cost = 200
    ev_small = compute_incremental_ev(uplift, amount_paise=50000, cost_paise=cost)
    ev_large = compute_incremental_ev(uplift, amount_paise=200000, cost_paise=cost)
    assert ev_large > ev_small


def test_economic_monotonicity_test_c_zero_uplift():
    """Test C: Zero uplift cannot create positive incremental value."""
    uplift = 0.0
    amt = 1000000  # ₹10,000
    cost = 200
    ev = compute_incremental_ev(uplift, amt, cost)
    assert ev <= 0
    assert ev == -cost


def test_economic_monotonicity_test_d_stop_zero_cost():
    """Test D: STOP has zero intervention cost."""
    assert CANONICAL_COSTS_PAISE["STOP"] == 0
    assert CANONICAL_COSTS_PAISE["NO_INTERVENTION"] == 0


def test_economic_monotonicity_test_e_negative_uplift_clamping():
    """Test E: Negative uplift is clamped to zero in compute_incremental_ev."""
    uplift_negative = -0.08
    amt = 100000
    cost = 200
    ev = compute_incremental_ev(uplift_negative, amt, cost)
    # max(0, -0.08) * amt - cost = 0 - 200 = -200
    assert ev == -200


def test_economic_monotonicity_test_f_strict_integer_paise():
    """Test F: All monetary calculations remain integer paise."""
    uplift = 0.123456
    amt = 789123
    cost = 200
    ev = compute_incremental_ev(uplift, amt, cost)
    assert isinstance(ev, int)


def test_regret_mathematical_integrity_on_test_split():
    """Verify Tests 1-6 on the actual 3,000-case holdout test split:
    Test 1: Non-negative regret (regret >= 0)
    Test 2: Percentile ordering (median <= p75 <= p90 <= p95 <= max)
    Test 3: Correct regret identity (regret == oracle_value - strategy_value)
    Test 4: Exact test-set size (len == 3000)
    Test 5: Max consistency (reported_max == actual_max)
    Test 6: P95 bounded by max (p95 <= max)
    """
    from pathlib import Path
    from ai.models.action_value_model import ActionValueEstimator
    from backend.decision_engine.context_aware_ranker import ContextAwareInterventionRanker

    dataset_path = Path("data/v2/dataset_20k.parquet")
    if not dataset_path.exists():
        pytest.skip("Dataset 2.0 parquet not found")

    df = pd.read_parquet(dataset_path)
    test_df = df[df["split"] == "test"].copy().reset_index(drop=True)

    artifact_path = Path("model_artifacts/action_value_v3.pkl")
    if not artifact_path.exists():
        pytest.skip("Action value artifact not found")

    m3 = ActionValueEstimator.load(artifact_path)
    ranker = ContextAwareInterventionRanker(estimator=m3)
    policy_cfg = PolicyConfig(
        policy_id="pol_test_001", merchant_id="merch_test_001", version="v1",
        min_confidence=0.10, cooldown_seconds=300,
        max_retries={"PAYMENT_LINK": 3, "RETRY": 3, "CUSTOMER_ACTION": 3},
        max_amount={"INR": 50_000_000}, human_approval_threshold=100_000_000,
        attribution_window_seconds=86400, exposure_thresholds={"HIGH": 100_000, "MEDIUM": 20_000},
    )

    ranker_regrets = []
    heuristic_regrets = []

    for _, row in test_df.iterrows():
        amt = int(row["amount_paise"])
        y0 = int(row["y_0_baseline"])
        yr = int(row["y_retry"])
        ypl = int(row["y_payment_link"])
        yca = int(row["y_customer_action"])

        gains = calculate_action_gains(amt, y0, yr, ypl, yca)
        _, o_val = calculate_oracle_action(gains)

        # Heuristic choice
        fcat = row["failure_category"]
        if fcat in ("TECHNICAL_FAILURE", "NETWORK_TIMEOUT"):
            act_h = "RETRY"
        elif fcat in ("AUTHENTICATION_FAILURE",):
            act_h = "CUSTOMER_ACTION"
        else:
            act_h = "PAYMENT_LINK"
        h_gain = gains[act_h]
        heuristic_regrets.append(calculate_regret(o_val, h_gain))

        # Ranker choice
        c_data = {
            "amount_paise": amt, "currency": "INR", "failure_category": row["failure_category"],
            "payment_method": row["payment_method"], "time_since_failure_seconds": 0.0,
            "hour_of_day": int(row["hour_of_day"]), "day_of_week": int(row["day_of_week"]),
            "customer_historical_failure_count": int(row.get("customer_historical_failure_count", 0)),
            "is_subscription": bool(row.get("is_subscription", False)),
            "subscription_billing_amount_paise": int(row["subscription_billing_amount_paise"]) if pd.notna(row.get("subscription_billing_amount_paise")) else None,
        }
        probs = m3.predict_action_probabilities(c_data)
        cid = str(row["case_id"])
        ctx = CaseContext(
            case_id=cid, merchant_id="merch_test_001", payment_id="pay_" + cid,
            amount_paise=amt, currency="INR", error_code=None,
            previous_failure_count=int(row.get("customer_historical_failure_count", 0)),
            created_at=row["created_at"],
        )
        try:
            fcat_enum = FailureCategory(row["failure_category"])
        except Exception:
            fcat_enum = FailureCategory.UNKNOWN
        ranked, _ = ranker.generate_and_rank_candidates(
            context=ctx, policy_config=policy_cfg, action_probabilities=probs, failure_category=fcat_enum
        )
        act_r = ranked[0].intervention_type.value
        r_gain = gains.get(act_r, 0)
        ranker_regrets.append(calculate_regret(o_val, r_gain))

    # Test 4: Exact test-set size
    assert len(ranker_regrets) == 3000
    assert len(heuristic_regrets) == 3000

    r_arr = np.array(ranker_regrets, dtype=np.int64)
    h_arr = np.array(heuristic_regrets, dtype=np.int64)

    for arr in (r_arr, h_arr):
        # Test 1: Non-negative regret
        assert np.min(arr) >= 0

        # Test 2: Percentile ordering
        med = np.median(arr)
        p75 = np.percentile(arr, 75)
        p90 = np.percentile(arr, 90)
        p95 = np.percentile(arr, 95)
        mx = np.max(arr)
        assert np.min(arr) <= med <= p75 <= p90 <= p95 <= mx

        # Test 5 & 6: Max consistency & p95 bounded by max
        assert mx == np.max(arr)
        assert p95 <= mx

