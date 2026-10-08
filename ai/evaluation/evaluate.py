#!/usr/bin/env python3
"""make evaluate — evaluate recoverability models with quality gates.

Usage:
    python ai/evaluation/evaluate.py
    make evaluate

Environment variables:
    DATASET_SIZE:                  cases to generate (default: 50000)
    RANDOM_SEED:                   random seed (default: 42)
    BRIER_SCORE_WARNING_THRESHOLD: quality gate threshold (default: 0.25)
    MODEL_ARTIFACTS_DIR:           path to saved model artifacts (default: model_artifacts/)

Outputs:
    - Comparison table (stdout) for both models + deterministic baseline
    - Exits non-zero if any model fails the quality gate
    - All metrics computed on the same test cohort (cohort consistency, EVAL-011)

Requirements: FR-MODEL-006, FR-MODEL-007, FR-MODEL-008, EVAL-002, EVAL-004, EVAL-011
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root is on sys.path so all package imports resolve correctly
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def compute_metrics(y_true: list[int], y_pred_proba: list[float]) -> dict:
    """Compute classification metrics for a set of predictions.

    Parameters
    ----------
    y_true:
        Ground-truth binary labels (0 or 1).
    y_pred_proba:
        Predicted probabilities in [0.0, 1.0].

    Returns
    -------
    dict
        Keys: precision, recall, f1, auc_roc, brier_score.
        Any metric that cannot be computed (e.g., AUC when only one class
        is present) is reported as the string "NOT_AVAILABLE" so callers
        can detect and display it gracefully without a runtime error.

    Notes
    -----
    Threshold for binary decision: 0.5.
    AUC-ROC is not available when y_true contains only one class (degenerate
    case that cannot produce a meaningful ROC curve).
    """
    try:
        from sklearn.metrics import (  # type: ignore[import-untyped]
            brier_score_loss,
            f1_score,
            precision_score,
            recall_score,
            roc_auc_score,
        )
        has_sklearn = True
    except ImportError:
        has_sklearn = False

    if not y_true:
        # Empty inputs — return all NOT_AVAILABLE
        return {
            "precision": "NOT_AVAILABLE",
            "recall": "NOT_AVAILABLE",
            "f1": "NOT_AVAILABLE",
            "auc_roc": "NOT_AVAILABLE",
            "brier_score": "NOT_AVAILABLE",
        }

    y_pred_binary = [1 if p >= 0.5 else 0 for p in y_pred_proba]

    n_pos = sum(y_true)
    n_neg = len(y_true) - n_pos

    try:
        if has_sklearn:
            precision = precision_score(y_true, y_pred_binary, zero_division=0)
            recall = recall_score(y_true, y_pred_binary, zero_division=0)
            f1 = f1_score(y_true, y_pred_binary, zero_division=0)
            brier = brier_score_loss(y_true, y_pred_proba)

            # AUC-ROC requires both positive and negative examples to be meaningful
            if n_pos > 0 and n_neg > 0:
                auc: float | str = roc_auc_score(y_true, y_pred_proba)
            else:
                auc = "NOT_AVAILABLE"
        else:
            # Pure Python implementation without external dependency
            tp = sum(1 for yt, yp in zip(y_true, y_pred_binary) if yt == 1 and yp == 1)
            fp = sum(1 for yt, yp in zip(y_true, y_pred_binary) if yt == 0 and yp == 1)
            fn = sum(1 for yt, yp in zip(y_true, y_pred_binary) if yt == 1 and yp == 0)

            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
            brier = sum((yt - yp) ** 2 for yt, yp in zip(y_true, y_pred_proba)) / len(y_true)

            if n_pos > 0 and n_neg > 0:
                # Rank-sum Wilcoxon-Mann-Whitney calculation for AUC-ROC
                ranked = sorted(enumerate(y_pred_proba), key=lambda x: x[1])
                pos_ranks = [rank + 1 for rank, (idx, _) in enumerate(ranked) if y_true[idx] == 1]
                auc = (sum(pos_ranks) - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
            else:
                auc = "NOT_AVAILABLE"

    except Exception as exc:  # noqa: BLE001
        print(f"  WARNING: metric computation error: {exc}", file=sys.stderr)
        return {
            "precision": "NOT_AVAILABLE",
            "recall": "NOT_AVAILABLE",
            "f1": "NOT_AVAILABLE",
            "auc_roc": "NOT_AVAILABLE",
            "brier_score": "NOT_AVAILABLE",
        }

    return {
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "f1": round(float(f1), 4),
        "auc_roc": round(float(auc), 4) if isinstance(auc, float) else "NOT_AVAILABLE",
        "brier_score": round(float(brier), 4),
    }


# ---------------------------------------------------------------------------
# Main evaluation pipeline
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the full evaluation pipeline and enforce the quality gate.

    Steps:
      1. Generate the synthetic dataset with the configured seed
         (same call as make train — ensures cohort consistency, EVAL-011).
      2. Apply the temporal split and use ONLY the test split for evaluation.
      3. Compute predictions from:
           a. DeterministicFallbackClassifier (baseline)
           b. LightGBMModel (loaded from model_artifacts/)
           c. LogisticRegressionBaseline (loaded from model_artifacts/)
         All three use the IDENTICAL test cases (cohort consistency, EVAL-011).
      4. Print a comparison table to stdout (FR-MODEL-007).
      5. Enforce the quality gate: exit non-zero if any model's Brier score
         exceeds BRIER_SCORE_WARNING_THRESHOLD (FR-MODEL-008, EVAL-004).

    Environment variables read (all optional — safe defaults provided):
      DATASET_SIZE                  int  default 50000
      RANDOM_SEED                   int  default 42
      BRIER_SCORE_WARNING_THRESHOLD float default 0.25
      MODEL_ARTIFACTS_DIR           str  default "model_artifacts"
    """
    # -----------------------------------------------------------------------
    # Configuration from environment
    # -----------------------------------------------------------------------
    dataset_size = int(os.environ.get("DATASET_SIZE", "50000"))
    random_seed = int(os.environ.get("RANDOM_SEED", "42"))
    brier_threshold = float(
        os.environ.get("BRIER_SCORE_WARNING_THRESHOLD", "0.25")
    )
    artifacts_dir = Path(os.environ.get("MODEL_ARTIFACTS_DIR", "model_artifacts"))

    print("=" * 65)
    print("Averiq — make evaluate")
    print("=" * 65)
    print(f"  DATASET_SIZE                    : {dataset_size:,}")
    print(f"  RANDOM_SEED                     : {random_seed}")
    print(f"  BRIER_SCORE_WARNING_THRESHOLD   : {brier_threshold}")
    print(f"  MODEL_ARTIFACTS_DIR             : {artifacts_dir}")

    # -----------------------------------------------------------------------
    # Step 1: Generate dataset (same seed = same cohort as training)
    # Cohort consistency (EVAL-011): all models are evaluated on the same
    # test cases, same eligibility criteria, same outcome definition, and the
    # same observation window.
    # -----------------------------------------------------------------------
    from ai.data.generator import generate
    from ai.training.feature_extraction import (
        extract_features_from_synthetic,
        extract_feature_matrix,
        temporal_split,
    )

    print("\nStep 1/4  Generating evaluation dataset...")
    cases = generate(seed=random_seed, count=dataset_size)
    train_cases, val_cases, test_cases = temporal_split(cases)
    print(
        f"          Total: {len(cases):,} cases  |  "
        f"train: {len(train_cases):,}  val: {len(val_cases):,}  "
        f"test: {len(test_cases):,}"
    )

    if not test_cases:
        print(
            "ERROR: Test split is empty — cannot evaluate. "
            "Increase DATASET_SIZE or adjust split boundaries.",
            file=sys.stderr,
        )
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Step 2: Extract features on the shared test cohort
    # This single extraction is used by ALL models, guaranteeing that every
    # model is evaluated on exactly the same feature vectors and labels.
    # -----------------------------------------------------------------------
    print("Step 2/4  Extracting features on shared test cohort...")
    X_test, y_test = extract_feature_matrix(test_cases)
    n_test = len(y_test)
    n_pos = sum(y_test)
    print(
        f"          {n_test:,} test cases  |  "
        f"{n_pos:,} positive ({n_pos / n_test * 100:.1f}%)"
    )

    # -----------------------------------------------------------------------
    # Step 3: Compute predictions for each model
    # All models operate on the same test_cases list (cohort consistency).
    # -----------------------------------------------------------------------
    print("Step 3/4  Computing model predictions...")

    all_metrics: dict[str, dict] = {}

    # --- Deterministic Fallback baseline ---
    _evaluate_deterministic_baseline(test_cases, y_test, all_metrics)

    # --- LightGBM primary model ---
    lgbm_path = artifacts_dir / f"lgbm_{random_seed}_{dataset_size}.pkl"
    _evaluate_lgbm(lgbm_path, test_cases, y_test, all_metrics)

    # --- Logistic Regression baseline ---
    lr_path = artifacts_dir / f"lr_{random_seed}_{dataset_size}.pkl"
    _evaluate_lr(lr_path, test_cases, y_test, all_metrics)

    # -----------------------------------------------------------------------
    # Step 4: Print comparison table (FR-MODEL-007)
    # The table is ALWAYS printed, even if a quality gate failure follows.
    # -----------------------------------------------------------------------
    print("\nStep 4/4  Evaluation results")
    _print_comparison_table(all_metrics)

    # -----------------------------------------------------------------------
    # Quality gate (FR-MODEL-008, EVAL-004)
    # Emit structured warnings and exit non-zero if any model fails.
    # The comparison table was already printed before this check.
    # -----------------------------------------------------------------------
    quality_gate_failures: list[str] = []
    for model_name, metrics in all_metrics.items():
        brier = metrics.get("brier_score")
        if isinstance(brier, float) and brier > brier_threshold:
            quality_gate_failures.append(
                f"{model_name}: Brier score {brier:.4f} > threshold {brier_threshold}"
            )

    if quality_gate_failures:
        print("\n\u26a0  QUALITY GATE FAILURES:")
        for failure in quality_gate_failures:
            print(f"   - {failure}")
        print(f"\n   Threshold: BRIER_SCORE_WARNING_THRESHOLD={brier_threshold}")
        print(
            "   Retrain the model or adjust the threshold before "
            "promoting to active.\n"
        )
        sys.exit(1)  # non-zero on quality gate failure (FR-MODEL-008, EVAL-004)
    else:
        print(
            "\u2713  Quality gate passed "
            f"(all Brier scores \u2264 {brier_threshold})\n"
        )


# ---------------------------------------------------------------------------
# Private evaluation helpers
# ---------------------------------------------------------------------------


def _evaluate_deterministic_baseline(
    test_cases: list,
    y_test: list[int],
    all_metrics: dict[str, dict],
) -> None:
    """Evaluate the DeterministicFallbackClassifier on the test cohort."""
    from backend.decision_engine.fallback_classifier import DeterministicFallbackClassifier

    clf = DeterministicFallbackClassifier()
    baseline_proba: list[float] = []
    for case in test_cases:
        result = clf.classify(error_code=case.failure_category)
        baseline_proba.append(result.confidence_score)

    all_metrics["DeterministicFallback"] = compute_metrics(y_test, baseline_proba)
    print("          DeterministicFallback: done")


def _evaluate_lgbm(
    lgbm_path: Path,
    test_cases: list,
    y_test: list[int],
    all_metrics: dict[str, dict],
) -> None:
    """Evaluate the LightGBM model on the test cohort."""
    from ai.training.feature_extraction import extract_features_from_synthetic

    if not lgbm_path.exists():
        print(
            f"  WARNING: LightGBM artifact not found at {lgbm_path}. "
            "Run 'make train' first.",
            file=sys.stderr,
        )
        all_metrics["LightGBM"] = {"note": "artifact not found — run make train"}
        return

    try:
        from ai.models.lightgbm_model import LightGBMModel

        lgbm = LightGBMModel.load(lgbm_path)
        features_test = [extract_features_from_synthetic(c) for c in test_cases]
        lgbm_proba = lgbm.predict_batch(features_test)
        all_metrics["LightGBM"] = compute_metrics(y_test, lgbm_proba)
        print("          LightGBM: done")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARNING: LightGBM load/predict failed: {exc}", file=sys.stderr)
        all_metrics["LightGBM"] = {"error": str(exc)}


def _evaluate_lr(
    lr_path: Path,
    test_cases: list,
    y_test: list[int],
    all_metrics: dict[str, dict],
) -> None:
    """Evaluate the Logistic Regression model on the test cohort."""
    from ai.training.feature_extraction import extract_features_from_synthetic

    if not lr_path.exists():
        print(
            f"  WARNING: LR artifact not found at {lr_path}. "
            "Run 'make train' first.",
            file=sys.stderr,
        )
        all_metrics["LogisticRegression"] = {
            "note": "artifact not found — run make train"
        }
        return

    try:
        from ai.models.logistic_regression import LogisticRegressionBaseline

        lr = LogisticRegressionBaseline.load(lr_path)
        features_test = [extract_features_from_synthetic(c) for c in test_cases]
        lr_proba = lr.predict_batch(features_test)
        all_metrics["LogisticRegression"] = compute_metrics(y_test, lr_proba)
        print("          LogisticRegression: done")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARNING: LR load/predict failed: {exc}", file=sys.stderr)
        all_metrics["LogisticRegression"] = {"error": str(exc)}


def _print_comparison_table(all_metrics: dict[str, dict]) -> None:
    """Print the model comparison table to stdout (FR-MODEL-007).

    The table is always printed, even when models are unavailable or when
    the quality gate subsequently fails.  This ensures operators can always
    see what metrics were computed.

    Columns: Model | Precision | Recall | F1 | AUC-ROC | Brier
    """
    print("\n" + "=" * 70)
    print(
        f"{'Model':<26} {'Precision':>9} {'Recall':>8} "
        f"{'F1':>8} {'AUC-ROC':>9} {'Brier':>7}"
    )
    print("-" * 70)

    for model_name, metrics in all_metrics.items():
        # Model not available (artifact missing or error)
        if "error" in metrics or "note" in metrics:
            reason = metrics.get("error") or metrics.get("note", "N/A")
            # Truncate long error messages for table readability
            reason_short = reason[:38] + "…" if len(reason) > 40 else reason
            print(f"{model_name:<26}  [{reason_short}]")
            continue

        prec = metrics.get("precision", "N/A")
        rec = metrics.get("recall", "N/A")
        f1 = metrics.get("f1", "N/A")
        auc = metrics.get("auc_roc", "N/A")
        brier = metrics.get("brier_score", "N/A")

        print(
            f"{model_name:<26} {str(prec):>9} {str(rec):>8} "
            f"{str(f1):>8} {str(auc):>9} {str(brier):>7}"
        )

    print("=" * 70)
    print()
    print(
        "Primary metric : Incremental_Recovered_Revenue (see make simulate)"
    )
    print("Calibration    : Brier score (lower is better)")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
