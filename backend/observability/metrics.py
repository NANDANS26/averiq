"""OpenTelemetry named metrics for Averiq.

Registers all 9 required metrics plus webhook_missing_event_id_total.
All labels are low-cardinality — never case_id, customer_id, or payment_id.

Falls back to structured log emission when no OTel collector is running.

Requirements: NFR-OBS-003, NFR-OBS-004, NFR-OBS-005
"""
from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Low-cardinality label constants (NFR-OBS-005)
# ---------------------------------------------------------------------------

# Labels that are NEVER allowed on metrics (would create high cardinality)
FORBIDDEN_METRIC_LABELS: frozenset[str] = frozenset({
    "case_id", "customer_id", "payment_id", "user_id", "webhook_event_id",
})

# All 10 expected metric names (9 from NFR-OBS-003 + webhook_missing_event_id_total)
EXPECTED_METRIC_NAMES: frozenset[str] = frozenset({
    "webhook_received_total",
    "webhook_duplicate_total",
    "webhook_missing_event_id_total",
    "decision_latency_seconds",
    "model_inference_latency_seconds",
    "action_success_total",
    "action_failure_total",
    "policy_block_total",
    "recovered_revenue_total",
    "ai_fallback_total",
})


def _validate_labels(labels: dict[str, str]) -> None:
    """Raise ValueError if any forbidden high-cardinality label is used.

    Requirements: NFR-OBS-005 — all metric labels must be low-cardinality.
    Never use case_id, customer_id, or payment_id as metric labels.
    """
    bad = set(labels.keys()) & FORBIDDEN_METRIC_LABELS
    if bad:
        raise ValueError(
            f"Metric labels contain high-cardinality keys: {bad}. "
            "Never use case_id, customer_id, or payment_id as metric labels (NFR-OBS-005)."
        )


# ---------------------------------------------------------------------------
# OTel initialization (best-effort — falls back to log when unavailable)
# ---------------------------------------------------------------------------

_meter: Any = None
_metrics: dict[str, Any] = {}


def _init_meter() -> None:
    """Initialize the OTel meter and register all named counters/histograms.

    Requirements: NFR-OBS-003 (named metrics), NFR-OBS-004 (OTel-compatible format).
    Falls back gracefully to structured log emission when no collector is running.
    """
    global _meter, _metrics
    try:
        from opentelemetry import metrics as otel_metrics  # type: ignore[import]
        from opentelemetry.sdk.metrics import MeterProvider  # type: ignore[import]
        from opentelemetry.sdk.metrics.export import (  # type: ignore[import]
            ConsoleMetricExporter,
            PeriodicExportingMetricReader,
        )

        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (  # type: ignore[import]
                    OTLPMetricExporter,
                )
                exporter: Any = OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True)
            except Exception:
                exporter = ConsoleMetricExporter()
        else:
            exporter = ConsoleMetricExporter()

        reader = PeriodicExportingMetricReader(exporter, export_interval_millis=60_000)
        provider = MeterProvider(metric_readers=[reader])
        otel_metrics.set_meter_provider(provider)
        _meter = otel_metrics.get_meter("averiq", version="0.1.0")

        # --- Counters ---

        _metrics["webhook_received_total"] = _meter.create_counter(
            "webhook_received_total",
            description="Total webhook events received (NFR-OBS-003)",
        )
        _metrics["webhook_duplicate_total"] = _meter.create_counter(
            "webhook_duplicate_total",
            description="Total duplicate webhook events (deduplication fired)",
        )
        _metrics["webhook_missing_event_id_total"] = _meter.create_counter(
            "webhook_missing_event_id_total",
            description=(
                "Total webhooks received without x-razorpay-event-id header "
                "(task 6.1 degraded-mode path)"
            ),
        )
        _metrics["action_success_total"] = _meter.create_counter(
            "action_success_total",
            description="Total external actions that completed with SUCCEEDED status",
        )
        _metrics["action_failure_total"] = _meter.create_counter(
            "action_failure_total",
            description="Total external actions that completed with FAILED status",
        )
        _metrics["policy_block_total"] = _meter.create_counter(
            "policy_block_total",
            description="Total interventions blocked by Policy_Engine rules",
        )
        _metrics["recovered_revenue_total"] = _meter.create_counter(
            "recovered_revenue_total",
            description="Total recovered revenue in minor currency units (paise)",
        )
        _metrics["ai_fallback_total"] = _meter.create_counter(
            "ai_fallback_total",
            description=(
                "Total times DeterministicFallback was invoked instead of AI provider"
            ),
        )

        # --- Histograms ---

        _metrics["decision_latency_seconds"] = _meter.create_histogram(
            "decision_latency_seconds",
            description="Latency of the full decision pipeline (OBSERVED → ACTION_APPROVED)",
            unit="s",
        )
        _metrics["model_inference_latency_seconds"] = _meter.create_histogram(
            "model_inference_latency_seconds",
            description="Latency of recoverability model predict() call",
            unit="s",
        )

        logger.info("otel_metrics.initialized", exporter=type(exporter).__name__)

    except Exception as exc:
        logger.warning(
            "otel_metrics.initialization_failed",
            error=str(exc),
            note=(
                "Falling back to structured log emission for metrics (NFR-OBS-004). "
                "This is expected when no OTel collector is running."
            ),
        )
        _meter = None


# Initialize on module import
_init_meter()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _emit_counter(name: str, value: int = 1, labels: dict[str, str] | None = None) -> None:
    """Emit a counter — via OTel if available, else structured log (NFR-OBS-004)."""
    labels = labels or {}
    _validate_labels(labels)

    counter = _metrics.get(name)
    if counter is not None:
        counter.add(value, labels)
    else:
        logger.info(f"metric.{name}", value=value, **labels)


def _emit_histogram(name: str, value: float, labels: dict[str, str] | None = None) -> None:
    """Emit a histogram observation — via OTel if available, else structured log (NFR-OBS-004)."""
    labels = labels or {}
    _validate_labels(labels)

    histogram = _metrics.get(name)
    if histogram is not None:
        histogram.record(value, labels)
    else:
        logger.info(f"metric.{name}", value=value, **labels)


# ---------------------------------------------------------------------------
# Named metric functions (public API)
# All labels are low-cardinality only (NFR-OBS-005).
# High-cardinality identifiers (case_id, customer_id, payment_id) MUST NOT
# be passed as labels — they belong in structured log fields only.
# ---------------------------------------------------------------------------


def webhook_received(event_type: str = "") -> None:
    """Increment webhook_received_total.

    Labels: event_type (e.g. 'payment.failed', 'subscription.charged')
    Requirements: NFR-OBS-003
    """
    _emit_counter("webhook_received_total", labels={"event_type": event_type})


def webhook_duplicate(event_type: str = "") -> None:
    """Increment webhook_duplicate_total when deduplication fires.

    Labels: event_type
    Requirements: NFR-OBS-003
    """
    _emit_counter("webhook_duplicate_total", labels={"event_type": event_type})


def webhook_missing_event_id(event_type: str = "") -> None:
    """Increment webhook_missing_event_id_total.

    Emitted when the x-razorpay-event-id header is absent or blank (task 6.1
    degraded-mode path). The handler falls back to an entity-based dedup key.

    Labels: event_type
    """
    _emit_counter("webhook_missing_event_id_total", labels={"event_type": event_type})


def decision_latency(seconds: float, outcome: str = "") -> None:
    """Record decision pipeline latency in seconds.

    Labels: outcome (e.g. 'RECOVERED', 'STOPPED', 'ESCALATED')
    Requirements: NFR-OBS-003
    """
    _emit_histogram("decision_latency_seconds", seconds, labels={"outcome": outcome})


def model_inference_latency(seconds: float, model_type: str = "") -> None:
    """Record recoverability model predict() latency in seconds.

    Labels: model_type (e.g. 'lightgbm', 'logistic_regression')
    Requirements: NFR-OBS-003
    """
    _emit_histogram(
        "model_inference_latency_seconds", seconds, labels={"model_type": model_type}
    )


def action_success(action_type: str = "", intervention_type: str = "") -> None:
    """Increment action_success_total when an external operation completes with SUCCEEDED.

    Labels: action_type, intervention_type (both low-cardinality)
    Requirements: NFR-OBS-003
    """
    _emit_counter(
        "action_success_total",
        labels={"action_type": action_type, "intervention_type": intervention_type},
    )


def action_failure(action_type: str = "", failure_reason: str = "") -> None:
    """Increment action_failure_total when an external operation completes with FAILED.

    Labels: action_type, failure_reason (e.g. 'gateway_timeout', 'gateway_error')
    Requirements: NFR-OBS-003
    """
    _emit_counter(
        "action_failure_total",
        labels={"action_type": action_type, "failure_reason": failure_reason},
    )


def policy_block(rule_name: str = "", intervention_type: str = "") -> None:
    """Increment policy_block_total when a Policy_Engine rule fires a BLOCKED decision.

    Labels: rule_name (the blocking rule), intervention_type
    Requirements: NFR-OBS-003
    """
    _emit_counter(
        "policy_block_total",
        labels={"rule_name": rule_name, "intervention_type": intervention_type},
    )


def recovered_revenue(amount_paise: int, currency: str = "INR") -> None:
    """Increment recovered_revenue_total by the recovered amount in minor currency units.

    Labels: currency (low-cardinality ISO 4217 code, e.g. 'INR')
    Requirements: NFR-OBS-003
    """
    _emit_counter("recovered_revenue_total", value=amount_paise, labels={"currency": currency})


def ai_fallback(fallback_reason: str = "") -> None:
    """Increment ai_fallback_total when DeterministicFallback is invoked.

    Labels: fallback_reason (e.g. 'ai_timeout', 'schema_validation_failure', 'circuit_open')
    Requirements: NFR-OBS-003
    """
    _emit_counter("ai_fallback_total", labels={"fallback_reason": fallback_reason})
