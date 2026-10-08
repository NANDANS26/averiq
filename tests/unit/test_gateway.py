"""Unit tests for GatewayAbstraction interface and its implementations.

Tests verify:
1. FakeRazorpayGateway satisfies GatewayAbstraction Protocol (isinstance check)
2. create_payment_link returns integer amount (not float)
3. GatewayAbstraction has NO method named retry_payment, capture_failed_payment, or capture_payment
4. FakeRazorpayGateway.create_payment_link returns id starting with "plink_"
5. All abstract method names are exactly: create_payment_link, get_payment,
   get_payment_link, get_subscription

Requirements: FR-EXEC-001, INT-RAZORPAY-001 through INT-RAZORPAY-006
"""
from __future__ import annotations

import inspect

import pytest

from backend.gateway.abstract import GatewayAbstraction
from backend.gateway.fake_razorpay import FakeRazorpayGateway


# ---------------------------------------------------------------------------
# Test 1: FakeRazorpayGateway satisfies GatewayAbstraction Protocol
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fake_razorpay_satisfies_protocol() -> None:
    """FakeRazorpayGateway must be an instance of the GatewayAbstraction runtime_checkable Protocol."""
    gateway = FakeRazorpayGateway()
    assert isinstance(gateway, GatewayAbstraction), (
        "FakeRazorpayGateway does not satisfy the GatewayAbstraction Protocol. "
        "Ensure it implements all required methods."
    )


# ---------------------------------------------------------------------------
# Test 2: create_payment_link returns integer amount (not float)
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_create_payment_link_returns_integer_amount() -> None:
    """Amount in the returned dict must be an int (paise), never a float."""
    gateway = FakeRazorpayGateway()
    result = await gateway.create_payment_link(
        amount_paise=50000,
        currency="INR",
        description="Test payment",
    )
    amount = result["amount"]
    assert isinstance(amount, int), (
        f"create_payment_link returned amount of type {type(amount).__name__!r}; "
        "expected int (paise), never float."
    )
    assert amount == 50000


# ---------------------------------------------------------------------------
# Test 3: GatewayAbstraction has NO forbidden methods
# ---------------------------------------------------------------------------

FORBIDDEN_GATEWAY_METHODS = [
    "retry_payment",
    "capture_failed_payment",
    "capture_payment",
]


@pytest.mark.unit
@pytest.mark.parametrize("method_name", FORBIDDEN_GATEWAY_METHODS)
def test_gateway_abstraction_has_no_forbidden_methods(method_name: str) -> None:
    """GatewayAbstraction must NOT expose retry_payment, capture_failed_payment, or capture_payment.

    RETRY for standalone failed payments = RECOMMENDATION_ONLY.
    Calling POST /v1/payments/:id/capture on a failed payment is a critical bug (INT-RAZORPAY-006).
    """
    assert not hasattr(GatewayAbstraction, method_name), (
        f"GatewayAbstraction must not have a method named '{method_name}'. "
        "RETRY is RECOMMENDATION_ONLY; no gateway call should be made for failed standalone payments."
    )


# ---------------------------------------------------------------------------
# Test 4: create_payment_link returns id starting with "plink_"
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_create_payment_link_id_prefix() -> None:
    """The returned payment link id must start with 'plink_'."""
    gateway = FakeRazorpayGateway()
    result = await gateway.create_payment_link(
        amount_paise=10000,
        currency="INR",
        description="Test",
    )
    link_id = result["id"]
    assert link_id.startswith("plink_"), (
        f"Payment link ID {link_id!r} does not start with 'plink_'."
    )


# ---------------------------------------------------------------------------
# Test 5: GatewayAbstraction has exactly the required methods
# ---------------------------------------------------------------------------

REQUIRED_GATEWAY_METHODS = {
    "create_payment_link",
    "get_payment",
    "get_payment_link",
    "get_subscription",
}


@pytest.mark.unit
def test_gateway_abstraction_method_names() -> None:
    """GatewayAbstraction must have exactly the 4 required methods — no more, no less."""
    # Collect all public async methods defined on the Protocol itself
    protocol_methods = {
        name
        for name, member in inspect.getmembers(GatewayAbstraction)
        if not name.startswith("_") and (inspect.isfunction(member) or inspect.iscoroutinefunction(member))
    }
    assert protocol_methods == REQUIRED_GATEWAY_METHODS, (
        f"GatewayAbstraction methods mismatch.\n"
        f"  Expected: {sorted(REQUIRED_GATEWAY_METHODS)}\n"
        f"  Got:      {sorted(protocol_methods)}"
    )


# ---------------------------------------------------------------------------
# Additional: FakeRazorpayGateway also has no forbidden methods
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("method_name", FORBIDDEN_GATEWAY_METHODS)
def test_fake_gateway_has_no_forbidden_methods(method_name: str) -> None:
    """FakeRazorpayGateway must not expose retry_payment, capture_failed_payment, or capture_payment."""
    gateway = FakeRazorpayGateway()
    assert not hasattr(gateway, method_name), (
        f"FakeRazorpayGateway must not have a method named '{method_name}'."
    )


# ---------------------------------------------------------------------------
# Additional: create_payment_link returns short_url and status="created"
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_create_payment_link_returns_expected_fields() -> None:
    """create_payment_link must return id, short_url, status='created', amount, currency."""
    gateway = FakeRazorpayGateway()
    result = await gateway.create_payment_link(
        amount_paise=99900,
        currency="INR",
        description="Monthly subscription",
        customer_email="customer@example.com",
        reference_id="REF-001",
    )
    assert "id" in result
    assert "short_url" in result
    assert result["status"] == "created"
    assert result["currency"] == "INR"
    assert result["amount"] == 99900
    assert result["reference_id"] == "REF-001"
