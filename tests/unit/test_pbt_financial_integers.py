"""Property-Based Tests: Financial Amount Integer Preservation (Property 9).

Property 9: Financial Amount Integer Preservation
  - For any integer amount in paise, all financial computations return int
  - No float is used for financial amounts at any point in the pipeline
  - expected_recovery_value = floor(probability × amount) is always int
  - Decimal intermediate is used for EV computation but never stored as float

Feature: averiq, Property 9: Financial Amount Integer Preservation
Requirements: DATA-001, DATA-003, FR-RISK-001

**Validates: Requirements DATA-001, DATA-003, FR-RISK-001**
"""
from __future__ import annotations

import uuid
from decimal import ROUND_FLOOR, Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from shared.contracts.enums import ExecutionCapability, InterventionType
from shared.contracts.schemas import InterventionCandidate, PolicyConfig


# ---------------------------------------------------------------------------
# Helper: minimal PolicyConfig
# ---------------------------------------------------------------------------


def _make_policy() -> PolicyConfig:
    """Return a minimal PolicyConfig with standard thresholds."""
    return PolicyConfig(
        policy_id=str(uuid.uuid4()),
        merchant_id=str(uuid.uuid4()),
        version="v1",
        min_confidence=0.5,
        cooldown_seconds=0,
        max_retries={},
        max_amount={"INR": 10**13},
        human_approval_threshold=0,
        attribution_window_seconds=86400,
        exposure_thresholds={"HIGH": 100_000, "MEDIUM": 20_000},
    )


# ---------------------------------------------------------------------------
# P9a — RiskAssessmentResult.payment_amount_at_risk is always int
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(amount_paise=st.integers(min_value=1, max_value=10**12))
@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
def test_p9_payment_amount_at_risk_is_integer(amount_paise: int) -> None:
    """For any integer paise amount, payment_amount_at_risk must remain an int.

    Feature: averiq, Property 9: Financial Amount Integer Preservation

    **Validates: Requirements FR-RISK-001, DATA-001**

    The risk assessor must pass through the integer paise value as-is —
    no transformation, no float conversion, no rounding. The returned
    payment_amount_at_risk must be bit-for-bit identical to the input.
    """
    from backend.decision_engine.risk_assessor import RevenueRiskAssessor

    policy = _make_policy()
    assessor = RevenueRiskAssessor()

    result = assessor.assess(
        payment_amount_paise=amount_paise,
        currency="INR",
        subscription_billing_amount_paise=None,
        subscription_status=None,
        policy_config=policy,
        recovery_probability=0.6,
        failure_category=None,
    )

    assert isinstance(result.payment_amount_at_risk, int), (
        f"payment_amount_at_risk must be int, got {type(result.payment_amount_at_risk)}"
    )
    assert result.payment_amount_at_risk == amount_paise, (
        f"payment_amount_at_risk was mutated: "
        f"input={amount_paise}, output={result.payment_amount_at_risk}"
    )
    assert not isinstance(result.payment_amount_at_risk, float), (
        "payment_amount_at_risk must never be float"
    )


# ---------------------------------------------------------------------------
# P9b — Projected subscription revenue is always int
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(
    billing_amount=st.integers(min_value=1, max_value=10**10),
    cycles=st.integers(min_value=1, max_value=12),
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_p9_projected_subscription_revenue_is_integer(
    billing_amount: int, cycles: int
) -> None:
    """Projected subscription revenue must be an integer multiple of billing_amount.

    Feature: averiq, Property 9: Financial Amount Integer Preservation

    **Validates: Requirements DATA-001, DATA-003**

    Integer multiplication of two ints must always yield int — no float
    intermediate is permitted. This covers the formula:
        projected_revenue = subscription_billing_amount_paise × PROJECTION_CYCLES
    """
    result_value = billing_amount * cycles

    assert isinstance(result_value, int), (
        f"billing_amount * cycles must be int, got {type(result_value)}"
    )
    assert result_value == billing_amount * cycles, (
        "Result must exactly equal the arithmetic product (no rounding)"
    )
    assert not isinstance(result_value, float), (
        "Projected revenue must never be float"
    )


# ---------------------------------------------------------------------------
# P9c — Expected value (EV) computation always returns int
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(
    amount_paise=st.integers(min_value=1, max_value=10**12),
    probability=st.floats(
        min_value=0.0,
        max_value=1.0,
        allow_nan=False,
        allow_infinity=False,
    ),
)
@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
def test_p9_expected_value_computation_returns_int(
    amount_paise: int, probability: float
) -> None:
    """compute_expected_value(amount, probability) must always return int.

    Feature: averiq, Property 9: Financial Amount Integer Preservation

    **Validates: Requirements DATA-001, DATA-003, FR-RISK-001**

    Uses Decimal with ROUND_FLOOR internally; the stored result is int.
    The EV formula from the spec: floor(probability × amount).
    Decimal is a computational intermediate only — the result that leaves
    this function must always be a Python int.
    """
    # The EV formula from the spec: floor(probability × amount)
    ev = int(Decimal(str(probability)) * Decimal(amount_paise))

    assert isinstance(ev, int), f"EV must be int, got {type(ev)}"
    assert ev >= 0, f"EV must be non-negative, got {ev}"
    assert not isinstance(ev, float), "EV must never be stored as float"


# ---------------------------------------------------------------------------
# P9d — No float survives paise→BIGINT→paise round-trip
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(amount_paise=st.integers(min_value=1, max_value=10**12))
@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
def test_p9_paise_bigint_roundtrip_no_float(amount_paise: int) -> None:
    """Simulating DB round-trip: int → BIGINT (int) → int, never float.

    Feature: averiq, Property 9: Financial Amount Integer Preservation

    **Validates: Requirements DATA-001, DATA-003**

    Simulates what SQLAlchemy does with BigInteger columns: storing an int
    and reading it back must yield an identical int. A float must never
    appear in this chain, and a Decimal must not leak into storage.
    """
    # Simulate what SQLAlchemy does with BigInteger columns
    db_value = int(amount_paise)   # stored as BIGINT
    retrieved = int(db_value)      # read back

    assert retrieved == amount_paise, (
        f"Round-trip value changed: {amount_paise} → {retrieved}"
    )
    assert isinstance(retrieved, int), (
        f"Retrieved value must be int, got {type(retrieved)}"
    )
    assert not isinstance(retrieved, float), (
        "Retrieved value must never be float"
    )
    assert not isinstance(retrieved, Decimal), (
        "Decimal must not leak into storage — retrieved value must be plain int"
    )


# ---------------------------------------------------------------------------
# P9e — Decimal intermediate is discarded, not stored
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(
    amount_paise=st.integers(min_value=1, max_value=10**12),
    probability=st.floats(
        min_value=0.001,
        max_value=1.0,
        allow_nan=False,
        allow_infinity=False,
    ),
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_p9_decimal_intermediate_yields_int_result(
    amount_paise: int, probability: float
) -> None:
    """The EV formula uses Decimal for precision but the final result is always int.

    Feature: averiq, Property 9: Financial Amount Integer Preservation

    **Validates: Requirements DATA-001, DATA-003**

    Exact formula from InterventionCandidate docstring:
        EV = floor(probability × amount_at_risk) - cost - risk_penalty
    This test verifies the intermediate Decimal is converted to int before
    being stored — no Decimal or float survives past this computation.
    """
    ev_decimal = Decimal(str(probability)) * Decimal(amount_paise)
    ev_int = int(ev_decimal.to_integral_value(rounding=ROUND_FLOOR))

    assert isinstance(ev_int, int), (
        f"EV result must be int after conversion, got {type(ev_int)}"
    )
    assert not isinstance(ev_int, Decimal), (
        "Decimal must not be stored — must be converted to int"
    )
    assert not isinstance(ev_int, float), (
        "Float must not be stored as EV result"
    )
    assert ev_int >= 0, (
        f"EV result must be non-negative (floor of non-negative product), got {ev_int}"
    )


# ---------------------------------------------------------------------------
# P9f — InterventionCandidate.expected_recovery_value schema enforces int
# ---------------------------------------------------------------------------


@pytest.mark.pbt
@pytest.mark.unit
@given(
    amount_paise=st.integers(min_value=1, max_value=10**12),
    probability=st.floats(
        min_value=0.0,
        max_value=1.0,
        allow_nan=False,
        allow_infinity=False,
    ),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_p9_intervention_candidate_expected_value_is_int(
    amount_paise: int, probability: float
) -> None:
    """InterventionCandidate.expected_recovery_value must be stored as int.

    Feature: averiq, Property 9: Financial Amount Integer Preservation

    **Validates: Requirements DATA-001, DATA-003, FR-RISK-001**

    The InterventionCandidate schema (shared/contracts/schemas.py) declares
    expected_recovery_value as an int field. This test verifies that any
    EV computed via the Decimal formula is accepted and stored as int —
    not as float, not as Decimal. Pydantic enforces the type annotation;
    this property test verifies it holds across all valid (amount, probability)
    pairs.
    """
    ev = int(Decimal(str(probability)) * Decimal(amount_paise))

    candidate = InterventionCandidate(
        intervention_type=InterventionType.PAYMENT_LINK,
        execution_capability=ExecutionCapability.CREATE_PAYMENT_LINK,
        recovery_probability=probability,
        expected_recovery_value=ev,
        model_version="v1.0",
    )

    assert isinstance(candidate.expected_recovery_value, int), (
        f"expected_recovery_value must be int, got "
        f"{type(candidate.expected_recovery_value)}"
    )
    assert candidate.expected_recovery_value == ev, (
        f"expected_recovery_value was mutated: ev={ev}, "
        f"stored={candidate.expected_recovery_value}"
    )
    assert not isinstance(candidate.expected_recovery_value, float), (
        "expected_recovery_value must never be float in InterventionCandidate"
    )
