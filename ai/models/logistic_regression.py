"""Logistic Regression baseline recoverability model for Averiq.

Comparison baseline implementing ``RecoverabilityModelInterface`` via
structural subtyping (``typing.Protocol``).  Used for model comparison in
``make evaluate`` (FR-MODEL-003, FR-MODEL-007).

Design rationale (per design.md):
  The Logistic Regression baseline is intentionally simple — it provides a
  well-understood linear benchmark that LightGBM must meaningfully outperform.
  Having both models evaluated on the identical test cohort (same cases, same
  features, same observation window) enables a fair comparison.

  Model lifecycle: trained → evaluated → candidate → active → retired.
  FR-MODEL-003 requires both model variants to be trained and compared.

Requirements: FR-MODEL-003, AI-MODEL-001, AI-MODEL-002
"""

from __future__ import annotations

import hashlib
import pickle
from pathlib import Path
from typing import Any

try:
    from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
    from sklearn.pipeline import Pipeline                # type: ignore[import-untyped]
    from sklearn.preprocessing import StandardScaler     # type: ignore[import-untyped]
    HAS_SKLEARN = True
except ImportError:
    LogisticRegression = None  # type: ignore[assignment,misc]
    Pipeline = None            # type: ignore[assignment,misc]
    StandardScaler = None      # type: ignore[assignment,misc]
    HAS_SKLEARN = False

from shared.contracts.schemas import RecoverabilityFeatures

# Training pipeline imports (only used during fit)
from ai.training.feature_extraction import FEATURE_VERSION, features_to_vector


# ---------------------------------------------------------------------------
# Default hyperparameters
# ---------------------------------------------------------------------------

_DEFAULT_HYPERPARAMETERS: dict[str, Any] = {
    "C": 1.0,                    # Regularisation strength (inverse)
    "solver": "lbfgs",           # Works well for binary classification
    "max_iter": 1000,            # Sufficient for convergence on tabular data
    "class_weight": "balanced",  # Handle class imbalance automatically
}


class LogisticRegressionBaseline:
    """Logistic Regression recoverability prediction baseline.

    Satisfies ``RecoverabilityModelInterface`` via structural subtyping.

    The model is wrapped in a ``sklearn.pipeline.Pipeline`` with
    ``StandardScaler`` preprocessing to ensure feature scales do not bias
    the linear classifier.

    Usage::

        model = LogisticRegressionBaseline(model_version="lr-v1.0-42", random_seed=42)
        model.fit(X_train, y_train)
        path = Path("model_artifacts/lr_42_50000.pkl")
        sha256 = model.save(path, dataset_hash="...", random_seed=42)
        # Later:
        loaded = LogisticRegressionBaseline.load(path)
        prob = loaded.predict(features)
    """

    def __init__(
        self,
        model_version: str = "lr-v1.0",
        hyperparameters: dict[str, Any] | None = None,
        random_seed: int = 42,
    ) -> None:
        self._model_version = model_version
        self._feature_version = FEATURE_VERSION
        self._hyperparameters = hyperparameters or dict(_DEFAULT_HYPERPARAMETERS)
        self._random_seed = random_seed
        self._pipeline: Pipeline | None = None
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
        X_val: list[list[float]] | None = None,   # noqa: ARG002  (unused: LR has no early stopping)
        y_val: list[int] | None = None,             # noqa: ARG002
    ) -> None:
        """Train the Logistic Regression pipeline on the provided feature matrix.

        Parameters
        ----------
        X_train:
            Feature matrix (list of 10-element vectors from ``features_to_vector``).
        y_train:
            Binary labels (1=recovered, 0=not recovered).
        X_val, y_val:
            Accepted for interface compatibility but unused — Logistic Regression
            does not support early stopping.

        Raises
        ------
        ValueError
            If training data is empty.
        """
        if not X_train:
            msg = "Training data is empty"
            raise ValueError(msg)

        params = dict(self._hyperparameters)
        params["random_state"] = self._random_seed

        lr = LogisticRegression(**params)
        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("classifier", lr),
        ])
        pipeline.fit(X_train, y_train)
        self._pipeline = pipeline
        self._is_trained = True

    # -------------------------------------------------------------------------
    # Inference
    # -------------------------------------------------------------------------

    def predict(self, features: RecoverabilityFeatures) -> float:
        """Predict recovery probability for a single case.

        If the model has not been trained or loaded, returns the neutral default
        probability of 0.5 (maximum entropy / no information).  This allows the
        model to be used safely as a pure default when no artifact is available.

        Parameters
        ----------
        features:
            Deterministic, provider-supplied feature vector.
            ``features.feature_version`` MUST equal ``self.feature_version``
            when the model is trained; the version check is skipped for
            the untrained default.

        Returns
        -------
        float
            Recovery probability in [0.0, 1.0].  Returns 0.5 when untrained.
        """
        if not self._is_trained or self._pipeline is None:
            return 0.5
        if features.feature_version != self._feature_version:
            msg = (
                f"Feature version mismatch: model expects '{self._feature_version}', "
                f"got '{features.feature_version}'"
            )
            raise ValueError(msg)
        vector = features_to_vector(features)
        return float(self._pipeline.predict_proba([vector])[0][1])

    def predict_batch(self, features: list[RecoverabilityFeatures]) -> list[float]:
        """Predict recovery probabilities for a batch of cases.

        If the model has not been trained or loaded, returns 0.5 for every
        input (neutral default, consistent with ``predict``'s untrained behaviour).

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
        if not self._is_trained or self._pipeline is None:
            return [0.5] * len(features)
        for f in features:
            self._check_ready(f)
        vectors = [features_to_vector(f) for f in features]
        proba = self._pipeline.predict_proba(vectors)
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
            Destination file path (e.g. ``model_artifacts/lr_42_50000.pkl``).
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
        if not self._is_trained or self._pipeline is None:
            msg = "Model must be trained before saving"
            raise RuntimeError(msg)

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        artifact: dict[str, Any] = {
            "pipeline": self._pipeline,
            "model_version": self._model_version,
            "feature_version": self._feature_version,
            "hyperparameters": self._hyperparameters,
            "random_seed": random_seed,
            "dataset_hash": dataset_hash,
            "model_type": "LogisticRegressionBaseline",
        }

        with path.open("wb") as f:
            pickle.dump(artifact, f, protocol=pickle.HIGHEST_PROTOCOL)

        sha256 = _compute_sha256(path)
        self._artifact_sha256 = sha256
        self._dataset_hash = dataset_hash

        return sha256

    @classmethod
    def load(cls, path: Path, expected_sha256: str | None = None) -> "LogisticRegressionBaseline":
        """Load a trained model artifact from disk.

        Parameters
        ----------
        path:
            Path to the ``*.pkl`` artifact file.
        expected_sha256:
            If provided, verifies the artifact's SHA-256 digest against this
            value before loading.  A mismatch raises ``ValueError``.
            AI-MODEL-002.

        Returns
        -------
        LogisticRegressionBaseline
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
        instance._pipeline = artifact["pipeline"]
        instance._feature_version = artifact["feature_version"]
        instance._dataset_hash = artifact.get("dataset_hash", "")
        instance._is_trained = True

        return instance

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _check_ready(self, features: RecoverabilityFeatures) -> None:
        """Validate model readiness and feature version compatibility."""
        if not self._is_trained or self._pipeline is None:
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
            "model_type": "LogisticRegressionBaseline",
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
