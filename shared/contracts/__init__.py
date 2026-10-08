"""
``shared.contracts`` — the single cross-boundary import surface for Averiq.

This package exposes every public symbol that ``backend/`` and ``ai/`` are
allowed to share.  It is the *only* package that either subsystem may import
from the other's domain:

* ``backend/`` imports from ``shared.contracts``; it MUST NOT import from
  ``ai/`` directly.
* ``ai/`` imports from ``shared.contracts``; it MUST NOT import from
  ``backend/`` directly.

The package has **zero I/O and zero implementation imports**.  Only the
Python standard library (``enum``, ``typing``, ``datetime``), ``pydantic``,
and the three sibling modules are imported at package initialisation time.

Requirements: DATA-008, DATA-010.

Usage example::

    from shared.contracts import (
        CaseContext,
        DiagnosisOutput,
        RecoverabilityFeatures,
        PolicyConfig,
        InterventionCandidate,
        AIProvider,
        RecoverabilityModelInterface,
        FailureCategory,
        CaseState,
        InterventionType,
        ExecutionCapability,
        ProviderSupportStatus,
        ExposureTier,
        RecoveryRiskTier,
        UserRole,
        OperatingMode,
        PolicyDecisionOutcome,
        AttributionClass,
        VerificationOutcome,
    )
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
from shared.contracts.enums import (
    AttributionClass,
    CaseState,
    ExecutionCapability,
    ExposureTier,
    FailureCategory,
    InterventionType,
    OperatingMode,
    PolicyDecisionOutcome,
    ProviderSupportStatus,
    RecoveryRiskTier,
    UserRole,
    VerificationOutcome,
)

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
from shared.contracts.schemas import (
    CaseContext,
    DiagnosisOutput,
    InterventionCandidate,
    PolicyConfig,
    RecoverabilityFeatures,
)

# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------
from shared.contracts.protocols import (
    AIProvider,
    RecoverabilityModelInterface,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # --- Enums ---
    "AttributionClass",
    "CaseState",
    "ExecutionCapability",
    "ExposureTier",
    "FailureCategory",
    "InterventionType",
    "OperatingMode",
    "PolicyDecisionOutcome",
    "ProviderSupportStatus",
    "RecoveryRiskTier",
    "UserRole",
    "VerificationOutcome",
    # --- Pydantic schemas ---
    "CaseContext",
    "DiagnosisOutput",
    "InterventionCandidate",
    "PolicyConfig",
    "RecoverabilityFeatures",
    # --- Protocols ---
    "AIProvider",
    "RecoverabilityModelInterface",
]
