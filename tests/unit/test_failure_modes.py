"""Unit tests for all 10 failure scenarios from REL-FAIL-001.

One concrete example test per failure scenario.
All tests are mocked — no DB or external dependencies.

Requirements: REL-FAIL-001, NFR-TEST-002
"""
from __future__ import annotations

import uuid
import pytest
from backend.failure_lab.scenarios import FailureLabScenarios, ScenarioResult


scenarios = FailureLabScenarios()


# ---------------------------------------------------------------------------
# Scenario 1: Duplicate webhook
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scenario_1_duplicate_webhook() -> None:
    """Duplicate webhook delivery is deduplicated (scenario 1).

    Expected: result.state_changes['expected_dedup'] == True
    Requirements: REL-FAIL-001 (scenario 1)
    """
    result = scenarios.trigger_duplicate_webhook()

    assert result.triggered is True
    assert result.scenario == "duplicate_webhook"
    assert result.state_changes.get("expected_dedup") is True, (
        "Duplicate webhook scenario must record expected_dedup=True in state_changes"
    )


# ---------------------------------------------------------------------------
# Scenario 2: Out-of-order webhook
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scenario_2_out_of_order_webhook() -> None:
    """Out-of-order webhook is rejected by the state machine (scenario 2).

    Expected: result.state_changes['transition_skipped'] == True
    Requirements: REL-FAIL-001 (scenario 2)
    """
    result = scenarios.trigger_out_of_order_webhook()

    assert result.triggered is True
    assert result.scenario == "out_of_order_webhook"
    assert result.state_changes.get("transition_skipped") is True, (
        "Out-of-order scenario must record transition_skipped=True in state_changes"
    )


# ---------------------------------------------------------------------------
# Scenario 3: AI timeout
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scenario_3_ai_timeout() -> None:
    """AI provider timeout causes DeterministicFallback invocation (scenario 3).

    Expected: result.fallback == 'DeterministicFallback'
    Requirements: REL-FAIL-001 (scenario 3)
    """
    result = scenarios.trigger_ai_timeout()

    assert result.triggered is True
    assert result.scenario == "ai_timeout"
    assert result.fallback == "DeterministicFallback", (
        "AI timeout scenario must record fallback='DeterministicFallback'"
    )


# ---------------------------------------------------------------------------
# Scenario 4: Malformed AI output
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scenario_4_malformed_ai_output() -> None:
    """Malformed AI output triggers schema_validation_failure fallback (scenario 4).

    Expected: result.fallback_reason == 'schema_validation_failure'
    Requirements: REL-FAIL-001 (scenario 4)
    """
    result = scenarios.trigger_malformed_ai_output()

    assert result.triggered is True
    assert result.scenario == "malformed_ai_output"
    assert result.fallback_reason == "schema_validation_failure", (
        "Malformed AI output scenario must record fallback_reason='schema_validation_failure'"
    )


# ---------------------------------------------------------------------------
# Scenario 5: External API timeout
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scenario_5_external_api_timeout() -> None:
    """External API timeout results in TIMEOUT status (scenario 5).

    Expected: result.state_changes['expected_status'] == 'TIMEOUT'
    Requirements: REL-FAIL-001 (scenario 5)
    """
    result = scenarios.trigger_external_api_timeout()

    assert result.triggered is True
    assert result.scenario == "external_api_timeout"
    assert result.state_changes.get("expected_status") == "TIMEOUT", (
        "External API timeout scenario must record expected_status='TIMEOUT' in state_changes"
    )


# ---------------------------------------------------------------------------
# Scenario 6: Payment already recovered
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scenario_6_payment_already_recovered() -> None:
    """Intervention on already-recovered case is blocked (scenario 6).

    Expected: result.blocking_condition == 'payment_already_recovered'
    Requirements: REL-FAIL-001 (scenario 6)
    """
    result = scenarios.trigger_payment_already_recovered()

    assert result.triggered is True
    assert result.scenario == "payment_already_recovered"
    assert result.blocking_condition == "payment_already_recovered", (
        "Already-recovered scenario must record blocking_condition='payment_already_recovered'"
    )


# ---------------------------------------------------------------------------
# Scenario 7: Duplicate action attempt
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scenario_7_duplicate_action_attempt() -> None:
    """Second submission of same action_id is blocked by idempotency guard (scenario 7).

    Expected: result.state_changes['second_blocked'] == True
    Requirements: REL-FAIL-001 (scenario 7)
    """
    result = scenarios.trigger_duplicate_action_attempt()

    assert result.triggered is True
    assert result.scenario == "duplicate_action_attempt"
    assert result.state_changes.get("second_blocked") is True, (
        "Duplicate action scenario must record second_blocked=True in state_changes"
    )


# ---------------------------------------------------------------------------
# Scenario 8: Retry budget exhausted
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scenario_8_retry_budget_exhausted() -> None:
    """Retry attempt after budget exhaustion is blocked by retry_budget rule (scenario 8).

    Expected: result.state_changes['blocking_rule'] == 'retry_budget'
    Requirements: REL-FAIL-001 (scenario 8)
    """
    result = scenarios.trigger_retry_budget_exhausted()

    assert result.triggered is True
    assert result.scenario == "retry_budget_exhausted"
    assert result.state_changes.get("blocking_rule") == "retry_budget", (
        "Retry budget scenario must record blocking_rule='retry_budget' in state_changes"
    )


# ---------------------------------------------------------------------------
# Scenario 9: Policy rejection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scenario_9_policy_rejection() -> None:
    """Low-confidence AI proposal is blocked by confidence_threshold rule (scenario 9).

    Expected: result.state_changes['blocking_rule'] == 'confidence_threshold'
    Requirements: REL-FAIL-001 (scenario 9)
    """
    result = scenarios.trigger_policy_rejection()

    assert result.triggered is True
    assert result.scenario == "policy_rejection"
    assert result.state_changes.get("blocking_rule") == "confidence_threshold", (
        "Policy rejection scenario must record blocking_rule='confidence_threshold' in state_changes"
    )


# ---------------------------------------------------------------------------
# Scenario 10: Processing crash mid-execution
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scenario_10_crash_mid_execution() -> None:
    """Mid-execution crash leaves TIMEOUT status; reconciliation path is required (scenario 10).

    Expected: result.reconciliation_required == True
    Requirements: REL-FAIL-001 (scenario 10)
    """
    result = scenarios.trigger_crash_mid_execution()

    assert result.triggered is True
    assert result.scenario == "crash_mid_execution"
    assert result.reconciliation_required is True, (
        "Crash mid-execution scenario must record reconciliation_required=True"
    )


# ---------------------------------------------------------------------------
# Cross-cutting properties
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_10_scenarios_return_scenario_result_instances() -> None:
    """All 10 scenario triggers must return ScenarioResult instances.

    Requirements: REL-FAIL-001
    """
    results = [
        scenarios.trigger_duplicate_webhook(),
        scenarios.trigger_out_of_order_webhook(),
        scenarios.trigger_ai_timeout(),
        scenarios.trigger_malformed_ai_output(),
        scenarios.trigger_external_api_timeout(),
        scenarios.trigger_payment_already_recovered(),
        scenarios.trigger_duplicate_action_attempt(),
        scenarios.trigger_retry_budget_exhausted(),
        scenarios.trigger_policy_rejection(),
        scenarios.trigger_crash_mid_execution(),
    ]
    for result in results:
        assert isinstance(result, ScenarioResult), (
            f"Expected ScenarioResult, got {type(result).__name__}"
        )


@pytest.mark.unit
def test_all_10_scenarios_return_triggered_true() -> None:
    """All 10 scenario triggers must return triggered=True.

    Requirements: REL-FAIL-001
    """
    results = [
        scenarios.trigger_duplicate_webhook(),
        scenarios.trigger_out_of_order_webhook(),
        scenarios.trigger_ai_timeout(),
        scenarios.trigger_malformed_ai_output(),
        scenarios.trigger_external_api_timeout(),
        scenarios.trigger_payment_already_recovered(),
        scenarios.trigger_duplicate_action_attempt(),
        scenarios.trigger_retry_budget_exhausted(),
        scenarios.trigger_policy_rejection(),
        scenarios.trigger_crash_mid_execution(),
    ]
    for result in results:
        assert result.triggered is True, (
            f"Scenario '{result.scenario}' must return triggered=True"
        )


@pytest.mark.unit
def test_all_10_scenario_names_are_unique() -> None:
    """All 10 scenario names must be unique non-empty strings.

    Requirements: REL-FAIL-001
    """
    results = [
        scenarios.trigger_duplicate_webhook(),
        scenarios.trigger_out_of_order_webhook(),
        scenarios.trigger_ai_timeout(),
        scenarios.trigger_malformed_ai_output(),
        scenarios.trigger_external_api_timeout(),
        scenarios.trigger_payment_already_recovered(),
        scenarios.trigger_duplicate_action_attempt(),
        scenarios.trigger_retry_budget_exhausted(),
        scenarios.trigger_policy_rejection(),
        scenarios.trigger_crash_mid_execution(),
    ]
    names = [r.scenario for r in results]

    # All names must be non-empty strings
    for name in names:
        assert isinstance(name, str) and name, (
            f"Scenario name must be a non-empty string, got {name!r}"
        )

    # All names must be unique
    assert len(names) == len(set(names)), (
        f"Scenario names must be unique; found duplicates in: {names}"
    )
