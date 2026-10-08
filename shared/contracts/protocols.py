"""
Structural interface contracts for Averiq's cross-boundary AI components.

Both protocols use ``typing.Protocol`` with ``@runtime_checkable`` so that
``isinstance()`` checks can verify that a concrete implementation satisfies
the interface at startup (dependency injection / container validation).

Structural subtyping means ``backend/`` and ``ai/`` never import each other
directly:

* ``ai/providers/stub.py`` and ``ai/providers/external_llm.py`` satisfy
  ``AIProvider`` without importing from ``backend/``.
* ``ai/models/lightgbm_model.py`` and ``ai/models/logistic_regression.py``
  satisfy ``RecoverabilityModelInterface`` without importing from ``backend/``.

Active implementations are injected at application startup via dependency
injection.  No module outside ``shared/contracts/`` is imported here.

Requirements: DATA-008, DATA-010, FR-DIAG-001, FR-MODEL-001.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shared.contracts.schemas import CaseContext, DiagnosisOutput, RecoverabilityFeatures


@runtime_checkable
class AIProvider(Protocol):
    """
    Structural interface for all AI/LLM diagnosis providers.

    Concrete implementations:

    * ``ai.providers.stub.StubAIProvider`` — deterministic, no external calls.
      Used when no external LLM is configured.  MUST work out-of-the-box with
      zero configuration.  FR-DIAG-007, AI-PROV-001.
    * ``ai.providers.external_llm.ExternalLLMProvider`` — calls an external
      LLM API.  Enforces architectural prompt injection defence (role
      separation, structured JSON data section, enum allowlist validation).
      FR-DIAG-008, SEC-AI-002.

    The ``Root_Cause_Reasoner`` always wraps the active provider with:

    * An ``asyncio.wait_for`` timeout guard (default: 10 s, configurable via
      ``AI_RESPONSE_TIMEOUT_SECONDS``).
    * A Pydantic schema validation gate on the returned ``DiagnosisOutput``.
    * A deterministic fallback classifier invoked on timeout or validation
      failure.

    SEC-AI-003: LLM output MUST NEVER directly authorise or execute a
    financial action.  All AI output passes through the ``Policy_Engine``
    before the ``Execution_Engine`` acts.
    """

    async def diagnose(self, context: CaseContext) -> DiagnosisOutput:
        """
        Produce a structured diagnosis for a failed payment case.

        Parameters
        ----------
        context:
            A fully-populated ``CaseContext`` carrying the payment failure
            signals.  All untrusted data (error codes, customer fields) MUST
            be passed through this structured object — never by string
            interpolation into a system prompt.

        Returns
        -------
        DiagnosisOutput
            A validated diagnosis including ``failure_category``,
            ``confidence_score``, ``ai_source``, and optional ``reasoning``
            and ``raw_response`` fields.

            Implementations MUST set ``ai_source`` to the correct identifier
            string ("StubAIProvider", "ExternalLLMProvider", or
            "DeterministicFallback") so that the decision trace accurately
            reflects which component produced the output.  AI-PROV-002.

        Raises
        ------
        This method SHOULD NOT raise exceptions for recoverable conditions
        (timeout, schema validation failure).  The ``Root_Cause_Reasoner``
        handles those cases by invoking the deterministic fallback.
        Unrecoverable errors (e.g., missing required configuration) MAY raise
        to signal a misconfigured provider at startup.
        """
        ...  # pragma: no cover


@runtime_checkable
class RecoverabilityModelInterface(Protocol):
    """
    Structural interface for ML models that predict the probability of
    recovering a failed payment.

    Concrete implementations:

    * ``ai.models.lightgbm_model.LightGBMModel`` — primary model.  Used by
      default when both variants are available.  FR-MODEL-004.
    * ``ai.models.logistic_regression.LogisticRegressionBaseline`` — baseline
      model for comparison.  FR-MODEL-003.

    Model lifecycle: ``trained → evaluated → candidate → active → retired``.
    A model artifact MUST NOT transition to ``active`` without passing the
    evaluation step with a Brier score below the configured threshold.
    AI-MODEL-001.

    Model integrity guarantee (AI-MODEL-002):
    Before loading, the model file's SHA-256 digest is compared against the
    ``artifact_sha256`` stored in ``model_artifacts``.  A mismatch causes the
    load to fail; the system falls back to the previous active model or stub.

    Determinism guarantee (FR-MODEL-010, Property 4):
    For any valid ``RecoverabilityFeatures`` input and any loaded model
    artifact with a fixed random seed, calling ``predict(features)`` on the
    same process and runtime MUST return the same score within documented
    numerical tolerance.

    Feature version contract:
    ``feature_version`` MUST match the ``feature_version`` field of every
    ``RecoverabilityFeatures`` instance passed to ``predict``.  A mismatch
    is a hard error — never silently produce incorrect predictions with
    mismatched features.
    """

    #: Version string identifying this model artifact
    #: (e.g. ``"lgbm-v2.1.0-2024-01-15"``).  Recorded in every AuditEvent
    #: that includes a recoverability score.  AI-MODEL-002.
    model_version: str

    #: Version string identifying the feature schema expected by this model
    #: (e.g. ``"v1.0"``).  Must match ``RecoverabilityFeatures.feature_version``
    #: at serve time.  FR-MODEL-002.
    feature_version: str

    def predict(self, features: RecoverabilityFeatures) -> float:
        """
        Compute the recovery probability for a single case.

        Parameters
        ----------
        features:
            Deterministic, provider-supplied feature vector.  The
            ``features.feature_version`` MUST match ``self.feature_version``.

        Returns
        -------
        float
            Recovery probability in [0.0, 1.0].  Not a financial value.
            This float is used for EV calculation and is converted to
            ``int`` paise before any storage operation.  DATA-003.

        Notes
        -----
        * The LLM-derived ``DiagnosisOutput.failure_category`` MUST NOT be
          included in ``features``.  The recoverability model uses only
          deterministic, provider-supplied signals to prevent train/serve
          mismatch.
        * For reproducibility: given the same model artifact and the same
          ``features`` input, this method MUST return the same value within
          documented numerical tolerance on the same runtime.  FR-MODEL-010.
        """
        ...  # pragma: no cover

    def predict_batch(self, features: list[RecoverabilityFeatures]) -> list[float]:
        """
        Compute recovery probabilities for a batch of cases in one call.

        Provides the same guarantees as ``predict`` for each element.
        Implementations SHOULD use vectorised inference (e.g.
        ``model.predict_proba``) rather than a Python loop over ``predict``.

        Parameters
        ----------
        features:
            A list of feature vectors.  All elements MUST share the same
            ``feature_version`` and it MUST match ``self.feature_version``.

        Returns
        -------
        list[float]
            Probabilities in the same order as the input list.  Each value
            is in [0.0, 1.0].
        """
        ...  # pragma: no cover
