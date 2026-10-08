"""Unit tests for OpenTelemetry named metrics (task 19.3).

Tests:
  1. webhook_received() does not raise with or without a running OTel collector.
  2. webhook_missing_event_id() does not raise.
  3. _validate_labels raises ValueError when a forbidden label (case_id) is present.
  4. _validate_labels passes for valid low-cardinality labels.
  5. All 10 expected metric names are registered in the module's EXPECTED_METRIC_NAMES set.

Requirements: NFR-OBS-003, NFR-OBS-004, NFR-OBS-005
"""
from __future__ import annotations

import pytest

from backend.observability.metrics import (
    EXPECTED_METRIC_NAMES,
    FORBIDDEN_METRIC_LABELS,
    _validate_labels,
    webhook_missing_event_id,
    webhook_received,
)


# ---------------------------------------------------------------------------
# Test 1: webhook_received() does not raise
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_webhook_received_does_not_raise() -> None:
    """webhook_received() must never raise, whether OTel is initialized or not.

    The system must degrade gracefully to structured log emission when no
    OTel collector is running (NFR-OBS-004). Missing event_type defaults to ''.
    """
    # Should not raise regardless of OTel collector availability
    webhook_received()
    webhook_received(event_type="payment.failed")
    webhook_received(event_type="subscription.charged")


# ---------------------------------------------------------------------------
# Test 2: webhook_missing_event_id() does not raise
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_webhook_missing_event_id_does_not_raise() -> None:
    """webhook_missing_event_id() must never raise.

    This counter is emitted when the x-razorpay-event-id header is absent
    or blank (task 6.1 degraded-mode path). It must work in log-fallback mode.
    """
    webhook_missing_event_id()
    webhook_missing_event_id(event_type="payment.failed")


# ---------------------------------------------------------------------------
# Test 3: _validate_labels raises ValueError for forbidden label 'case_id'
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_labels_rejects_case_id() -> None:
    """_validate_labels must raise ValueError when 'case_id' is used as a label.

    case_id is a high-cardinality identifier that must only appear in structured
    logs and traces, never as a metric label (NFR-OBS-005).
    """
    with pytest.raises(ValueError, match="high-cardinality"):
        _validate_labels({"case_id": "case_abc123", "event_type": "payment.failed"})


@pytest.mark.unit
def test_validate_labels_rejects_customer_id() -> None:
    """_validate_labels must raise ValueError for other forbidden high-cardinality labels.

    customer_id and payment_id are also forbidden metric label keys (NFR-OBS-005).
    """
    with pytest.raises(ValueError):
        _validate_labels({"customer_id": "cust_xyz"})

    with pytest.raises(ValueError):
        _validate_labels({"payment_id": "pay_xyz"})


# ---------------------------------------------------------------------------
# Test 4: _validate_labels passes for valid low-cardinality labels
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_labels_allows_low_cardinality_labels() -> None:
    """_validate_labels must NOT raise for valid low-cardinality label keys.

    Allowed label keys: event_type, outcome, model_type, action_type,
    intervention_type, failure_reason, rule_name, currency, fallback_reason, etc.
    These have a bounded, small number of distinct values.
    """
    # None of these should raise
    _validate_labels({})
    _validate_labels({"event_type": "payment.failed"})
    _validate_labels({"outcome": "RECOVERED"})
    _validate_labels({"model_type": "lightgbm", "intervention_type": "PAYMENT_LINK"})
    _validate_labels({"rule_name": "confidence_threshold", "intervention_type": "RETRY"})
    _validate_labels({"currency": "INR"})
    _validate_labels({"fallback_reason": "ai_timeout"})
    _validate_labels({"action_type": "PAYMENT_LINK", "failure_reason": "gateway_timeout"})


# ---------------------------------------------------------------------------
# Test 5: All 10 metric names are present in EXPECTED_METRIC_NAMES
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_expected_metric_names_are_registered() -> None:
    """EXPECTED_METRIC_NAMES must contain all 10 required metric names.

    9 metrics from NFR-OBS-003 plus webhook_missing_event_id_total (task 6.1).

    Requirements: NFR-OBS-003
    """
    required = {
        # 9 from NFR-OBS-003
        "webhook_received_total",
        "webhook_duplicate_total",
        "decision_latency_seconds",
        "model_inference_latency_seconds",
        "action_success_total",
        "action_failure_total",
        "policy_block_total",
        "recovered_revenue_total",
        "ai_fallback_total",
        # additional metric from task 6.1
        "webhook_missing_event_id_total",
    }

    missing = required - EXPECTED_METRIC_NAMES
    assert not missing, (
        f"The following required metric names are missing from EXPECTED_METRIC_NAMES: {missing}"
    )
    assert len(EXPECTED_METRIC_NAMES) == 10, (
        f"Expected exactly 10 metric names, got {len(EXPECTED_METRIC_NAMES)}: "
        f"{sorted(EXPECTED_METRIC_NAMES)}"
    )


@pytest.mark.unit
def test_forbidden_metric_labels_contains_required_keys() -> None:
    """FORBIDDEN_METRIC_LABELS must include case_id, customer_id, and payment_id.

    Requirements: NFR-OBS-005
    """
    assert "case_id" in FORBIDDEN_METRIC_LABELS
    assert "customer_id" in FORBIDDEN_METRIC_LABELS
    assert "payment_id" in FORBIDDEN_METRIC_LABELS
