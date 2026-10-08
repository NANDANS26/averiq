"""GatewayAbstraction — interface for all payment gateway integrations.

CRITICAL: This interface deliberately does NOT expose:
  - retry_payment()       — Razorpay has no API to retry failed standalone payments
  - capture_failed_payment() — MUST NOT call POST /v1/payments/:id/capture on failed payments
  - capture_payment()     — capture is only valid for AUTHORIZED payments, not FAILED ones

RETRY for standalone failed payments = RECOMMENDATION_ONLY.
The InterventionEngine produces a recommendation; the GatewayAbstraction
is never called for this case.

INT-RAZORPAY-006: Never call POST /v1/payments/:id/capture on a payment
with status='failed'. This invariant is enforced by the absence of any
method in this interface that could be misused for that purpose.

Requirements: FR-EXEC-001, INT-RAZORPAY-001 through INT-RAZORPAY-006
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GatewayAbstraction(Protocol):
    """Structural interface for payment gateway integrations."""

    async def create_payment_link(
        self,
        amount_paise: int,
        currency: str,
        description: str,
        customer_email: str | None = None,
        customer_phone: str | None = None,
        callback_url: str | None = None,
        reference_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a payment link. Returns dict with at minimum:
        {
          "id": str,           # Razorpay payment link ID (plink_XXXX)
          "short_url": str,    # Short URL to send to customer
          "status": str,       # "created"
          "amount": int,       # paise
          "currency": str,
        }
        Raises asyncio.TimeoutError on timeout.
        """
        ...

    async def get_payment(self, payment_id: str) -> dict[str, Any]:
        """Fetch payment details by Razorpay payment ID.
        Returns payment object with at minimum: id, status, amount, currency.
        """
        ...

    async def get_payment_link(self, payment_link_id: str) -> dict[str, Any]:
        """Fetch payment link details by ID.
        Returns payment link object with status field.
        """
        ...

    async def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        """Fetch subscription details by ID."""
        ...
