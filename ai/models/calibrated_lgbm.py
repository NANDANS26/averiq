"""Calibrated LightGBM Recoverability Model for Averiq Dataset 2.0.

Provides:
  1. Base LightGBM classifier with tuned hyperparameters.
  2. Post-hoc probability calibration (Platt scaling via CalibratedClassifierCV
     or IsotonicRegression fit on validation split).
  3. Adherence to RecoverabilityModelInterface protocol.
  4. Cryptographic integrity checking (SHA-256).
  5. Full explainability: feature importances (gain & split).
"""
from __future__ import annotations

import hashlib
import pickle
from pathlib import Path
from typing import Any, Final

import lightgbm as lgb
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.isotonic import IsotonicRegression

from ai.data.v2.features import (
    FEATURE_NAMES_V2,
    FEATURE_VERSION_V2,
    record_to_features,
    record_to_vector_v2,
)
from shared.contracts.schemas import RecoverabilityFeatures


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


DEFAULT_LGBM_V2_PARAMS: Final[dict[str, Any]] = {
    "objective": "binary",
    "metric": "binary_logloss",
    "boosting_type": "gbdt",
    "n_estimators": 250,
    "learning_rate": 0.04,
    "num_leaves": 31,
    "max_depth": 6,
    "min_child_samples": 25,
    "subsample": 0.85,
    "colsample_bytree": 0.85,
    "reg_alpha": 0.1,
    "reg_lambda": 0.2,
    "random_state": 42,
    "verbose": -1,
}


class CalibratedLightGBMModel:
    """Production-grade Calibrated LightGBM model for Dataset 2.0."""

    def __init__(
        self,
        model_version: str = "lgbm-v2.0-calibrated",
        feature_version: str = FEATURE_VERSION_V2,
        hyperparameters: dict[str, Any] | None = None,
        random_seed: int = 42,
    ) -> None:
        self._model_version = model_version
        self._feature_version = feature_version
        self._hyperparameters = hyperparameters or dict(DEFAULT_LGBM_V2_PARAMS)
        self._random_seed = random_seed
        self._base_model: lgb.LGBMClassifier | None = None
        self._calibrator: IsotonicRegression | None = None
        self._is_trained: bool = False
        self._feature_names = list(FEATURE_NAMES_V2)

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def feature_version(self) -> str:
        return self._feature_version

    @property
    def feature_names(self) -> list[str]:
        return list(self._feature_names)

    def fit(
        self,
        X_train: list[list[float]] | np.ndarray,
        y_train: list[int] | np.ndarray,
        X_val: list[list[float]] | np.ndarray,
        y_val: list[int] | np.ndarray,
    ) -> None:
        """Train base LightGBM on train set, then calibrate probabilities on val set."""
        X_train_arr = np.array(X_train, dtype=np.float32)
        y_train_arr = np.array(y_train, dtype=np.int32)
        X_val_arr = np.array(X_val, dtype=np.float32)
        y_val_arr = np.array(y_val, dtype=np.int32)

        params = dict(self._hyperparameters)
        params["random_state"] = self._random_seed

        classifier = lgb.LGBMClassifier(**params)
        classifier.fit(
            X_train_arr,
            y_train_arr,
            eval_set=[(X_val_arr, y_val_arr)],
            callbacks=[lgb.early_stopping(stopping_rounds=30, verbose=False)],
        )
        self._base_model = classifier

        # Predict raw probabilities on validation set for calibration
        raw_val_probs = classifier.predict_proba(X_val_arr)[:, 1]

        # Fit isotonic regression calibrator on validation split
        iso = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        iso.fit(raw_val_probs, y_val_arr)
        self._calibrator = iso
        self._is_trained = True

    def predict_raw_proba(self, X: np.ndarray) -> np.ndarray:
        """Return uncalibrated probability predictions."""
        if not self._is_trained or self._base_model is None:
            raise RuntimeError("Model is not trained")
        return self._base_model.predict_proba(X)[:, 1]

    def predict_calibrated_proba(self, X: np.ndarray) -> np.ndarray:
        """Return calibrated probability predictions."""
        raw_probs = self.predict_raw_proba(X)
        if self._calibrator is not None:
            return np.clip(self._calibrator.predict(raw_probs), 0.0, 1.0)
        return raw_probs

    def predict(self, features: RecoverabilityFeatures | dict[str, Any]) -> float:
        """Single case prediction complying with RecoverabilityModelInterface."""
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
        else:
            vec = record_to_vector_v2(features)

        arr = np.array([vec], dtype=np.float32)
        prob = float(self.predict_calibrated_proba(arr)[0])
        return prob

    def get_feature_importances(self) -> dict[str, float]:
        """Return normalized feature importances (gain-based)."""
        if not self._is_trained or self._base_model is None:
            raise RuntimeError("Model is not trained")
        raw_importances = self._base_model.booster_.feature_importance(importance_type="gain")
        total = sum(raw_importances) + 1e-9
        return {
            name: round(float(imp / total), 4)
            for name, imp in zip(self._feature_names, raw_importances, strict=True)
        }

    def save(self, path: Path, dataset_hash: str = "") -> str:
        """Save model artifact with metadata and return SHA-256."""
        if not self._is_trained:
            raise RuntimeError("Model must be trained before saving")

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        artifact = {
            "base_model": self._base_model,
            "calibrator": self._calibrator,
            "model_version": self._model_version,
            "feature_version": self._feature_version,
            "feature_names": self._feature_names,
            "hyperparameters": self._hyperparameters,
            "random_seed": self._random_seed,
            "dataset_hash": dataset_hash,
            "model_type": "CalibratedLightGBMModel",
        }

        with path.open("wb") as f:
            pickle.dump(artifact, f, protocol=pickle.HIGHEST_PROTOCOL)

        return _compute_sha256(path)

    @classmethod
    def load(cls, path: Path, expected_sha256: str | None = None) -> "CalibratedLightGBMModel":
        """Load artifact and verify SHA-256 integrity."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Model artifact not found at {path}")

        if expected_sha256 is not None:
            actual_hash = _compute_sha256(path)
            if actual_hash != expected_sha256:
                raise ValueError(f"Integrity check failed: expected {expected_sha256}, got {actual_hash}")

        with path.open("rb") as f:
            artifact = pickle.load(f)

        instance = cls(
            model_version=artifact["model_version"],
            feature_version=artifact.get("feature_version", FEATURE_VERSION_V2),
            hyperparameters=artifact.get("hyperparameters"),
            random_seed=artifact.get("random_seed", 42),
        )
        instance._base_model = artifact["base_model"]
        instance._calibrator = artifact["calibrator"]
        instance._feature_names = artifact.get("feature_names", list(FEATURE_NAMES_V2))
        instance._is_trained = True
        return instance
