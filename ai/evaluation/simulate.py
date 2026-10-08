#!/usr/bin/env python3
"""make simulate — run recovery simulation on the test split.

Reports the following metrics for each model:
  Recovery_Rate:                  recovered_cases / total_eligible_cases
  Incremental_Recovered_Revenue:  (see formula below)
  Intervention_Success_Rate:      successful_interventions / total_interventions
  Escalation_Rate:                escalated_cases / total_eligible_cases
  Stop_Rate:                      stopped_cases / total_eligible_cases

Division by zero → emit NOT_AVAILABLE (never a runtime error).
All values computed at runtime from the test split — no hardcoded targets.

Incremental_Recovered_Revenue formula:
  = actual_recovered_revenue_in_cohort - baseline_expected_recovered_revenue
  Both values in integer paise (minor currency units).
  Same eligibility criteria, same observation window, same currency.
  NOTE: This is incremental measurement relative to a defined baseline,
  NOT a causal claim about uplift.

Requirements: EVAL-003
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _safe_rate(numerator: int, denominator: int) -> float | str:
    """Compute numerator/denominator, returning NOT_AVAILABLE on zero denom.

    Parameters
    ----------
    numerator:
        The dividend.
    denominator:
        The divisor. If zero, returns the sentinel string "NOT_AVAILABLE".

    Returns
    -------
    float | str
        A rounded float rate, or "NOT_AVAILABLE" when denominator is zero.
    """
    if denominator == 0:
        return "NOT_AVAILABLE"
    return round(numerator / denominator, 4)


def simulate_model(model_name: str, proba_list: list[float], cases: list) -> dict:
    """Run a simple threshold-based simulation on the test cases.

    Decision rule: if recovery_probability >= 0.5 → intervene (PAYMENT_LINK)
    else → STOP.

    Simulated outcomes:
      - Intervened + actual recovery_outcome=True  → RECOVERED
      - Intervened + actual recovery_outcome=False → FAILED (escalate after N retries)
      - Not intervened → STOPPED

    Metrics returned (formal definitions from Glossary in requirements.md):
      Recovery_Rate                : recovered_cases / total_eligible_cases
      Incremental_Recovered_Revenue: actual_recovered_revenue - baseline_expected
      Intervention_Success_Rate    : successful_interventions / total_interventions
      Escalation_Rate              : escalated_cases / total_eligible_cases
      Stop_Rate                    : stopped_cases / total_eligible_cases

    Division by zero emits NOT_AVAILABLE per EVAL-003.

    Parameters
    ----------
    model_name:
        Human-readable label for this model variant (used in reports).
    proba_list:
        Recovery probability predictions, one per case, in the same order.
    cases:
        List of SyntheticCase objects.  Each must have ``amount_paise`` (int)
        and ``recovery_outcome`` (bool).

    Returns
    -------
    dict
        Keys: total_cases, Recovery_Rate, Incremental_Recovered_Revenue,
        Intervention_Success_Rate, Escalation_Rate, Stop_Rate,
        recovered_cases, interventions.
    """
    total = len(cases)
    recovered = 0
    stopped = 0
    escalated = 0
    successful_interventions = 0
    total_interventions = 0
    actual_revenue_recovered = 0  # integer paise

    # Baseline: assume 10% organic recovery rate (no intervention).
    # This is the counter-factual baseline for incremental revenue calculation
    # (requirements.md FR-ATTR-003, design.md Incremental Revenue Calculation).
    # NOTE: This is an incremental measurement relative to a defined baseline,
    # not a causal claim about uplift.
    baseline_rate = 0.10

    for case, prob in zip(cases, proba_list):
        amount: int = case.amount_paise
        actual_outcome: bool = case.recovery_outcome

        if prob >= 0.5:
            # Intervene (PAYMENT_LINK or similar EXECUTABLE action)
            total_interventions += 1
            if actual_outcome:
                recovered += 1
                successful_interventions += 1
                actual_revenue_recovered += amount
            else:
                # Failed intervention → escalate
                escalated += 1
        else:
            # Stop — don't intervene
            stopped += 1
            if actual_outcome:
                # Organic recovery even without intervention
                actual_revenue_recovered += amount

    # Baseline revenue: how much would have been recovered without Averiq
    # (10% organic baseline applied to total eligible-case revenue).
    total_revenue: int = sum(c.amount_paise for c in cases)
    baseline_revenue_recovered: int = int(total_revenue * baseline_rate)

    incremental: int = actual_revenue_recovered - baseline_revenue_recovered

    return {
        "total_cases": total,
        "Recovery_Rate": _safe_rate(recovered, total),
        "Incremental_Recovered_Revenue": incremental,  # integer paise
        "Intervention_Success_Rate": _safe_rate(successful_interventions, total_interventions),
        "Escalation_Rate": _safe_rate(escalated, total),
        "Stop_Rate": _safe_rate(stopped, total),
        # Raw counts for downstream inspection / debugging
        "recovered_cases": recovered,
        "interventions": total_interventions,
    }


def main() -> None:
    """Run the simulation pipeline and print the results table.

    Environment variables (all optional — safe defaults provided):
      DATASET_SIZE        int  default 50000
      RANDOM_SEED         int  default 42
      MODEL_ARTIFACTS_DIR str  default "model_artifacts"

    Exit codes:
      0 — simulation completed (output written to stdout).
      1 — critical error (empty test split, import failure, etc.).
    """
    dataset_size = int(os.environ.get("DATASET_SIZE", "50000"))
    random_seed = int(os.environ.get("RANDOM_SEED", "42"))
    artifacts_dir = Path(os.environ.get("MODEL_ARTIFACTS_DIR", "model_artifacts"))

    print("=" * 65)
    print("Averiq — make simulate")
    print("=" * 65)
    print(f"  DATASET_SIZE   : {dataset_size:,}")
    print(f"  RANDOM_SEED    : {random_seed}")

    from ai.data.generator import generate
    from ai.training.feature_extraction import temporal_split, extract_features_from_synthetic

    print("\nGenerating dataset and extracting test split...")
    cases = generate(seed=random_seed, count=dataset_size)
    _, _, test_cases = temporal_split(cases)
    print(f"  Test cases: {len(test_cases):,}")

    if not test_cases:
        print(
            "ERROR: Test split empty. Increase DATASET_SIZE.",
            file=sys.stderr,
        )
        sys.exit(1)

    features_test = [extract_features_from_synthetic(c) for c in test_cases]
    all_results: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Deterministic baseline: always predict 0.5 (maximum entropy)
    # This provides a performance floor for comparison.
    # ------------------------------------------------------------------
    baseline_proba = [0.5] * len(test_cases)
    all_results["DeterministicFallback"] = simulate_model(
        "DeterministicFallback", baseline_proba, test_cases
    )

    # ------------------------------------------------------------------
    # LightGBM primary model
    # ------------------------------------------------------------------
    lgbm_path = artifacts_dir / f"lgbm_{random_seed}_{dataset_size}.pkl"
    if lgbm_path.exists():
        try:
            from ai.models.lightgbm_model import LightGBMModel

            lgbm = LightGBMModel.load(lgbm_path)
            lgbm_proba = lgbm.predict_batch(features_test)
            all_results["LightGBM"] = simulate_model("LightGBM", lgbm_proba, test_cases)
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: LightGBM load/predict failed: {exc}")
            all_results["LightGBM"] = {"error": str(exc)}
    else:
        print(
            f"  INFO: LightGBM artifact not found at {lgbm_path}. "
            "Run 'make train' first."
        )

    # ------------------------------------------------------------------
    # Logistic Regression baseline model
    # ------------------------------------------------------------------
    lr_path = artifacts_dir / f"lr_{random_seed}_{dataset_size}.pkl"
    if lr_path.exists():
        try:
            from ai.models.logistic_regression import LogisticRegressionBaseline

            lr = LogisticRegressionBaseline.load(lr_path)
            lr_proba = lr.predict_batch(features_test)
            all_results["LogisticRegression"] = simulate_model(
                "LogisticRegression", lr_proba, test_cases
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: LogisticRegression load/predict failed: {exc}")
            all_results["LogisticRegression"] = {"error": str(exc)}
    else:
        print(
            f"  INFO: LogisticRegression artifact not found at {lr_path}. "
            "Run 'make train' first."
        )

    # ------------------------------------------------------------------
    # Print results table
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print(
        f"{'Model':<25} {'Recov_Rate':>11} {'Incr_Rev_₹':>12} "
        f"{'Intvn_Succ':>11} {'Esc_Rate':>9} {'Stop_Rate':>10}"
    )
    print("-" * 80)

    for model_name, r in all_results.items():
        if "error" in r:
            error_short = r["error"][:40]
            print(f"{model_name:<25}  [error: {error_short}]")
            continue

        rr = r.get("Recovery_Rate", "N/A")
        irr_paise = r.get("Incremental_Recovered_Revenue", 0)
        # Display incremental revenue in rupees (÷100) for readability
        if isinstance(irr_paise, int):
            irr_rupees = f"₹{irr_paise // 100:,}"
        else:
            irr_rupees = str(irr_paise)
        isr = r.get("Intervention_Success_Rate", "N/A")
        er = r.get("Escalation_Rate", "N/A")
        sr = r.get("Stop_Rate", "N/A")
        print(
            f"{model_name:<25} {str(rr):>11} {str(irr_rupees):>12} "
            f"{str(isr):>11} {str(er):>9} {str(sr):>10}"
        )

    print("=" * 80)
    print()
    print(
        "NOTE: Incremental_Recovered_Revenue is relative to 10% organic baseline."
    )
    print("      This is a measurement, not a causal claim.\n")


if __name__ == "__main__":
    main()
