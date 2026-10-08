"""Training pipeline for ActionValueEstimator on Dataset 2.0.

Trains the 4 action-specific LightGBM models on the temporal train split
and fits Isotonic Regression calibrators on the validation split.

Persists:
  model_artifacts/action_value_v3.pkl
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np
import pandas as pd

from ai.data.v2.features import extract_v2_feature_matrix
from ai.models.action_value_model import ActionValueEstimator


def train_action_value_model(
    parquet_path: Path = Path("data/v2/dataset_20k.parquet"),
    output_path: Path = Path("model_artifacts/action_value_v3.pkl"),
) -> str:
    print(f"Loading benchmark dataset from {parquet_path}...")
    df = pd.read_parquet(parquet_path)

    train_df = df[df["split"] == "train"]
    val_df = df[df["split"] == "val"]

    X_train, y_train = extract_v2_feature_matrix(train_df.to_dict(orient="records"))
    X_val, y_val = extract_v2_feature_matrix(val_df.to_dict(orient="records"))

    X_train_arr = np.array(X_train, dtype=np.float32)
    y_train_arr = np.array(y_train, dtype=np.int32)
    X_val_arr = np.array(X_val, dtype=np.float32)
    y_val_arr = np.array(y_val, dtype=np.int32)

    train_treatments = train_df["intervention_assigned"].tolist()
    val_treatments = val_df["intervention_assigned"].tolist()

    print("Fitting ActionValueEstimator T-Learners...")
    estimator = ActionValueEstimator(model_version="action-value-v3.0-calibrated", random_seed=42)
    estimator.fit(
        X_train=X_train_arr,
        train_treatments=train_treatments,
        y_train=y_train_arr,
        X_val=X_val_arr,
        val_treatments=val_treatments,
        y_val=y_val_arr,
    )

    sha256 = estimator.save(output_path)
    print(f"Saved ActionValueEstimator artifact to {output_path}")
    print(f"SHA-256 Digest: {sha256}")
    return sha256


if __name__ == "__main__":
    train_action_value_model()
