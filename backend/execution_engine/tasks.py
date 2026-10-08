"""Execution engine Celery tasks.

Task name: backend.execution_engine.tasks.execute_intervention
Queue: default
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog

from backend.config import settings
from backend.database import async_session_factory
from backend.execution_engine.engine import ExecutionEngine
from backend.gateway.fake_razorpay import FakeRazorpayGateway
from backend.gateway.razorpay_test import RazorpayTestGateway
from backend.worker import celery_app

logger = structlog.get_logger(__name__)


def _get_gateway():
    if settings.OPERATING_MODE == "REAL_TEST_MODE":
        return RazorpayTestGateway()
    return FakeRazorpayGateway()


@celery_app.task(
    name="backend.execution_engine.tasks.execute_intervention",
    bind=True,
    queue="default",
    acks_late=True,
)
def execute_intervention(
    self: object,
    case_id: str,
    intervention_id: str,
    merchant_id: str,
) -> dict[str, Any]:
    """Execute an approved intervention."""
    return asyncio.run(_execute_async(case_id, intervention_id, merchant_id))


async def _execute_async(case_id_str: str, intervention_id_str: str, merchant_id_str: str) -> dict[str, Any]:
    gateway = _get_gateway()
    engine = ExecutionEngine(gateway=gateway)

    case_id = uuid.UUID(case_id_str)
    intervention_id = uuid.UUID(intervention_id_str)
    merchant_id = uuid.UUID(merchant_id_str)

    async with async_session_factory() as session:
        async with session.begin():
            return await engine.execute(
                session=session,
                case_id=case_id,
                intervention_id=intervention_id,
                merchant_id=merchant_id,
                actor="SYSTEM",
            )