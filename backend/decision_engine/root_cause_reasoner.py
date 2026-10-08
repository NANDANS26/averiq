"""Root Cause Reasoner — orchestrates AI diagnosis with fallback chain.

Call chain:
  1. Try AIProvider.diagnose(context) with asyncio.wait_for timeout
  2. On success: validate DiagnosisOutput schema (SEC-AI-001)
  3. On timeout/exception: invoke DeterministicFallbackClassifier
  4. On schema validation failure: invoke DeterministicFallbackClassifier
  5. Return validated DiagnosisOutput + metadata for AuditEvent

Security invariant (SEC-AI-003):
  LLM output NEVER directly authorises or executes a financial action.
  The DiagnosisOutput returned here passes through Policy_Engine before
  any execution occurs.

Requirements: FR-DIAG-001, FR-DIAG-003, FR-DIAG-004, FR-DIAG-005
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

from backend.config import settings
from backend.decision_engine.fallback_classifier import DeterministicFallbackClassifier
from shared.contracts.enums import FailureCategory
from shared.contracts.protocols import AIProvider
from shared.contracts.schemas import CaseContext, DiagnosisOutput

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# DiagnosisResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiagnosisResult:
    """Diagnosis outcome plus metadata required for the AuditEvent.

    Attributes
    ----------
    diagnosis:
        The validated ``DiagnosisOutput`` produced by whichever provider
        (AI or deterministic fallback) ran.
    ai_provider_id:
        Identifies which component produced the diagnosis.
        One of: ``"StubAIProvider"``, ``"ExternalLLMProvider"``,
        ``"DeterministicFallback"``.  Recorded in AuditEvent (AI-PROV-002).
    used_fallback:
        ``True`` when the deterministic fallback classifier was used instead
        of the AI provider.
    fallback_reason:
        ``None`` when the AI provider succeeded.  Otherwise, one of:
        ``"ai_timeout"``, ``"ai_provider_error"``,
        ``"schema_validation_failure"``, ``"invalid_failure_category"``,
        ``"invalid_confidence_score"``.
    ai_timeout:
        ``True`` when an ``asyncio.TimeoutError`` triggered the fallback.
        Persisted as ``ai_timeout=True`` in ``RevenueRiskCase.case_metadata``.
        FR-DIAG-005.
    metadata_updates:
        Dict merged into ``RevenueRiskCase.case_metadata`` (JSONB).  Always
        contains: ``ai_timeout``, ``fallback_reason``, ``ai_provider_id``,
        and ``diagnosis_confidence``.
    """

    diagnosis: DiagnosisOutput

    # For the AuditEvent (AI-PROV-002)
    ai_provider_id: str
    """One of: 'StubAIProvider' | 'ExternalLLMProvider' | 'DeterministicFallback'."""

    used_fallback: bool
    """True when the deterministic fallback classifier was invoked."""

    fallback_reason: str | None
    """None if AI succeeded; otherwise describes why fallback was used."""

    ai_timeout: bool
    """True if asyncio.TimeoutError triggered the fallback. FR-DIAG-005."""

    # For case_metadata JSONB
    metadata_updates: dict[str, Any] = field(default_factory=dict)
    """Merged into RevenueRiskCase.case_metadata on the successful case record."""


# ---------------------------------------------------------------------------
# RootCauseReasoner
# ---------------------------------------------------------------------------


class RootCauseReasoner:
    """Orchestrates AI-assisted failure diagnosis with a deterministic fallback chain.

    The orchestration flow for each ``diagnose()`` call is:

    1. Attempt ``AIProvider.diagnose(context)`` wrapped in
       ``asyncio.wait_for`` with the configured timeout.
    2. If the AI call succeeds, validate the returned ``DiagnosisOutput``
       against the Pydantic schema and the ``FailureCategory`` enum allowlist.
    3. If any validation check fails, or if the AI call times out or raises
       an exception, invoke the ``DeterministicFallbackClassifier`` and
       record the reason.
    4. Return a ``DiagnosisResult`` carrying the validated diagnosis and all
       metadata required for the AuditEvent and ``case_metadata`` update.

    Security invariant (SEC-AI-003):
      The ``DiagnosisOutput`` returned here is for operator-facing explanation
      only.  It MUST pass through the ``Policy_Engine`` before any execution
      decision is made.  The ``RootCauseReasoner`` never calls the
      ``ExecutionEngine`` or ``GatewayAbstraction`` directly.

    Requirements: FR-DIAG-001, FR-DIAG-003, FR-DIAG-004, FR-DIAG-005.
    """

    def __init__(
        self,
        ai_provider: AIProvider,
        timeout_seconds: float | None = None,
    ) -> None:
        """
        Parameters
        ----------
        ai_provider:
            Any object satisfying the ``AIProvider`` protocol.  Injected by
            the application container at startup.
        timeout_seconds:
            Override for the AI response timeout.  Defaults to
            ``settings.AI_RESPONSE_TIMEOUT_SECONDS`` when ``None``.
        """
        self._ai_provider = ai_provider
        self._timeout = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(settings.AI_RESPONSE_TIMEOUT_SECONDS)
        )
        self._fallback = DeterministicFallbackClassifier()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def diagnose(self, context: CaseContext) -> DiagnosisResult:
        """Run failure diagnosis with timeout guard and fallback chain.

        Parameters
        ----------
        context:
            The fully-populated ``CaseContext`` for the AT_RISK case being
            diagnosed.  All untrusted data (error_code, customer fields) are
            encapsulated here — never interpolated into prompts.

        Returns
        -------
        DiagnosisResult
            A frozen dataclass containing the validated ``DiagnosisOutput``
            and all metadata required for the AuditEvent.

        Notes
        -----
        This method never raises exceptions from the AI provider.  All error
        conditions (timeout, unexpected exception, schema failure) are handled
        internally and result in a ``DiagnosisResult`` with
        ``used_fallback=True`` and the appropriate ``fallback_reason``.
        """
        raw_output: DiagnosisOutput | None = None
        fallback_reason: str | None = None
        ai_timeout: bool = False

        # ------------------------------------------------------------------
        # Step 1: Attempt AI diagnosis with timeout guard
        # ------------------------------------------------------------------
        try:
            raw_output = await asyncio.wait_for(
                self._ai_provider.diagnose(context),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            # FR-DIAG-005: record ai_timeout=True and invoke fallback
            ai_timeout = True
            fallback_reason = "ai_timeout"
            logger.warning(
                "root_cause_reasoner.ai_timeout",
                case_id=context.case_id,
                timeout=self._timeout,
            )
        except Exception:  # noqa: BLE001
            # Any other AI provider error triggers fallback
            fallback_reason = "ai_provider_error"
            logger.warning(
                "root_cause_reasoner.ai_provider_error",
                case_id=context.case_id,
            )

        # ------------------------------------------------------------------
        # Step 2: Validate AI output when a response was received
        # ------------------------------------------------------------------
        if raw_output is not None and fallback_reason is None:
            fallback_reason = self._validate_output(raw_output, context)

        # ------------------------------------------------------------------
        # Step 3: Invoke fallback if anything went wrong
        # ------------------------------------------------------------------
        if fallback_reason is not None:
            diagnosis = self._invoke_fallback(context, fallback_reason)
            logger.info(
                "root_cause_reasoner.fallback_invoked",
                case_id=context.case_id,
                reason=fallback_reason,
            )
        else:
            # raw_output passed validation — use it directly
            assert raw_output is not None  # narrowing; guaranteed by flow above
            diagnosis = raw_output

        # ------------------------------------------------------------------
        # Step 4: Build DiagnosisResult
        # ------------------------------------------------------------------
        used_fallback = diagnosis.ai_source == "DeterministicFallback"
        ai_provider_id = diagnosis.ai_source

        metadata_updates: dict[str, Any] = {
            "ai_timeout": ai_timeout,
            "fallback_reason": fallback_reason,
            "ai_provider_id": ai_provider_id,
            "diagnosis_confidence": diagnosis.confidence_score,
        }

        return DiagnosisResult(
            diagnosis=diagnosis,
            ai_provider_id=ai_provider_id,
            used_fallback=used_fallback,
            fallback_reason=fallback_reason,
            ai_timeout=ai_timeout,
            metadata_updates=metadata_updates,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate_output(
        self,
        raw_output: DiagnosisOutput,
        context: CaseContext,
    ) -> str | None:
        """Validate the AI output against the DiagnosisOutput schema and enum allowlists.

        Parameters
        ----------
        raw_output:
            The ``DiagnosisOutput`` returned by the AI provider.
        context:
            The original ``CaseContext`` (used only for logging ``case_id``).

        Returns
        -------
        str | None
            ``None`` when validation passes.
            A ``fallback_reason`` string when any validation check fails.
        """
        from pydantic import ValidationError  # local import to avoid top-level cost

        # Re-validate via Pydantic to catch any schema inconsistencies.
        # This is the SEC-AI-001 gate: all AI output is validated before use.
        try:
            DiagnosisOutput.model_validate(raw_output.model_dump())
        except ValidationError:
            return "schema_validation_failure"

        # Validate failure_category against the FailureCategory enum allowlist.
        # If the LLM returned a value outside the allowlist, trigger fallback.
        # SEC-AI-001, SEC-AI-002.
        try:
            FailureCategory(raw_output.failure_category)
        except ValueError:
            return "invalid_failure_category"

        # Validate confidence_score is in [0.0, 1.0].
        # Pydantic already enforces this via ProbabilityScore, but we check
        # explicitly here in case a subclass bypasses schema constraints.
        if not (0.0 <= raw_output.confidence_score <= 1.0):
            return "invalid_confidence_score"

        return None

    def _invoke_fallback(
        self,
        context: CaseContext,
        fallback_reason: str,
    ) -> DiagnosisOutput:
        """Invoke the deterministic fallback classifier.

        Parameters
        ----------
        context:
            The original ``CaseContext`` — provides ``error_code`` to the
            fallback classifier.
        fallback_reason:
            Why the fallback was triggered.  Stored in the ``DiagnosisOutput``
            reasoning and ``case_metadata``.

        Returns
        -------
        DiagnosisOutput
            A validated ``DiagnosisOutput`` with
            ``ai_source = "DeterministicFallback"``.
        """
        return self._fallback.classify(
            error_code=context.error_code,
            error_description=context.error_description,
            fallback_reason=fallback_reason,
        )
