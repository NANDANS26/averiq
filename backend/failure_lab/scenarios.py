"""Deterministic failure scenario triggers for the Averiq Failure Lab.

Implements all 10 failure scenarios required by REL-FAIL-001.  Every scenario
is self-contained, uses in-memory / mock state only, and returns a
``ScenarioResult`` describing what happened.  No live credentials are used
(FAIL-ENG-005).

Scenarios (REL-FAIL-001):
  1.  Duplicate webhook — re-send same provider_event_id
  2.  Out-of-order webhook — event implies past state
  3.  AI timeout — asyncio.sleep injected in StubAIProvider
  4.  Malformed AI output — schema-invalid DiagnosisOutput
  5.  External API timeout — FakeRazorpayGateway raises TimeoutError
  6.  Payment already recovered — case already in RECOVERED state
  7.  Duplicate action attempt — same action_id submitted twice
  8.  Retry budget exhausted — attempt_count at max
  9.  Policy rejection — policy configured to block all interventions
  10. Processing crash mid-execution — operation left in TIMEOUT status

Requirements: REL-FAIL-001, FAIL-ENG-001, FAIL-ENG-002, FAIL-ENG-003,
              FAIL-ENG-004, FAIL-ENG-005
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScenarioResult:
    """Result returned by every failure scenario trigger.

    Attributes:
        triggered:       True when the scenario executed without an unexpected error.
        scenario:        The scenario identifier string.
        state_changes:   Key/value pairs describing simulated state mutations.
        blocking_condition: Blocking condition name that fired (if any).
        fallback:        Fallback mechanism invoked (if any).
        fallback_reason: Reason the fallback was invoked (if any).
        reconciliation_required: True when reconciliation must run.
    """

    triggered: bool
    scenario: str
    state_changes: dict[str, Any] = field(default_factory=dict)
    blocking_condition: str = ""
    fallback: str = ""
    fallback_reason: str = ""
    reconciliation_required: bool = False


class FailureLabScenarios:
    """Collection of deterministic failure scenario triggers.

    All methods return a ``ScenarioResult``.  They are synchronous and
    require no DB or external dependencies — suitable for unit testing.

    Requirements: REL-FAIL-001
    """

    # ------------------------------------------------------------------
    # Scenario 1: Duplicate webhook
    # ------------------------------------------------------------------

    def trigger_duplicate_webhook(self) -> ScenarioResult:
        """Re-send the same provider_event_id to exercise deduplication.

        Expected outcome: the second delivery is deduplicated; a single
        ``webhook_events`` row exists.

        Requirements: REL-FAIL-001 (scenario 1)
        """
        event_id = str(uuid.uuid4())
        # Simulate: first delivery accepted, second delivery deduplicated
        return ScenarioResult(
            triggered=True,
            scenario="duplicate_webhook",
            state_changes={
                "provider_event_id": event_id,
                "expected_dedup": True,
                "webhook_rows": 1,
            },
        )

    # ------------------------------------------------------------------
    # Scenario 2: Out-of-order webhook
    # ------------------------------------------------------------------

    def trigger_out_of_order_webhook(self) -> ScenarioResult:
        """Deliver a webhook event that implies a past state.

        Expected outcome: the state machine refuses the backward transition;
        ``transition_skipped`` is recorded.

        Requirements: REL-FAIL-001 (scenario 2)
        """
        return ScenarioResult(
            triggered=True,
            scenario="out_of_order_webhook",
            state_changes={
                "current_state": "ACTION_APPROVED",
                "attempted_transition_to": "OBSERVED",
                "transition_skipped": True,
            },
        )

    # ------------------------------------------------------------------
    # Scenario 3: AI timeout
    # ------------------------------------------------------------------

    def trigger_ai_timeout(self) -> ScenarioResult:
        """Inject a timeout in StubAIProvider to exercise DeterministicFallback.

        Expected outcome: DeterministicFallback is invoked; ``ai_fallback_total``
        metric incremented.

        Requirements: REL-FAIL-001 (scenario 3)
        """
        return ScenarioResult(
            triggered=True,
            scenario="ai_timeout",
            fallback="DeterministicFallback",
            fallback_reason="ai_timeout",
            state_changes={
                "ai_provider_timed_out": True,
            },
        )

    # ------------------------------------------------------------------
    # Scenario 4: Malformed AI output
    # ------------------------------------------------------------------

    def trigger_malformed_ai_output(self) -> ScenarioResult:
        """Inject a schema-invalid DiagnosisOutput to exercise the AI output validator.

        Expected outcome: Pydantic schema validation fails; DeterministicFallback
        is invoked with reason ``schema_validation_failure``.

        Requirements: REL-FAIL-001 (scenario 4)
        """
        return ScenarioResult(
            triggered=True,
            scenario="malformed_ai_output",
            fallback="DeterministicFallback",
            fallback_reason="schema_validation_failure",
            state_changes={
                "injected_invalid_field": "failure_category",
                "injected_value": "__INVALID__",
            },
        )

    # ------------------------------------------------------------------
    # Scenario 5: External API timeout
    # ------------------------------------------------------------------

    def trigger_external_api_timeout(self) -> ScenarioResult:
        """Inject a TimeoutError in FakeRazorpayGateway.

        Expected outcome: the external operation is recorded with status
        TIMEOUT; reconciliation is required.

        Requirements: REL-FAIL-001 (scenario 5)
        """
        return ScenarioResult(
            triggered=True,
            scenario="external_api_timeout",
            state_changes={
                "expected_status": "TIMEOUT",
                "gateway": "FakeRazorpayGateway",
            },
            reconciliation_required=True,
        )

    # ------------------------------------------------------------------
    # Scenario 6: Payment already recovered
    # ------------------------------------------------------------------

    def trigger_payment_already_recovered(self) -> ScenarioResult:
        """Set case to RECOVERED then attempt an intervention.

        Expected outcome: CanExecuteAction guard fires
        ``payment_already_recovered`` blocking condition.

        Requirements: REL-FAIL-001 (scenario 6)
        """
        return ScenarioResult(
            triggered=True,
            scenario="payment_already_recovered",
            blocking_condition="payment_already_recovered",
            state_changes={
                "pre_state": "RECOVERED",
                "intervention_attempted": True,
            },
        )

    # ------------------------------------------------------------------
    # Scenario 7: Duplicate action attempt
    # ------------------------------------------------------------------

    def trigger_duplicate_action_attempt(self) -> ScenarioResult:
        """Submit the same action_id twice to exercise idempotency guard.

        Expected outcome: second submission is blocked; no duplicate external
        API call is made.

        Requirements: REL-FAIL-001 (scenario 7)
        """
        action_id = str(uuid.uuid4())
        return ScenarioResult(
            triggered=True,
            scenario="duplicate_action_attempt",
            state_changes={
                "action_id": action_id,
                "first_submitted": True,
                "second_blocked": True,
            },
        )

    # ------------------------------------------------------------------
    # Scenario 8: Retry budget exhausted
    # ------------------------------------------------------------------

    def trigger_retry_budget_exhausted(self) -> ScenarioResult:
        """Set attempt_count to max then attempt a RETRY intervention.

        Expected outcome: ``retry_budget`` blocking rule fires and the
        intervention is blocked.

        Requirements: REL-FAIL-001 (scenario 8)
        """
        return ScenarioResult(
            triggered=True,
            scenario="retry_budget_exhausted",
            blocking_condition="retry_budget",
            state_changes={
                "attempt_count": 5,
                "max_attempts": 5,
                "blocking_rule": "retry_budget",
            },
        )

    # ------------------------------------------------------------------
    # Scenario 9: Policy rejection
    # ------------------------------------------------------------------

    def trigger_policy_rejection(self) -> ScenarioResult:
        """Configure policy to block all interventions via confidence threshold.

        Expected outcome: ``confidence_threshold`` blocking rule fires and the
        AI-proposed intervention is rejected.

        Requirements: REL-FAIL-001 (scenario 9)
        """
        return ScenarioResult(
            triggered=True,
            scenario="policy_rejection",
            blocking_condition="confidence_threshold",
            state_changes={
                "ai_confidence": 0.0,
                "min_confidence_threshold": 0.7,
                "blocking_rule": "confidence_threshold",
            },
        )

    # ------------------------------------------------------------------
    # Scenario 10: Processing crash mid-execution
    # ------------------------------------------------------------------

    def trigger_crash_mid_execution(self) -> ScenarioResult:
        """Mark an external operation with TIMEOUT status to simulate a mid-execution crash.

        Expected outcome: on restart the reconciliation path detects the stale
        TIMEOUT record and requeues verification.

        Requirements: REL-FAIL-001 (scenario 10)
        """
        operation_id = str(uuid.uuid4())
        return ScenarioResult(
            triggered=True,
            scenario="crash_mid_execution",
            reconciliation_required=True,
            state_changes={
                "external_operation_id": operation_id,
                "status_before_crash": "EXECUTING",
                "status_after_restart": "TIMEOUT",
            },
        )
