"""FakeRazorpayGateway — deterministic simulation for SIMULATED mode.

No network calls are made. All responses are deterministic given the inputs.
Safe for CI, demos, and bulk evaluation runs.

All amounts remain integer paise throughout.
"""
from __future__ import annotations

import uuid
from typing import Any


class FakeRazorpayGateway:
    """Deterministic fake for SIMULATED operating mode."""

    async def create_payment_link(
        self,
        amount_paise: int,
        currency: str = "INR",
        description: str = "",
        customer_email: str | None = None,
        customer_phone: str | None = None,
        callback_url: str | None = None,
        reference_id: str | None = None,
    ) -> dict[str, Any]:
        link_id = f"plink_fake_{uuid.uuid4().hex[:16]}"
        return {
            "id": link_id,
            "short_url": f"https://rzp.io/l/{link_id}",
            "status": "created",
            "amount": amount_paise,       # integer paise — never float
            "currency": currency,
            "description": description,
            "reference_id": reference_id,
        }

    async def get_payment(self, payment_id: str) -> dict[str, Any]:
        return {
            "id": payment_id,
            "status": "failed",
            "amount": 0,
            "currency": "INR",
        }

    async def get_payment_link(self, payment_link_id: str) -> dict[str, Any]:
        return {
            "id": payment_link_id,
            "status": "created",
            "amount": 0,
            "currency": "INR",
        }

    async def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        return {
            "id": subscription_id,
            "status": "active",
        }
