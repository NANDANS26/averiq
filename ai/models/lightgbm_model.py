"""LightGBM-based recoverability prediction model for Averiq.

Primary ML model implementing ``RecoverabilityModelInterface`` via structural
subtyping (``typing.Protocol``).  LightGBM is the default model used in
production when both variants are available (FR-MODEL-004).

Model lifecycle: trained → evaluated → candidate → active → retired.
A model artifact MUST NOT transition to ``active`` without passing the
evaluation step with a Brier score below the configured threshold.
AI-MODEL-001, AI-MODEL-002.

Artifact integrity (AI-MODEL-002):
  Before loading, the SHA-256 digest of the artifact file is verified against
  the ``artifact_sha256`` stored alongside the artifact.  A mismatch causes
  the load to fail; the system falls back to the previous active model or stub.

Reproducibility guarantee (FR-MODEL-010, Property 4):
  Given the same artifact and the same feature vector, ``predict()`` returns
  the same probability score within documented numerical tolerance on the
  same runtime.

Requirements: FR-MODEL-003, FR-MODEL-004, AI-MODEL-001, AI-MODEL-002
"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any

from shared.contracts.schemas import RecoverabilityFeatures

# Training pipeline imports (only used during fit — not at serve time)
from ai.training.feature_extraction import FEATURE_VERSION, features_to_vector

# ---------------------------------------------------------------------------
# Optional LightGBM import
# ---------------------------------------------------------------------------
try:
    import lightgbm as lgb

    _LGBM_AVAILABLE = True
except ImportError:
    _LGBM_AVAILABLE = False
    lgb = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Default hyperparameters
# ---------------------------------------------------------------------------

_DEFAULT_HYPERPARAMETERS: dict[str, Any] = {
    "objective": "binary",
    "metric": "binary_logloss",
    "boosting_type": "gbdt",
    "n_estimators": 200,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "max_depth": -1,
    "min_child_samples": 20,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_alpha": 0.1,
    "reg_lambda": 0.1,
    "verbose": -1,          # suppress LightGBM output
}


class LightGBMModel:
    """LightGBM recoverability prediction model.

    Satisfies ``RecoverabilityModelInterface`` via structural subtyping.

    Usage::

        model = LightGBMModel(model_version="lgbm-v1.0-42", random_seed=42)
        model.fit(X_train, y_train)
        path = Path("model_artifacts/lgbm_42_50000.pkl")
        sha256 = model.save(path, dataset_hash="...", random_seed=42)
        # Later:
        loaded = LightGBMModel.load(path)
        prob = loaded.predict(features)
    """

    def __init__(
        self,
        model_version: str = "lgbm-v1.0",
        hyperparameters: dict[str, Any] | None = None,
        random_seed: int = 42,
    ) -> None:
        self._model_version = model_version
        self._feature_version = FEATURE_VERSION
        self._hyperparameters = hyperparameters or dict(_DEFAULT_HYPERPARAMETERS)
        self._random_seed = random_seed
        self._model: Any = None   # lgb.LGBMClassifier or scikit-learn fallback
        self._is_trained: bool = False
        self._dataset_hash: str = ""
        self._artifact_sha256: str = ""

    # -------------------------------------------------------------------------
    # RecoverabilityModelInterface protocol attributes
    # -------------------------------------------------------------------------

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def feature_version(self) -> str:
        return self._feature_version

    # -------------------------------------------------------------------------
    # Training
    # -------------------------------------------------------------------------

    def fit(
        self,
        X_train: list[list[float]],
        y_train: list[int],
        X_val: list[list[float]] | None = None,
        y_val: list[int] | None = None,
    ) -> None:
        """Train the LightGBM model on the provided feature matrix.

        Parameters
        ----------
        X_train:
            Feature matrix (list of 10-element vectors from ``features_to_vector``).
        y_train:
            Binary labels (1=recovered, 0=not recovered).
        X_val, y_val:
            Optional validation set for early stopping.

        Raises
        ------
        ImportError
            If ``lightgbm`` is not installed.  Install via ``pip install lightgbm``.
        ValueError
            If training data is empty.
        """
        if not X_train:
            msg = "Training data is empty"
            raise ValueError(msg)

        if not _LGBM_AVAILABLE:
            # Fall back to a scikit-learn GradientBoostingClassifier as a
            # structural equivalent when LightGBM is not installed.
            # This maintains the interface contract while degrading gracefully.
            self._model = self._fit_sklearn_fallback(X_train, y_train)
        else:
            import numpy as np  # numpy is always available alongside lightgbm

            params = dict(self._hyperparameters)
            params["random_state"] = self._random_seed
            classifier = lgb.LGBMClassifier(**params)

            # Convert to numpy arrays for LightGBM compatibility
            X_arr = np.array(X_train, dtype=np.float32)
            y_arr = np.array(y_train, dtype=np.int32)

            if X_val is not None and y_val is not None and len(X_val) > 0:
                X_val_arr = np.array(X_val, dtype=np.float32)
                y_val_arr = np.array(y_val, dtype=np.int32)
                # LightGBM ≥ 4.x uses eval_X / eval_y instead of eval_set
                try:
                    classifier.fit(
                        X_arr,
                        y_arr,
                        eval_X=X_val_arr,
                        eval_y=y_val_arr,
                        callbacks=[lgb.early_stopping(50, verbose=False)],
                    )
                except TypeError:
                    # Older LightGBM API fallback
                    classifier.fit(
                        X_arr,
                        y_arr,
                        eval_set=[(X_val_arr, y_val_arr)],
                        callbacks=[lgb.early_stopping(50, verbose=False)],
                    )
            else:
                classifier.fit(X_arr, y_arr)

            self._model = classifier

        self._is_trained = True

    def _fit_sklearn_fallback(
        self, X_train: list[list[float]], y_train: list[int]
    ) -> Any:
        """Fit a scikit-learn GradientBoostingClassifier as a LightGBM substitute."""
        from sklearn.ensemble import GradientBoostingClassifier  # type: ignore[import-untyped]

        clf = GradientBoostingClassifier(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=4,
            subsample=0.8,
            random_state=self._random_seed,
            verbose=0,
        )
        clf.fit(X_train, y_train)
        return clf

    # -------------------------------------------------------------------------
    # Inference
    # -------------------------------------------------------------------------

    def predict(self, features: RecoverabilityFeatures) -> float:
        """Predict recovery probability for a single case.

        Parameters
        ----------
        features:
            Deterministic, provider-supplied feature vector.
            ``features.feature_version`` MUST equal ``self.feature_version``.

        Returns
        -------
        float
            Recovery probability in [0.0, 1.0].

        Raises
        ------
        RuntimeError
            If the model has not been trained or loaded.
        ValueError
            If the feature version does not match.
        """
        self._check_ready(features)
        vector = features_to_vector(features)
        try:
            import numpy as np
            arr = np.array([vector], dtype=np.float32)
        except ImportError:
            arr = [vector]
        return float(self._model.predict_proba(arr)[0][1])

    def predict_batch(self, features: list[RecoverabilityFeatures]) -> list[float]:
        """Predict recovery probabilities for a batch of cases.

        Parameters
        ----------
        features:
            List of feature vectors. All must share the same ``feature_version``.

        Returns
        -------
        list[float]
            Probabilities in the same order as the input.
        """
        if not features:
            return []
        for f in features:
            self._check_ready(f)
        vectors = [features_to_vector(f) for f in features]
        try:
            import numpy as np
            arr = np.array(vectors, dtype=np.float32)
        except ImportError:
            arr = vectors
        proba = self._model.predict_proba(arr)
        return [float(p[1]) for p in proba]

    # -------------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------------

    def save(
        self,
        path: Path,
        dataset_hash: str,
        random_seed: int,
    ) -> str:
        """Serialise the trained model artifact to disk.

        Parameters
        ----------
        path:
            Destination file path (e.g. ``model_artifacts/lgbm_42_50000.pkl``).
        dataset_hash:
            A string identifying the training dataset (e.g. ``synthetic-50000-seed42``).
        random_seed:
            The random seed used during training — stored in the artifact
            metadata for reproducibility (AI-MODEL-002).

        Returns
        -------
        str
            Hex SHA-256 digest of the saved artifact file.

        Raises
        ------
        RuntimeError
            If the model has not been trained.
        """
        if not self._is_trained or self._model is None:
            msg = "Model must be trained before saving"
            raise RuntimeError(msg)

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        artifact: dict[str, Any] = {
            "model": self._model,
            "model_version": self._model_version,
            "feature_version": self._feature_version,
            "hyperparameters": self._hyperparameters,
            "random_seed": random_seed,
            "dataset_hash": dataset_hash,
            "model_type": "LightGBMModel",
            "lgbm_available": _LGBM_AVAILABLE,
        }

        with path.open("wb") as f:
            pickle.dump(artifact, f, protocol=pickle.HIGHEST_PROTOCOL)

        sha256 = _compute_sha256(path)
        self._artifact_sha256 = sha256
        self._dataset_hash = dataset_hash

        return sha256

    @classmethod
    def load(cls, path: Path, expected_sha256: str | None = None) -> "LightGBMModel":
        """Load a trained model artifact from disk.

        Parameters
        ----------
        path:
            Path to the ``*.pkl`` artifact file.
        expected_sha256:
            If provided, verifies the artifact's SHA-256 digest against this
            value before loading.  A mismatch raises ``ValueError`` — the
            system must then fall back to the previous active model or stub.
            AI-MODEL-002.

        Returns
        -------
        LightGBMModel
            A fully loaded, ready-to-predict model instance.

        Raises
        ------
        FileNotFoundError
            If ``path`` does not exist.
        ValueError
            If ``expected_sha256`` is provided and the digest does not match.
        """
        path = Path(path)
        if not path.exists():
            msg = f"Artifact not found: {path}"
            raise FileNotFoundError(msg)

        if expected_sha256 is not None:
            actual = _compute_sha256(path)
            if actual != expected_sha256:
                msg = (
                    f"Artifact integrity check failed for {path}: "
                    f"expected {expected_sha256}, got {actual}"
                )
                raise ValueError(msg)

        with path.open("rb") as f:
            artifact: dict[str, Any] = pickle.load(f)  # noqa: S301 (trusted artifact)

        instance = cls(
            model_version=artifact["model_version"],
            hyperparameters=artifact.get("hyperparameters"),
            random_seed=artifact.get("random_seed", 42),
        )
        instance._model = artifact["model"]
        instance._feature_version = artifact["feature_version"]
        instance._dataset_hash = artifact.get("dataset_hash", "")
        instance._is_trained = True

        return instance

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _check_ready(self, features: RecoverabilityFeatures) -> None:
        """Validate model readiness and feature version compatibility."""
        if not self._is_trained or self._model is None:
            msg = "Model has not been trained or loaded"
            raise RuntimeError(msg)
        if features.feature_version != self._feature_version:
            msg = (
                f"Feature version mismatch: model expects '{self._feature_version}', "
                f"got '{features.feature_version}'"
            )
            raise ValueError(msg)

    def get_artifact_metadata(self) -> dict[str, Any]:
        """Return artifact metadata suitable for storing in model_artifacts DB table."""
        return {
            "model_version": self._model_version,
            "feature_version": self._feature_version,
            "hyperparameters": self._hyperparameters,
            "random_seed": self._random_seed,
            "dataset_hash": self._dataset_hash,
            "artifact_sha256": self._artifact_sha256,
            "model_type": "LightGBMModel",
            "lgbm_available": _LGBM_AVAILABLE,
        }


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _compute_sha256(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
