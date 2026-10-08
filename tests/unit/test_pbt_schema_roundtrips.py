"""Property-Based Tests: Pydantic Schema Serialization Round-Trips.

Property 7: WebhookEvent Serialization Round-Trip
  For any valid CaseContext (used as the cross-boundary webhook context schema),
  deserialize(serialize(obj)) == obj for both model_dump and model_dump_json paths.

Property 8: AI Output and Policy Config Round-Trips
  For any DiagnosisOutput, double-serialize is stable:
    serialize(deserialize(serialize(obj))) == serialize(obj)
  For any PolicyConfig, deserialize(serialize(obj)) preserves all constraint values.

Feature: averiq, Property 7/8: Serialization Round-Trip
Requirements: NFR-TEST-007, NFR-TEST-008, NFR-TEST-009

**Validates: Requirements NFR-TEST-007, NFR-TEST-008, NFR-TEST-009**
"""
from __future__ import annotations

import datetime

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from shared.contracts.enums import (
    ExecutionCapability,
    FailureCategory,
    InterventionType,
)
from shared.contracts.schemas import (
    CaseContext,
    DiagnosisOutput,
    InterventionCandidate,
    PolicyConfig,
    RecoverabilityFeatures,
)

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

# Fixed UTC timezone for all generated datetimes.
_UTC = datetime.timezone.utc

_case_context_strategy = st.builds(
    CaseContext,
    case_id=st.uuids().map(str),
    merchant_id=st.uuids().map(str),
    payment_id=st.text(min_size=5, max_size=30).map(lambda s: f"pay_{s}"),
    amount_paise=st.integers(min_value=1, max_value=10_000_000),
    currency=st.just("INR"),
    error_code=st.one_of(st.none(), st.text(min_size=1, max_size=50)),
    error_description=st.one_of(st.none(), st.text(min_size=0, max_size=200)),
    failure_category=st.one_of(st.none(), st.sampled_from(FailureCategory)),
    customer_id=st.one_of(st.none(), st.uuids().map(str)),
    subscription_id=st.one_of(st.none(), st.uuids().map(str)),
    previous_failure_count=st.integers(min_value=0, max_value=10),
    created_at=st.datetimes(timezones=st.just(_UTC)),
)

_diagnosis_strategy = st.builds(
    DiagnosisOutput,
    failure_category=st.sampled_from(FailureCategory),
    confidence_score=st.floats(
        min_value=0.0,
        max_value=1.0,
        allow_nan=False,
        allow_infinity=False,
    ),
    ai_source=st.sampled_from(
        ["StubAIProvider", "ExternalLLMProvider", "DeterministicFallback"]
    ),
    reasoning=st.one_of(st.none(), st.text(max_size=500)),
    raw_response=st.one_of(st.none(), st.text(max_size=200)),
)

_policy_config_strategy = st.builds(
    PolicyConfig,
    policy_id=st.uuids().map(str),
    merchant_id=st.uuids().map(str),
    version=st.text(
        min_size=1,
        max_size=10,
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789.",
    ),
    min_confidence=st.floats(
        min_value=0.0,
        max_value=1.0,
        allow_nan=False,
        allow_infinity=False,
    ),
    cooldown_seconds=st.integers(min_value=0, max_value=86400),
    max_retries=st.fixed_dictionaries(
        {
            "RETRY": st.integers(min_value=0, max_value=10),
            "PAYMENT_LINK": st.integers(min_value=0, max_value=5),
        }
    ),
    max_amount=st.fixed_dictionaries(
        {
            "INR": st.integers(min_value=0, max_value=10_000_000),
        }
    ),
    human_approval_threshold=st.integers(min_value=0, max_value=10_000_000),
    attribution_window_seconds=st.integers(min_value=0, max_value=604800),
    exposure_thresholds=st.fixed_dictionaries(
        {
            "HIGH": st.integers(min_value=1000, max_value=1_000_000),
            "MEDIUM": st.integers(min_value=100, max_value=999),
        }
    ),
)


# ---------------------------------------------------------------------------
# Property 7: WebhookEvent (CaseContext) Serialization Round-Trip
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(_case_context_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p7_case_context_dict_round_trip(obj: CaseContext) -> None:
    """
    Feature: averiq, Property 7: WebhookEvent Serialization Round-Trip

    **Validates: Requirements NFR-TEST-007**

    For any valid CaseContext, serializing to a dict and deserializing back
    must produce an equivalent object with all fields preserved — including
    timezone-aware UTC timestamps, integer amounts, optional fields, and enum
    values.

    Round-trip: CaseContext.model_validate(obj.model_dump()) == obj
    """
    serialized = obj.model_dump()
    recovered = CaseContext.model_validate(serialized)
    assert recovered == obj, (
        f"Dict round-trip failed.\nOriginal: {obj!r}\nRecovered: {recovered!r}"
    )


@pytest.mark.pbt
@pytest.mark.unit
@given(_case_context_strategy)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_p7_case_context_json_round_trip(obj: CaseContext) -> None:
    """
    Feature: averiq, Property 7: WebhookEvent Serialization Round-Trip (JSON path)

    **Validates: Requirements NFR-TEST-007**

    For any valid CaseContext, serializing to JSON and deserializing back must
    produce an equivalent object — verifying that datetime precision, enum
    serialization, and nullable fields all survive a JSON round-trip.

    Round-trip: CaseContext.model_validate_json(obj.model_dump_json()) == obj
    """
    json_str = obj.model_dump_json()
    recovered = CaseContext.model_validate_json(json_str)
    assert recovered == obj, (
        f"JSON round-trip failed.\nOriginal: {obj!r}\nRecovered: {recovered!r}"
    )


@pytest.mark.pbt
@pytest.mark.unit
@given(_case_context_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p7_case_context_amount_paise_preserved(obj: CaseContext) -> None:
    """
    Feature: averiq, Property 7: WebhookEvent Serialization Round-Trip — integer amount

    **Validates: Requirements NFR-TEST-007, DATA-001**

    The integer paise amount must survive a JSON round-trip without floating-
    point conversion or precision loss.
    """
    recovered = CaseContext.model_validate_json(obj.model_dump_json())
    assert isinstance(recovered.amount_paise, int), (
        f"amount_paise became non-int after JSON round-trip: {type(recovered.amount_paise)}"
    )
    assert recovered.amount_paise == obj.amount_paise, (
        f"amount_paise changed: {obj.amount_paise} → {recovered.amount_paise}"
    )


@pytest.mark.pbt
@pytest.mark.unit
@given(_case_context_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p7_case_context_datetime_tz_preserved(obj: CaseContext) -> None:
    """
    Feature: averiq, Property 7: WebhookEvent Serialization Round-Trip — timezone

    **Validates: Requirements NFR-TEST-007, DATA-004**

    The timezone-aware UTC timestamp must remain timezone-aware and in UTC
    after a JSON round-trip.
    """
    recovered = CaseContext.model_validate_json(obj.model_dump_json())
    assert recovered.created_at.tzinfo is not None, (
        "created_at lost its timezone info after JSON round-trip"
    )
    assert recovered.created_at == obj.created_at, (
        f"created_at changed: {obj.created_at!r} → {recovered.created_at!r}"
    )


# ---------------------------------------------------------------------------
# Property 8a: DiagnosisOutput Serialization Round-Trip (double-serialize stability)
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(_diagnosis_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p8_diagnosis_output_single_round_trip(obj: DiagnosisOutput) -> None:
    """
    Feature: averiq, Property 8: AI Output Round-Trip — single round-trip

    **Validates: Requirements NFR-TEST-008**

    For any DiagnosisOutput, deserialize(serialize(obj)) must equal the
    original object.

    Round-trip: DiagnosisOutput.model_validate(obj.model_dump()) == obj
    """
    recovered = DiagnosisOutput.model_validate(obj.model_dump())
    assert recovered == obj, (
        f"Single dict round-trip failed.\nOriginal: {obj!r}\nRecovered: {recovered!r}"
    )


@pytest.mark.pbt
@pytest.mark.unit
@given(_diagnosis_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p8_diagnosis_output_double_serialize_stability(obj: DiagnosisOutput) -> None:
    """
    Feature: averiq, Property 8: AI Output Round-Trip — double-serialize stability

    **Validates: Requirements NFR-TEST-008**

    For any DiagnosisOutput, serializing twice must produce identical JSON:
      obj → json1 → obj2 → json2
      json1 == json2

    This proves the serialization form is a fixed point — no information is
    gained or lost on successive round-trips.
    """
    json1 = obj.model_dump_json()
    obj2 = DiagnosisOutput.model_validate_json(json1)
    json2 = obj2.model_dump_json()
    assert json1 == json2, (
        f"Double-serialize instability detected.\n"
        f"First JSON:  {json1}\n"
        f"Second JSON: {json2}"
    )


@pytest.mark.pbt
@pytest.mark.unit
@given(_diagnosis_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p8_diagnosis_output_failure_category_preserved(obj: DiagnosisOutput) -> None:
    """
    Feature: averiq, Property 8: AI Output Round-Trip — FailureCategory enum preserved

    **Validates: Requirements NFR-TEST-008, SEC-AI-001**

    The FailureCategory enum value must survive a JSON round-trip without
    mutation.  This is the critical allowlist value; any corruption here
    would allow invalid categories to bypass the SEC-AI-001 validation gate.
    """
    recovered = DiagnosisOutput.model_validate_json(obj.model_dump_json())
    assert recovered.failure_category == obj.failure_category, (
        f"failure_category changed: {obj.failure_category!r} → {recovered.failure_category!r}"
    )
    assert isinstance(recovered.failure_category, FailureCategory), (
        f"failure_category is not a FailureCategory instance after round-trip: "
        f"{type(recovered.failure_category)}"
    )


# ---------------------------------------------------------------------------
# Property 8b: PolicyConfig Serialization Round-Trip (constraint value preservation)
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(_policy_config_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p8_policy_config_round_trip_preserves_all_values(obj: PolicyConfig) -> None:
    """
    Feature: averiq, Property 8: Policy Config Round-Trip

    **Validates: Requirements NFR-TEST-009**

    For any PolicyConfig, deserialize(serialize(obj)) must preserve ALL
    constraint values identically — min_confidence, cooldown_seconds,
    max_retries, human_approval_threshold, attribution_window_seconds,
    max_amount, and exposure_thresholds.
    """
    recovered = PolicyConfig.model_validate(obj.model_dump())
    assert recovered == obj, (
        f"PolicyConfig dict round-trip failed.\nOriginal: {obj!r}\nRecovered: {recovered!r}"
    )


@pytest.mark.pbt
@pytest.mark.unit
@given(_policy_config_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p8_policy_config_min_confidence_preserved(obj: PolicyConfig) -> None:
    """
    Feature: averiq, Property 8: Policy Config Round-Trip — min_confidence

    **Validates: Requirements NFR-TEST-009, FR-POLICY-002**

    min_confidence must be preserved exactly after a JSON round-trip.
    The confidence threshold gate is a financial safety control — any drift
    here could block or wrongly permit interventions.
    """
    recovered = PolicyConfig.model_validate_json(obj.model_dump_json())
    assert recovered.min_confidence == obj.min_confidence, (
        f"min_confidence changed: {obj.min_confidence!r} → {recovered.min_confidence!r}"
    )


@pytest.mark.pbt
@pytest.mark.unit
@given(_policy_config_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p8_policy_config_cooldown_seconds_preserved(obj: PolicyConfig) -> None:
    """
    Feature: averiq, Property 8: Policy Config Round-Trip — cooldown_seconds

    **Validates: Requirements NFR-TEST-009, FR-POLICY-005**

    cooldown_seconds must be preserved exactly as an integer after round-trip.
    """
    recovered = PolicyConfig.model_validate_json(obj.model_dump_json())
    assert recovered.cooldown_seconds == obj.cooldown_seconds, (
        f"cooldown_seconds changed: {obj.cooldown_seconds!r} → {recovered.cooldown_seconds!r}"
    )
    assert isinstance(recovered.cooldown_seconds, int), (
        f"cooldown_seconds became non-int: {type(recovered.cooldown_seconds)}"
    )


@pytest.mark.pbt
@pytest.mark.unit
@given(_policy_config_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p8_policy_config_max_retries_preserved(obj: PolicyConfig) -> None:
    """
    Feature: averiq, Property 8: Policy Config Round-Trip — max_retries

    **Validates: Requirements NFR-TEST-009, FR-POLICY-004**

    The max_retries dict must be preserved exactly — both keys and integer
    values — after a JSON round-trip.
    """
    recovered = PolicyConfig.model_validate_json(obj.model_dump_json())
    assert recovered.max_retries == obj.max_retries, (
        f"max_retries changed: {obj.max_retries!r} → {recovered.max_retries!r}"
    )
    for key, value in recovered.max_retries.items():
        assert isinstance(value, int), (
            f"max_retries[{key!r}] became non-int after round-trip: {type(value)}"
        )


@pytest.mark.pbt
@pytest.mark.unit
@given(_policy_config_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p8_policy_config_human_approval_threshold_is_integer(obj: PolicyConfig) -> None:
    """
    Feature: averiq, Property 8: Policy Config Round-Trip — human_approval_threshold integer

    **Validates: Requirements NFR-TEST-009, FR-POLICY-007, DATA-001**

    human_approval_threshold is a paise amount and must remain an integer
    (never a float) after JSON round-trip.  Any float conversion here could
    silently alter financial routing decisions.
    """
    recovered = PolicyConfig.model_validate_json(obj.model_dump_json())
    assert recovered.human_approval_threshold == obj.human_approval_threshold, (
        f"human_approval_threshold changed: "
        f"{obj.human_approval_threshold!r} → {recovered.human_approval_threshold!r}"
    )
    assert isinstance(recovered.human_approval_threshold, int), (
        f"human_approval_threshold became non-int: {type(recovered.human_approval_threshold)}"
    )


@pytest.mark.pbt
@pytest.mark.unit
@given(_policy_config_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p8_policy_config_attribution_window_seconds_preserved(obj: PolicyConfig) -> None:
    """
    Feature: averiq, Property 8: Policy Config Round-Trip — attribution_window_seconds

    **Validates: Requirements NFR-TEST-009, FR-ATTR-006**

    attribution_window_seconds must be preserved exactly as an integer after
    a JSON round-trip.
    """
    recovered = PolicyConfig.model_validate_json(obj.model_dump_json())
    assert recovered.attribution_window_seconds == obj.attribution_window_seconds, (
        f"attribution_window_seconds changed: "
        f"{obj.attribution_window_seconds!r} → {recovered.attribution_window_seconds!r}"
    )
    assert isinstance(recovered.attribution_window_seconds, int), (
        f"attribution_window_seconds became non-int: {type(recovered.attribution_window_seconds)}"
    )


@pytest.mark.pbt
@pytest.mark.unit
@given(_policy_config_strategy)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p8_policy_config_json_double_serialize_stability(obj: PolicyConfig) -> None:
    """
    Feature: averiq, Property 8: Policy Config Round-Trip — double-serialize stability

    **Validates: Requirements NFR-TEST-009**

    For any PolicyConfig, serializing twice must produce identical JSON output —
    i.e., the JSON representation is a fixed point.
    """
    json1 = obj.model_dump_json()
    obj2 = PolicyConfig.model_validate_json(json1)
    json2 = obj2.model_dump_json()
    assert json1 == json2, (
        f"PolicyConfig double-serialize instability.\n"
        f"First JSON:  {json1}\n"
        f"Second JSON: {json2}"
    )
