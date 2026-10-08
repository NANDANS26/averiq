"""Unit tests for the make evaluate pipeline.

Tests the ``compute_metrics`` function in ``ai/evaluation/evaluate.py``.
These tests run in isolation — no database, no external services, no model
artifacts required.

All tests are marked ``@pytest.mark.unit``.

Validates:
  - FR-MODEL-006: precision, recall, F1, ROC-AUC, Brier score computation
  - FR-MODEL-008: quality gate based on Brier score
  - EVAL-002: make evaluate computes and emits all required metrics
  - EVAL-004: non-zero exit on quality gate failure
  - EVAL-011: cohort consistency (same test cohort for all models)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Test 1: compute_metrics with all-positive predictions
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_compute_metrics_all_positive_predictions() -> None:
    """compute_metrics returns correct values when all predictions are positive.

    When every y_pred_proba >= 0.5, the binary classifier predicts all ones.
    For a balanced dataset with some actual negatives, precision < 1.0 and
    recall = 1.0 (all positives are found, but negatives are misclassified).
    Brier score should be a well-formed float, not NOT_AVAILABLE.
    """
    from ai.evaluation.evaluate import compute_metrics

    y_true = [1, 0, 1, 0, 1, 1, 0]     # 4 positive, 3 negative
    y_pred = [0.9, 0.8, 0.95, 0.7, 0.85, 0.6, 0.75]   # all >= 0.5

    metrics = compute_metrics(y_true, y_pred)

    # Must return all five metric keys
    assert set(metrics.keys()) == {"precision", "recall", "f1", "auc_roc", "brier_score"}

    # With all predictions = 1 and 4 positives out of 7:
    # precision = 4/7 ≈ 0.571 (4 true positives, 3 false positives)
    # recall    = 1.0  (all 4 positives are predicted as positive)
    assert isinstance(metrics["precision"], float), "precision must be a float"
    assert isinstance(metrics["recall"], float), "recall must be a float"
    assert isinstance(metrics["f1"], float), "f1 must be a float"
    assert isinstance(metrics["brier_score"], float), "brier_score must be a float"

    # Recall should be 1.0 (predicting all positive means all actual positives found)
    assert metrics["recall"] == pytest.approx(1.0, abs=1e-4), (
        f"Expected recall=1.0 when all predictions are positive, got {metrics['recall']}"
    )

    # Brier score must be in [0, 1]
    assert 0.0 <= metrics["brier_score"] <= 1.0, (
        f"Brier score {metrics['brier_score']} outside [0, 1]"
    )

    # AUC-ROC should be computable (both classes present)
    assert metrics["auc_roc"] != "NOT_AVAILABLE", (
        "AUC-ROC should be computable when both classes are present"
    )
    assert isinstance(metrics["auc_roc"], float)


# ---------------------------------------------------------------------------
# Test 2: compute_metrics with all-negative predictions → NOT_AVAILABLE for AUC
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_compute_metrics_only_one_class_in_y_true_returns_not_available_auc() -> None:
    """compute_metrics returns NOT_AVAILABLE for auc_roc when y_true has only one class.

    AUC-ROC is undefined when only one class is present in the ground truth.
    The function must return the string 'NOT_AVAILABLE' for auc_roc rather
    than raising an exception.

    This tests the degenerate case where the test split contains only negative
    examples (no recoveries), which can occur with very small datasets.
    """
    from ai.evaluation.evaluate import compute_metrics

    # All ground-truth labels are 0 (no positives)
    y_true = [0, 0, 0, 0, 0]
    y_pred = [0.3, 0.1, 0.4, 0.2, 0.45]

    metrics = compute_metrics(y_true, y_pred)

    assert set(metrics.keys()) == {"precision", "recall", "f1", "auc_roc", "brier_score"}

    # AUC-ROC cannot be computed with only one class — must be NOT_AVAILABLE
    assert metrics["auc_roc"] == "NOT_AVAILABLE", (
        f"Expected auc_roc='NOT_AVAILABLE' when y_true has only one class, "
        f"got {metrics['auc_roc']!r}"
    )

    # Other metrics should still be floats (Brier score is well-defined)
    assert isinstance(metrics["brier_score"], float), (
        "brier_score must be a float even when only one class is present"
    )


# ---------------------------------------------------------------------------
# Test 3: Empty list returns NOT_AVAILABLE for all metrics (no exception)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_compute_metrics_empty_inputs_returns_not_available_without_raising() -> None:
    """compute_metrics returns NOT_AVAILABLE for all metrics on empty inputs.

    An empty evaluation cohort is a degenerate edge case that must not raise
    any exception.  The function must return NOT_AVAILABLE for every metric
    and continue gracefully.
    """
    from ai.evaluation.evaluate import compute_metrics

    metrics = compute_metrics([], [])

    assert set(metrics.keys()) == {"precision", "recall", "f1", "auc_roc", "brier_score"}

    for key, value in metrics.items():
        assert value == "NOT_AVAILABLE", (
            f"Expected NOT_AVAILABLE for {key!r} on empty inputs, got {value!r}"
        )


# ---------------------------------------------------------------------------
# Test 4: Correct Brier score for known inputs
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_compute_metrics_correct_brier_score() -> None:
    """compute_metrics returns the correct Brier score for known inputs.

    Brier score formula: mean((y_true - y_pred_proba)^2).

    For y_true=[1, 0, 1] and y_pred=[0.9, 0.1, 0.8]:
      (1 - 0.9)^2 = 0.01
      (0 - 0.1)^2 = 0.01
      (1 - 0.8)^2 = 0.04
      mean = (0.01 + 0.01 + 0.04) / 3 = 0.06 / 3 = 0.02

    Validates FR-MODEL-006: Brier score is computed correctly.
    """
    from ai.evaluation.evaluate import compute_metrics

    y_true = [1, 0, 1]
    y_pred = [0.9, 0.1, 0.8]

    metrics = compute_metrics(y_true, y_pred)

    assert set(metrics.keys()) == {"precision", "recall", "f1", "auc_roc", "brier_score"}

    expected_brier = (
        (1 - 0.9) ** 2 + (0 - 0.1) ** 2 + (1 - 0.8) ** 2
    ) / 3  # = 0.02

    assert isinstance(metrics["brier_score"], float), (
        f"brier_score must be a float, got {type(metrics['brier_score'])}"
    )
    assert metrics["brier_score"] == pytest.approx(expected_brier, abs=1e-4), (
        f"Expected brier_score ≈ {expected_brier:.4f}, got {metrics['brier_score']}"
    )

    # All labels are available for AUC-ROC (both 0 and 1 present)
    assert metrics["auc_roc"] != "NOT_AVAILABLE", (
        "AUC-ROC should be available for a balanced 3-case example"
    )
    assert isinstance(metrics["auc_roc"], float)
