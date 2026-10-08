"""Verify GatewayAbstraction has no retry_payment or capture_failed_payment methods.

These methods must not exist on the gateway abstraction or any implementation
because retrying a failed standalone payment is RECOMMENDATION_ONLY and
calling capture on a failed payment would be a critical bug (INT-RAZORPAY-006).

Requirements: FR-EXEC-001, INT-RAZORPAY-006
"""
from __future__ import annotations

import inspect

import pytest

from backend.gateway.abstract import GatewayAbstraction
from backend.gateway.fake_razorpay import FakeRazorpayGateway


@pytest.mark.unit
def test_gateway_interface_has_no_retry_payment() -> None:
    """GatewayAbstraction must not expose a retry_payment method."""
    assert not hasattr(GatewayAbstraction, "retry_payment")


@pytest.mark.unit
def test_gateway_interface_has_no_capture_failed_payment() -> None:
    """GatewayAbstraction must not expose a capture_failed_payment method."""
    assert not hasattr(GatewayAbstraction, "capture_failed_payment")


@pytest.mark.unit
def test_fake_gateway_has_no_retry_payment() -> None:
    """FakeRazorpayGateway must not expose a retry_payment method."""
    gw = FakeRazorpayGateway()
    assert not hasattr(gw, "retry_payment")


@pytest.mark.unit
def test_no_code_calls_capture_on_failed_payment() -> None:
    """GatewayAbstraction method list contains no dangerous capture methods.

    Verify that GatewayAbstraction does not expose capture_payment,
    capture_failed_payment, or retry_payment — all of which could cause
    a critical bug if called on a failed payment (INT-RAZORPAY-006).
    """
    method_names = [
        name
        for name, _ in inspect.getmembers(GatewayAbstraction, predicate=inspect.isfunction)
    ]
    forbidden = ["capture_payment", "capture_failed_payment", "retry_payment"]
    for f in forbidden:
        assert f not in method_names, (
            f"GatewayAbstraction must not expose {f!r}"
        )
