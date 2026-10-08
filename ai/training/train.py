#!/usr/bin/env python3
"""make train — train recoverability models on synthetic data.

Trains both the LightGBM primary model and the Logistic Regression baseline
on a reproducible synthetic dataset.

Usage:
    python ai/training/train.py
    make train

Environment variables:
    DATASET_SIZE   Number of synthetic cases to generate (default: 50000).
                   Valid production range: 50000–250000.
                   Smoke-test range: 100+.
    RANDOM_SEED    Random seed for full reproducibility (default: 42).
                   Same seed + same DATASET_SIZE → identical training run.

Output:
    Trained model artifacts stored in ``model_artifacts/`` directory:
      - ``model_artifacts/lgbm_{RANDOM_SEED}_{DATASET_SIZE}.pkl``
      - ``model_artifacts/lr_{RANDOM_SEED}_{DATASET_SIZE}.pkl``

    A training summary is printed to stdout.

Lifecycle record:
    Both artifacts are registered in the ``model_artifacts`` database table
    with ``lifecycle_state = 'trained'`` when a DATABASE_URL is configured.
    If no database is available, the script skips the DB insert and prints
    a warning — training artifacts are still saved to disk.

Requirements: EVAL-001, AI-MODEL-001
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Add project root to sys.path so all package imports resolve correctly
# when running as ``python ai/training/train.py`` from the project root.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _read_env() -> tuple[int, int]:
    """Read and validate DATASET_SIZE and RANDOM_SEED from the environment.

    Returns
    -------
    tuple[int, int]
        (dataset_size, random_seed)
    """
    raw_size = os.environ.get("DATASET_SIZE", "50000")
    raw_seed = os.environ.get("RANDOM_SEED", "42")

    try:
        dataset_size = int(raw_size)
    except ValueError:
        print(f"ERROR: DATASET_SIZE must be an integer, got: '{raw_size}'", file=sys.stderr)
        sys.exit(1)

    try:
        random_seed = int(raw_seed)
    except ValueError:
        print(f"ERROR: RANDOM_SEED must be an integer, got: '{raw_seed}'", file=sys.stderr)
        sys.exit(1)

    if dataset_size < 100:
        print(
            f"ERROR: DATASET_SIZE must be >= 100, got: {dataset_size}",
            file=sys.stderr,
        )
        sys.exit(1)

    if dataset_size > 250_000:
        print(
            f"WARNING: DATASET_SIZE={dataset_size} exceeds recommended maximum of 250,000. "
            "Training may be slow.",
        )

    return dataset_size, random_seed


async def _insert_lifecycle_record(
    model_version: str,
    model_type: str,
    artifact_path: str,
    artifact_sha256: str,
    dataset_hash: str,
    random_seed: int,
    hyperparameters: dict[str, Any],
) -> None:
    """Insert a lifecycle record (lifecycle_state='trained') into model_artifacts table.

    This function is best-effort — if DATABASE_URL is not configured or the
    database is unavailable, a warning is printed and training proceeds.
    """
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("  [DB] DATABASE_URL not set — skipping lifecycle record insert.")
        return

    try:
        # Dynamic import to avoid hard dependency on backend/ in the AI module.
        # This respects the module boundary rule (ai/ must not import backend/ directly)
        # by going through the environment / configuration at runtime.
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        from sqlalchemy import text

        engine = create_async_engine(database_url, echo=False)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("""
                        INSERT INTO model_artifacts (
                            model_version,
                            model_type,
                            lifecycle_state,
                            dataset_hash,
                            artifact_sha256,
                            feature_version,
                            training_timestamp,
                            random_seed,
                            hyperparameters,
                            artifact_path,
                            evaluation_metrics
                        ) VALUES (
                            :model_version,
                            :model_type,
                            'trained',
                            :dataset_hash,
                            :artifact_sha256,
                            'v1.0',
                            :training_timestamp,
                            :random_seed,
                            CAST(:hyperparameters AS JSONB),
                            :artifact_path,
                            NULL
                        )
                        ON CONFLICT (model_version) DO UPDATE SET
                            lifecycle_state = EXCLUDED.lifecycle_state,
                            dataset_hash = EXCLUDED.dataset_hash,
                            artifact_sha256 = EXCLUDED.artifact_sha256,
                            training_timestamp = EXCLUDED.training_timestamp,
                            artifact_path = EXCLUDED.artifact_path
                    """),
                    {
                        "model_version": model_version,
                        "model_type": model_type,
                        "dataset_hash": dataset_hash,
                        "artifact_sha256": artifact_sha256,
                        "training_timestamp": datetime.now(tz=timezone.utc),
                        "random_seed": random_seed,
                        "hyperparameters": str(hyperparameters).replace("'", '"'),
                        "artifact_path": artifact_path,
                    },
                )

        await engine.dispose()
        print(f"  [DB] lifecycle_state='trained' recorded for {model_version}")

    except Exception as exc:  # noqa: BLE001
        print(f"  [DB] WARNING: could not insert lifecycle record: {exc}")
        print("  [DB] Training artifacts were saved to disk successfully.")


def main() -> None:
    """Main training entry point."""
    dataset_size, random_seed = _read_env()

    print("=" * 60)
    print("Averiq — make train")
    print("=" * 60)
    print(f"  DATASET_SIZE : {dataset_size:,}")
    print(f"  RANDOM_SEED  : {random_seed}")
    print()

    # -------------------------------------------------------------------------
    # Step 1: Generate synthetic dataset
    # -------------------------------------------------------------------------
    from ai.data.generator import generate
    from ai.training.feature_extraction import (
        temporal_split,
        extract_feature_matrix,
        SPLIT_BOUNDARY_1,
        SPLIT_BOUNDARY_2,
    )

    print("Step 1/5  Generating synthetic dataset...")
    cases = generate(seed=random_seed, count=dataset_size)
    print(f"          Generated {len(cases):,} cases")

    # -------------------------------------------------------------------------
    # Step 2: Temporal split
    # -------------------------------------------------------------------------
    print("Step 2/5  Applying temporal split...")
    train_cases, val_cases, test_cases = temporal_split(cases)
    print(f"          Split boundaries:")
    print(f"            training   < {SPLIT_BOUNDARY_1.date()} → {len(train_cases):,} cases")
    print(f"            validation < {SPLIT_BOUNDARY_2.date()} → {len(val_cases):,} cases")
    print(f"            test       ≥ {SPLIT_BOUNDARY_2.date()} → {len(test_cases):,} cases")

    if not train_cases:
        print(
            "ERROR: Training split is empty. The synthetic data may not cover the "
            f"training period (before {SPLIT_BOUNDARY_1.date()}). "
            "Try a larger DATASET_SIZE.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not test_cases:
        print(
            f"WARNING: Test split is empty (no cases after {SPLIT_BOUNDARY_2.date()}). "
            "Evaluation metrics will not be available for the test set."
        )

    # -------------------------------------------------------------------------
    # Step 3: Extract feature matrices
    # -------------------------------------------------------------------------
    print("Step 3/5  Extracting feature matrices...")
    X_train, y_train = extract_feature_matrix(train_cases)
    X_val, y_val = extract_feature_matrix(val_cases) if val_cases else ([], [])
    print(f"          X_train: {len(X_train):,} samples × 10 features")
    print(f"          X_val:   {len(X_val):,} samples")

    # -------------------------------------------------------------------------
    # Step 4: Train models
    # -------------------------------------------------------------------------
    artifacts_dir = Path("model_artifacts")
    artifacts_dir.mkdir(exist_ok=True)

    dataset_hash = f"synthetic-{dataset_size}-seed{random_seed}"
    training_timestamp = datetime.now(tz=timezone.utc).isoformat()

    # --- LightGBM ---
    print("Step 4/5  Training models...")
    lgbm_version = f"lgbm-v1.0-seed{random_seed}-n{dataset_size}"
    lgbm_path = artifacts_dir / f"lgbm_{random_seed}_{dataset_size}.pkl"

    lgbm_sha256 = "<not trained>"
    lgbm_trained = False

    try:
        from ai.models.lightgbm_model import LightGBMModel

        print(f"          [LightGBM] Training {lgbm_version}...")
        lgbm = LightGBMModel(model_version=lgbm_version, random_seed=random_seed)
        lgbm.fit(
            X_train, y_train,
            X_val=X_val if X_val else None,
            y_val=y_val if y_val else None,
        )
        lgbm_sha256 = lgbm.save(
            lgbm_path,
            dataset_hash=dataset_hash,
            random_seed=random_seed,
        )
        lgbm_trained = True
        print(f"          [LightGBM] Saved → {lgbm_path}")
        print(f"          [LightGBM] sha256: {lgbm_sha256[:32]}...")

    except ImportError as exc:
        print(f"          [LightGBM] WARNING: could not train — {exc}")
        print("          [LightGBM] Install lightgbm via: pip install lightgbm")

    # --- Logistic Regression ---
    lr_version = f"lr-v1.0-seed{random_seed}-n{dataset_size}"
    lr_path = artifacts_dir / f"lr_{random_seed}_{dataset_size}.pkl"

    print(f"          [LogisticRegression] Training {lr_version}...")
    from ai.models.logistic_regression import LogisticRegressionBaseline

    lr = LogisticRegressionBaseline(model_version=lr_version, random_seed=random_seed)
    lr.fit(X_train, y_train)
    lr_sha256 = lr.save(
        lr_path,
        dataset_hash=dataset_hash,
        random_seed=random_seed,
    )
    print(f"          [LogisticRegression] Saved → {lr_path}")
    print(f"          [LogisticRegression] sha256: {lr_sha256[:32]}...")

    # -------------------------------------------------------------------------
    # Step 5: Insert lifecycle records
    # -------------------------------------------------------------------------
    print("Step 5/5  Inserting lifecycle records into database...")

    async def _register_artifacts() -> None:
        if lgbm_trained:
            await _insert_lifecycle_record(
                model_version=lgbm_version,
                model_type="LightGBMModel",
                artifact_path=str(lgbm_path.resolve()),
                artifact_sha256=lgbm_sha256,
                dataset_hash=dataset_hash,
                random_seed=random_seed,
                hyperparameters=lgbm.get_artifact_metadata().get("hyperparameters", {}),
            )
        await _insert_lifecycle_record(
            model_version=lr_version,
            model_type="LogisticRegressionBaseline",
            artifact_path=str(lr_path.resolve()),
            artifact_sha256=lr_sha256,
            dataset_hash=dataset_hash,
            random_seed=random_seed,
            hyperparameters=lr.get_artifact_metadata().get("hyperparameters", {}),
        )

    asyncio.run(_register_artifacts())

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    print()
    print("=" * 60)
    print("Training complete")
    print("=" * 60)
    print(f"  Dataset size  : {dataset_size:,} cases")
    print(f"  Random seed   : {random_seed}")
    print(f"  Train split   : {len(train_cases):,} cases (< {SPLIT_BOUNDARY_1.date()})")
    print(f"  Val split     : {len(val_cases):,} cases")
    print(f"  Test split    : {len(test_cases):,} cases (≥ {SPLIT_BOUNDARY_2.date()})")
    print(f"  Artifacts dir : {artifacts_dir.resolve()}")
    if lgbm_trained:
        print(f"  LightGBM      : {lgbm_path.name}  ({lgbm_sha256[:16]}...)")
    else:
        print("  LightGBM      : NOT TRAINED (lightgbm not installed)")
    print(f"  LR baseline   : {lr_path.name}  ({lr_sha256[:16]}...)")
    print()
    print("Next step: run 'make evaluate' to compute model metrics.")


if __name__ == "__main__":
    main()
