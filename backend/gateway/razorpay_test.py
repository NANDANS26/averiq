"""RazorpayTestGateway — calls real Razorpay test-mode APIs (REAL_TEST_MODE).

Requires: RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET env vars set to test-mode credentials.
All calls are subject to GATEWAY_TIMEOUT_SECONDS (default 30s).
Subject to Razorpay test-mode limits (e.g. 30 payment links per business).

CRITICAL: Never calls POST /v1/payments/:id/capture on a payment with status='failed'.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from backend.config import settings

logger = structlog.get_logger(__name__)

RAZORPAY_BASE_URL = "https://api.razorpay.com/v1"


class RazorpayTestGateway:
    """Calls real Razorpay test-mode APIs."""

    def __init__(self) -> None:
        self._auth = (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
        self._timeout = float(settings.GATEWAY_TIMEOUT_SECONDS)

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
        payload: dict[str, Any] = {
            "amount": amount_paise,    # integer paise — never float
            "currency": currency,
            "description": description,
        }
        if customer_email:
            payload.setdefault("customer", {})["email"] = customer_email
        if customer_phone:
            payload.setdefault("customer", {})["contact"] = customer_phone
        if callback_url:
            payload["callback_url"] = callback_url
        if reference_id:
            payload["reference_id"] = reference_id

        async with httpx.AsyncClient(auth=self._auth, timeout=self._timeout) as client:
            response = await asyncio.wait_for(
                client.post(f"{RAZORPAY_BASE_URL}/payment_links", json=payload),
                timeout=self._timeout,
            )
            response.raise_for_status()
            return dict(response.json())

    async def get_payment(self, payment_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(auth=self._auth, timeout=self._timeout) as client:
            response = await client.get(f"{RAZORPAY_BASE_URL}/payments/{payment_id}")
            response.raise_for_status()
            return dict(response.json())

    async def get_payment_link(self, payment_link_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(auth=self._auth, timeout=self._timeout) as client:
            response = await client.get(f"{RAZORPAY_BASE_URL}/payment_links/{payment_link_id}")
            response.raise_for_status()
            return dict(response.json())

    async def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(auth=self._auth, timeout=self._timeout) as client:
            response = await client.get(f"{RAZORPAY_BASE_URL}/subscriptions/{subscription_id}")
            response.raise_for_status()
            return dict(response.json())
