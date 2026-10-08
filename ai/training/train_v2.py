"""Dataset 2.0 Multi-Model Training and Calibration Pipeline.

Trains, evaluates, and persists three comparative models on Dataset 2.0:
  1. Logistic Regression Baseline (Linear benchmark)
  2. Uncalibrated LightGBM Classifier (Gradient boosted trees)
  3. Calibrated LightGBM Classifier (Isotonic calibrated probabilities)

Artifacts produced:
  - model_artifacts/lr_v2.pkl
  - model_artifacts/lgbm_v2_raw.pkl
  - model_artifacts/lgbm_v2_calibrated.pkl
  - model_artifacts/training_manifest_v2.json
"""
from __future__ import annotations

import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure project root in sys.path
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ai.data.v2.features import (
    FEATURE_NAMES_V2,
    extract_v2_feature_matrix,
    temporal_split_v2,
)
from ai.data.v2.generator import PaymentEnvironmentSimulator
from ai.models.calibrated_lgbm import CalibratedLightGBMModel, _compute_sha256


def train_v2_models(
    dataset_parquet: Path = Path("data/v2/dataset_20k.parquet"),
    artifacts_dir: Path = Path("model_artifacts"),
) -> dict[str, Any]:
    print("=" * 70)
    print("Averiq Dataset 2.0 — Model Training & Calibration Pipeline")
    print("=" * 70)

    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load Data
    print(f"Loading dataset from: {dataset_parquet}")
    df = pd.read_parquet(dataset_parquet)
    print(f"Loaded {len(df):,} total records")

    train_df = df[df["split"] == "train"].copy()
    val_df = df[df["split"] == "val"].copy()
    test_df = df[df["split"] == "test"].copy()

    print(f"Splits: Train={len(train_df):,}, Val={len(val_df):,}, Test={len(test_df):,}")

    train_records = train_df.to_dict(orient="records")
    val_records = val_df.to_dict(orient="records")
    test_records = test_df.to_dict(orient="records")

    X_train, y_train = extract_v2_feature_matrix(train_records)
    X_val, y_val = extract_v2_feature_matrix(val_records)
    X_test, y_test = extract_v2_feature_matrix(test_records)

    X_train_arr = np.array(X_train, dtype=np.float32)
    y_train_arr = np.array(y_train, dtype=np.int32)
    X_val_arr = np.array(X_val, dtype=np.float32)
    y_val_arr = np.array(y_val, dtype=np.int32)
    X_test_arr = np.array(X_test, dtype=np.float32)
    y_test_arr = np.array(y_test, dtype=np.int32)

    manifest: dict[str, Any] = {
        "pipeline_version": "v2.0",
        "training_timestamp": datetime.now(timezone.utc).isoformat(),
        "dataset_rows": len(df),
        "feature_names": FEATURE_NAMES_V2,
        "models": {},
    }

    # -------------------------------------------------------------------------
    # Model 1: Logistic Regression Baseline
    # -------------------------------------------------------------------------
    print("\n[1/3] Training Logistic Regression Baseline...")
    lr_pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("classifier", LogisticRegression(C=1.0, max_iter=1000, class_weight="balanced", random_state=42)),
    ])
    lr_pipeline.fit(X_train_arr, y_train_arr)
    lr_path = artifacts_dir / "lr_v2.pkl"
    with lr_path.open("wb") as f:
        pickle.dump({
            "model": lr_pipeline,
            "model_version": "lr-v2.0-baseline",
            "feature_version": "v2.0",
            "model_type": "LogisticRegressionBaseline",
        }, f, protocol=pickle.HIGHEST_PROTOCOL)
    lr_sha = _compute_sha256(lr_path)
    print(f"Saved: {lr_path} (SHA-256: {lr_sha[:16]}...)")
    manifest["models"]["logistic_regression"] = {
        "version": "lr-v2.0-baseline",
        "path": str(lr_path),
        "sha256": lr_sha,
    }

    # -------------------------------------------------------------------------
    # Model 2 & 3: Calibrated LightGBM (and raw base)
    # -------------------------------------------------------------------------
    print("\n[2/3 & 3/3] Training LightGBM & Fitting Probability Calibration...")
    cal_model = CalibratedLightGBMModel(
        model_version="lgbm-v2.0-calibrated",
        random_seed=42,
    )
    cal_model.fit(X_train_arr, y_train_arr, X_val_arr, y_val_arr)

    # Save calibrated model
    cal_path = artifacts_dir / "lgbm_v2_calibrated.pkl"
    cal_sha = cal_model.save(cal_path, dataset_hash=f"v2-{len(df)}-seed42")
    print(f"Saved Calibrated LightGBM: {cal_path} (SHA-256: {cal_sha[:16]}...)")
    manifest["models"]["calibrated_lightgbm"] = {
        "version": "lgbm-v2.0-calibrated",
        "path": str(cal_path),
        "sha256": cal_sha,
        "feature_importances": cal_model.get_feature_importances(),
    }

    # Save raw uncalibrated base model
    raw_path = artifacts_dir / "lgbm_v2_raw.pkl"
    with raw_path.open("wb") as f:
        pickle.dump({
            "base_model": cal_model._base_model,
            "model_version": "lgbm-v2.0-raw",
            "feature_version": "v2.0",
            "model_type": "RawLightGBMModel",
        }, f, protocol=pickle.HIGHEST_PROTOCOL)
    raw_sha = _compute_sha256(raw_path)
    manifest["models"]["raw_lightgbm"] = {
        "version": "lgbm-v2.0-raw",
        "path": str(raw_path),
        "sha256": raw_sha,
    }

    manifest_path = artifacts_dir / "training_manifest_v2.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nSaved Training Manifest: {manifest_path}")

    return manifest


if __name__ == "__main__":
    train_v2_models()
