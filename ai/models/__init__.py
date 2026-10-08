"""
AI model implementations for Averiq's Recoverability_Model.

Both models implement ``RecoverabilityModelInterface`` from
``shared/contracts/protocols.py`` via structural subtyping — no direct
import from ``backend/`` is required.

Available implementations:
- ``LightGBMModel`` — primary model (FR-MODEL-004)
- ``LogisticRegressionBaseline`` — comparison baseline (FR-MODEL-003)

Requirements: FR-MODEL-003, FR-MODEL-004, AI-MODEL-002
"""

from __future__ import annotations

from ai.models.lightgbm_model import LightGBMModel
from ai.models.logistic_regression import LogisticRegressionBaseline

__all__ = ["LightGBMModel", "LogisticRegressionBaseline"]
