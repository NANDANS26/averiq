"""Celery application for Averiq async task processing.

The Celery worker processes webhook events from the outbox queue, drives the
Decision Engine, and handles re-verification scheduling. It uses Redis as both
the broker and result backend.
"""

from __future__ import annotations

from celery import Celery
from celery.signals import setup_logging
from celery.utils.log import get_task_logger
from kombu import Exchange, Queue

from backend.config import settings

# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

celery_app = Celery(
    "averiq",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    result_expires=86_400,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "backend.webhook_processor.processor.*": {"queue": "webhook"},
        "backend.verification_engine.*": {"queue": "verification"},
        "backend.decision_engine.*": {"queue": "decisions"},
        "backend.execution_engine.*": {"queue": "default"},
    },
    task_queues=(
        Queue(
            "default",
            Exchange("default"),
            routing_key="default",
            queue_arguments={"x-max-priority": 10},
        ),
        Queue(
            "webhook",
            Exchange("webhook"),
            routing_key="webhook",
            queue_arguments={"x-dead-letter-exchange": "dead_letters"},
        ),
        Queue(
            "verification",
            Exchange("verification"),
            routing_key="verification",
        ),
        Queue(
            "decisions",
            Exchange("decisions"),
            routing_key="decisions",
        ),
        Queue(
            "dead_letters",
            Exchange("dead_letters"),
            routing_key="dead_letters",
        ),
    ),
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
    beat_schedule={
        "outbox-reconciliation": {
            "task": "backend.webhook_processor.outbox.reconcile_outbox",
            "schedule": 30.0,
            "options": {"queue": "default"},
        },
    },
    worker_hijack_root_logger=False,
    worker_redirect_stdouts=False,
)

# ---------------------------------------------------------------------------
# Auto-discover tasks
# ---------------------------------------------------------------------------

celery_app.conf.imports = (
    "backend.webhook_processor.outbox",
    "backend.webhook_processor.processor",
    "backend.decision_engine.tasks",
    "backend.execution_engine.tasks",
    "backend.verification_engine.tasks",
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logger = get_task_logger(__name__)


@setup_logging.connect
def configure_logging(**kwargs: object) -> None:
    pass


# ---------------------------------------------------------------------------
# Health / introspection
# ---------------------------------------------------------------------------


@celery_app.task(name="backend.worker.ping", bind=True)
def ping(self: object) -> str:
    return "pong"