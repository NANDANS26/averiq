"""Canonical economic evaluation module for Averiq Decision Engine.

Calculates:
  1. Action gains and deterministic oracle decisions based on realized counterfactual outcomes.
  2. Incremental accounting: Organic, Gross, Incremental Revenue, Costs, and Net Incremental Value.
  3. Economic regret quantiles.
  4. Exact oracle agreement matrices.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR
from typing import Any, Final
import numpy as np
import pandas as pd
from shared.contracts.enums import FailureCategory, InterventionType
from shared.contracts.schemas import CaseContext, PolicyConfig

# Canonical intervention costs in paise
CANONICAL_COSTS_PAISE: Final[dict[str, int]] = {
    "NO_INTERVENTION": 0,
    "STOP": 0,
    "RETRY": 100,            # ₹1.00
    "CUSTOMER_ACTION": 200,  # ₹2.00
    "PAYMENT_LINK": 500,     # ₹5.00
}

# Deterministic tie-breaking priority order for oracle
ORACLE_TIE_PRIORITY: Final[list[str]] = [
    "NO_INTERVENTION",
    "RETRY",
    "CUSTOMER_ACTION",
    "PAYMENT_LINK",
]


def calculate_action_gains(
    amount_paise: int,
    y0: int,
    y_retry: int,
    y_payment_link: int,
    y_customer_action: int,
    costs: dict[str, int] | None = None,
) -> dict[str, int]:
    """Calculate net realized gain in integer paise for each candidate action."""
    c = costs or CANONICAL_COSTS_PAISE
    return {
        "NO_INTERVENTION": 0,
        "RETRY": (y_retry - y0) * amount_paise - c["RETRY"],
        "PAYMENT_LINK": (y_payment_link - y0) * amount_paise - c["PAYMENT_LINK"],
        "CUSTOMER_ACTION": (y_customer_action - y0) * amount_paise - c["CUSTOMER_ACTION"],
    }


def calculate_oracle_action(
    action_gains: dict[str, int],
    priority_order: list[str] | None = None,
) -> tuple[str, int]:
    """Deterministically select oracle optimal action and its value.
    
    Returns:
        (best_action, best_value_paise)
    """
    order = priority_order or ORACLE_TIE_PRIORITY
    max_gain = max(action_gains.values())
    for act in order:
        if action_gains[act] == max_gain:
            return act, max_gain
    # Fallback to python max
    best_act = max(action_gains.keys(), key=lambda a: action_gains[a])
    return best_act, action_gains[best_act]


def calculate_regret(
    oracle_value_paise: int,
    chosen_action_gain_paise: int,
) -> int:
    """Calculate realized economic regret in paise."""
    return max(0, oracle_value_paise - chosen_action_gain_paise)


def evaluate_strategy_records(
    test_df: pd.DataFrame,
    strategy_actions: list[str],
    costs: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Compute complete canonical economic metrics for a list of strategy decisions."""
    c = costs or CANONICAL_COSTS_PAISE
    n = len(test_df)
    if len(strategy_actions) != n:
        raise ValueError(f"Action count {len(strategy_actions)} != dataset rows {n}")

    gross_rev_paise = 0
    organic_rev_paise = 0
    incremental_rev_paise = 0
    cost_paise = 0
    net_inc_val_paise = 0

    intervention_count = 0
    stop_count = 0
    recovered_count = 0
    incremental_recovered_count = 0

    case_values = []
    action_counts: dict[str, int] = {}

    for i, (_, row) in enumerate(test_df.iterrows()):
        amt = int(row["amount_paise"])
        y0 = int(row["y_0_baseline"])
        act = strategy_actions[i]
        action_counts[act] = action_counts.get(act, 0) + 1

        is_stop = (act in ("STOP", "NO_INTERVENTION"))
        if is_stop:
            stop_count += 1
            y_act = y0
            act_cost = 0
        else:
            intervention_count += 1
            col_name = f"y_{act.lower()}"
            y_act = int(row[col_name])
            act_cost = c[act]

        gross = y_act * amt
        organic = y0 * amt
        inc_rev = (y_act - y0) * amt
        net_inc = inc_rev - act_cost

        gross_rev_paise += gross
        organic_rev_paise += organic
        incremental_rev_paise += inc_rev
        cost_paise += act_cost
        net_inc_val_paise += net_inc
        case_values.append(net_inc)

        if y_act == 1:
            recovered_count += 1
        if y_act == 1 and y0 == 0:
            incremental_recovered_count += 1

    return {
        "test_n": n,
        "action_counts": action_counts,
        "intervention_count": intervention_count,
        "stop_count": stop_count,
        "intervention_rate": intervention_count / n if n > 0 else 0.0,
        "gross_recovered_paise": gross_rev_paise,
        "organic_recovered_paise": organic_rev_paise,
        "incremental_recovered_paise": incremental_rev_paise,
        "intervention_cost_paise": cost_paise,
        "net_incremental_value_paise": net_inc_val_paise,
        "mean_net_incremental_paise": net_inc_val_paise / n if n > 0 else 0.0,
        "median_net_incremental_paise": float(np.median(case_values)) if n > 0 else 0.0,
        "total_recovery_rate": recovered_count / n if n > 0 else 0.0,
        "incremental_recovery_rate": incremental_recovered_count / n if n > 0 else 0.0,
        "cost_per_case_paise": cost_paise / n if n > 0 else 0.0,
        "case_values": case_values,
    }
