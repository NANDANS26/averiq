"""Gateway Abstraction — isolates the system from specific payment provider implementations.

Implementations:
- abstract.py        — GatewayAbstraction interface (no retry_payment method)
- razorpay_test.py   — RazorpayTestGateway: calls real Razorpay test-mode APIs (REAL_TEST_MODE)
- fake_razorpay.py   — FakeRazorpayGateway: deterministic simulation (SIMULATED)

IMPORTANT: GatewayAbstraction must NOT expose retry_payment or capture_failed_payment.
RETRY for standalone failed payments is RECOMMENDATION_ONLY — no gateway call is made.
"""
from backend.gateway.abstract import GatewayAbstraction
from backend.gateway.fake_razorpay import FakeRazorpayGateway
from backend.gateway.razorpay_test import RazorpayTestGateway

__all__ = [
    "GatewayAbstraction",
    "FakeRazorpayGateway",
    "RazorpayTestGateway",
]
