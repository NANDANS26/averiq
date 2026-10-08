"""Action-Value Estimators for Averiq Context-Aware Intervention Decision Engine.

Estimates P(recovery | X, action) for all supported recovery interventions:
  - NO_INTERVENTION (Baseline organic recovery)
  - PAYMENT_LINK
  - RETRY
  - CUSTOMER_ACTION

Uses a T-Learner architecture (separate LightGBM classifiers per intervention)
calibrated using Isotonic Regression on the validation split.

Guarantees:
  1. Whitelist-only feature consumption (no target, no counterfactual, no post-outcome fields).
  2. Zero float arithmetic on stored financial values (uses integer paise).
  3. Strict isolation from PolicyEngine and ExecutionEngine.
"""
from __future__ import annotations

import hashlib
import pickle
from pathlib import Path
from typing import Any, Final

import numpy as np
import lightgbm as lgb
from sklearn.isotonic import IsotonicRegression

from ai.data.v2.features import (
    FEATURE_NAMES_V2,
    FEATURE_VERSION_V2,
    extract_v2_feature_matrix,
    record_to_vector_v2,
)
from shared.contracts.enums import InterventionType
from shared.contracts.schemas import RecoverabilityFeatures

MODEL_VERSION_V3: Final[str] = "action-value-v3.0-calibrated"
DEFAULT_INTERVENTION_COSTS_PAISE: Final[dict[str, int]] = {
    "NO_INTERVENTION": 0,
    InterventionType.STOP.value: 0,
    InterventionType.PAYMENT_LINK.value: 500,     # ₹5.00
    InterventionType.RETRY.value: 100,            # ₹1.00
    InterventionType.CUSTOMER_ACTION.value: 200,   # ₹2.00
}


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class ActionValueEstimator:
    """Multi-action probability estimator implementing T-Learner with Isotonic calibration."""

    def __init__(
        self,
        model_version: str = MODEL_VERSION_V3,
        feature_version: str = FEATURE_VERSION_V2,
        random_seed: int = 42,
    ) -> None:
        self.model_version = model_version
        self.feature_version = feature_version
        self.random_seed = random_seed
        self.supported_actions = [
            "NO_INTERVENTION",
            "PAYMENT_LINK",
            "RETRY",
            "CUSTOMER_ACTION",
        ]
        self._models: dict[str, lgb.LGBMClassifier] = {}
        self._calibrators: dict[str, IsotonicRegression] = {}
        self._is_trained = False

    def fit(
        self,
        X_train: np.ndarray,
        train_treatments: list[str],
        y_train: np.ndarray,
        X_val: np.ndarray,
        val_treatments: list[str],
        y_val: np.ndarray,
    ) -> None:
        """Fit an action-specific estimator for each treatment."""
        train_treatments_arr = np.array(train_treatments)
        val_treatments_arr = np.array(val_treatments)

        for action in self.supported_actions:
            mask_tr = train_treatments_arr == action
            mask_va = val_treatments_arr == action

            if not np.any(mask_tr) or not np.any(mask_va):
                continue

            clf = lgb.LGBMClassifier(
                n_estimators=100,
                learning_rate=0.04,
                num_leaves=31,
                max_depth=5,
                subsample=0.85,
                colsample_bytree=0.85,
                random_state=self.random_seed,
                verbose=-1,
            )
            clf.fit(X_train[mask_tr], y_train[mask_tr])
            self._models[action] = clf

            val_preds = clf.predict_proba(X_val[mask_va])[:, 1]
            iso = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
            iso.fit(val_preds, y_val[mask_va])
            self._calibrators[action] = iso

        self._is_trained = True

    def predict_action_probabilities(
        self,
        features: RecoverabilityFeatures | dict[str, Any] | list[float],
    ) -> dict[str, float]:
        """Predict calibrated P(recovery | X, action) for all supported actions."""
        if isinstance(features, RecoverabilityFeatures):
            vec = record_to_vector_v2({
                "amount_paise": features.amount_paise,
                "currency": features.currency,
                "failure_category": features.failure_category.value,
                "payment_method": features.payment_method,
                "time_since_failure_seconds": features.time_since_failure_seconds,
                "hour_of_day": features.hour_of_day,
                "day_of_week": features.day_of_week,
                "customer_historical_failure_count": features.previous_failure_count,
                "is_subscription": features.has_subscription,
                "subscription_billing_amount_paise": features.subscription_billing_amount_paise,
            })
            arr = np.array([vec], dtype=np.float32)
        elif isinstance(features, dict):
            vec = record_to_vector_v2(features)
            arr = np.array([vec], dtype=np.float32)
        elif isinstance(features, (list, np.ndarray)):
            arr = np.array([features], dtype=np.float32)
        else:
            raise TypeError(f"Unsupported features type: {type(features)}")

        probs: dict[str, float] = {}
        for action in self.supported_actions:
            if action in self._models and action in self._calibrators:
                raw_p = self._models[action].predict_proba(arr)[:, 1]
                cal_p = float(np.clip(self._calibrators[action].predict(raw_p), 0.0, 1.0)[0])
                probs[action] = round(cal_p, 4)
            else:
                probs[action] = 0.35

        return probs

    def save(self, path: Path) -> str:
        """Persist estimators and metadata to disk."""
        if not self._is_trained:
            raise RuntimeError("Estimator must be trained before saving")

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        artifact = {
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "random_seed": self.random_seed,
            "supported_actions": self.supported_actions,
            "models": self._models,
            "calibrators": self._calibrators,
        }

        with path.open("wb") as f:
            pickle.dump(artifact, f, protocol=pickle.HIGHEST_PROTOCOL)

        return _compute_sha256(path)

    @classmethod
    def load(cls, path: Path, expected_sha256: str | None = None) -> "ActionValueEstimator":
        """Load artifact and verify integrity."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Action value artifact not found at {path}")

        if expected_sha256 is not None:
            actual = _compute_sha256(path)
            if actual != expected_sha256:
                raise ValueError(f"Integrity check failed: expected {expected_sha256}, got {actual}")

        with path.open("rb") as f:
            artifact = pickle.load(f)

        instance = cls(
            model_version=artifact["model_version"],
            feature_version=artifact["feature_version"],
            random_seed=artifact["random_seed"],
        )
        instance.supported_actions = artifact["supported_actions"]
        instance._models = artifact["models"]
        instance._calibrators = artifact["calibrators"]
        instance._is_trained = True
        return instance
