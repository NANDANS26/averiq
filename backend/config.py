"""Application configuration — reads all settings from environment variables.

All settings are declared here; no component may read os.environ directly.
Use the module-level ``settings`` singleton for access:

    from backend.config import settings

Sensitive values (APP_SECRET_KEY, DATABASE_URL with credentials) are never
logged or included in API responses.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Averiq application settings.

    Values are loaded from the environment (or a .env file at the project root).
    All fields without defaults are required; the application will fail on startup
    if they are absent.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Extra fields in .env are ignored silently
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Database
    # -------------------------------------------------------------------------
    DATABASE_URL: str = Field(
        ...,
        description="Async PostgreSQL DSN, e.g. postgresql+asyncpg://user:pass@host:5432/db",
    )

    DATABASE_SYNC_URL: str = Field(
        default="",
        description=(
            "Synchronous PostgreSQL DSN for Alembic migrations, "
            "e.g. postgresql+psycopg2://user:pass@host:5432/db. "
            "If empty, derived automatically from DATABASE_URL by replacing the asyncpg driver."
        ),
    )

    # -------------------------------------------------------------------------
    # Environment & Deployment
    # -------------------------------------------------------------------------
    ENVIRONMENT: Literal["development", "test", "simulation", "production"] = Field(
        default="development",
        description="Deployment environment name",
    )

    # -------------------------------------------------------------------------
    # Redis / Celery
    # -------------------------------------------------------------------------
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL used for Celery broker, result backend, and rate limiting",
    )

    CELERY_BROKER_URL: str = Field(
        default="",
        description="Celery broker URL (defaults to REDIS_URL if empty)",
    )

    CELERY_RESULT_BACKEND: str = Field(
        default="",
        description="Celery result backend URL (defaults to REDIS_URL if empty)",
    )

    # -------------------------------------------------------------------------
    # Decision Engine & Model Configuration (Phase 3/4/5 Frozen Contracts)
    # -------------------------------------------------------------------------
    MODEL_ARTIFACT_PATH: str = Field(
        default="model_artifacts/action_value_v3.pkl",
        description="Path to primary action value model artifact",
    )

    MODEL_VERSION: str = Field(
        default="action-value-v3.0-calibrated",
        description="Current active model version string",
    )

    FALLBACK_PROBABILITY: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Frozen fallback base rate probability for unmodeled / failure cases (0.35)",
    )

    VERIFICATION_MAX_ATTEMPTS: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum verification retry attempts before escalating",
    )

    ATTRIBUTION_WINDOW_SECONDS: int = Field(
        default=86400,
        ge=60,
        description="Attribution observation window in seconds (default 24h = 86400)",
    )

    # -------------------------------------------------------------------------
    # Security
    # -------------------------------------------------------------------------
    APP_SECRET_KEY: str = Field(
        ...,
        description=(
            "32-byte hex secret used for webhook secret encryption/decryption and JWT signing. "
            "Never log this value."
        ),
    )

    TOKEN_EXPIRY_SECONDS: int = Field(
        default=3600,
        ge=60,
        description="JWT access token lifetime in seconds (minimum 60)",
    )

    # -------------------------------------------------------------------------
    # Webhook ingestion
    # -------------------------------------------------------------------------
    WEBHOOK_RATE_LIMIT_PER_MINUTE: int = Field(
        default=300,
        ge=1,
        description=(
            "Token-bucket rate limit for webhook POST requests per merchant per minute. "
            "Set conservatively so legitimate Razorpay bursts never hit the limit. "
            "HTTP 429 causes Razorpay to retry per its exponential back-off (up to 24h)."
        ),
    )

    WEBHOOK_RETAIN_RAW_PAYLOAD: bool = Field(
        default=False,
        description=(
            "When True, raw webhook bytes are stored in webhook_raw_payloads with an "
            "expiry date. Disabled by default to minimise PII retention. "
            "Guarded by WEBHOOK_RETAIN_RAW_PAYLOAD flag throughout the codebase."
        ),
    )

    WEBHOOK_RAW_PAYLOAD_RETENTION_DAYS: int = Field(
        default=30,
        ge=1,
        description=(
            "Retention window in days for raw webhook payloads. "
            "Only used when WEBHOOK_RETAIN_RAW_PAYLOAD=true. "
            "Rows with expires_at < NOW() may be purged by a background job."
        ),
    )

    # -------------------------------------------------------------------------
    # Gateway
    # -------------------------------------------------------------------------
    GATEWAY_TIMEOUT_SECONDS: int = Field(
        default=30,
        ge=1,
        description=(
            "asyncio timeout in seconds applied to all GatewayAbstraction calls. "
            "Raises asyncio.TimeoutError when exceeded."
        ),
    )

    # -------------------------------------------------------------------------
    # AI / ML
    # -------------------------------------------------------------------------
    AI_RESPONSE_TIMEOUT_SECONDS: int = Field(
        default=10,
        ge=1,
        description="asyncio.wait_for timeout for external LLM provider calls",
    )
    AI_PROVIDER: Literal["stub", "external_llm"] = Field(
        default="stub",
        description=(
            "AI provider used for failure diagnosis. "
            "stub is deterministic local development mode; "
            "external_llm uses the configured external provider."
        ),
    )

    LLM_API_BASE_URL: str = Field(
        default="",
        description="Base URL for the external LLM provider.",
    )

    LLM_API_KEY: str = Field(
        default="",
        description="API key for the external LLM provider. Never logged.",
    )

    LLM_MODEL_NAME: str = Field(
        default="",
        description="Model name used by the external LLM provider.",
    )

    # -------------------------------------------------------------------------
    # Operating mode
    # -------------------------------------------------------------------------
    OPERATING_MODE: Literal["SIMULATED", "REAL_TEST_MODE"] = Field(
        default="SIMULATED",
        description=(
            "Controls which gateway implementation is active. "
            "SIMULATED uses FakeRazorpayGateway (no external calls). "
            "REAL_TEST_MODE uses RazorpayTestGateway with real Razorpay test-mode credentials."
        ),
    )

    # -------------------------------------------------------------------------
    # Failure Lab (development only)
    # -------------------------------------------------------------------------
    ENABLE_FAILURE_LAB: bool = Field(
        default=False,
        description=(
            "When False, all /api/v1/simulation/* endpoints return HTTP 404. "
            "Must never be True in production or live-mode contexts."
        ),
    )

    # -------------------------------------------------------------------------
    # Razorpay credentials (required for REAL_TEST_MODE)
    # -------------------------------------------------------------------------
    RAZORPAY_KEY_ID: str = Field(
        default="",
        description="Razorpay test-mode API key ID (rzp_test_…). Required when OPERATING_MODE=REAL_TEST_MODE.",
    )

    RAZORPAY_KEY_SECRET: str = Field(
        default="",
        description="Razorpay test-mode API key secret. Required when OPERATING_MODE=REAL_TEST_MODE. Never logged.",
    )

    # -------------------------------------------------------------------------
    # CORS
    # -------------------------------------------------------------------------
    ALLOWED_CORS_ORIGINS: list[str] = Field(
        default=["http://localhost:3000"],
        description=(
            "Explicit allowlist of origins for CORS. "
            "Wildcard with credentials is rejected. Add origins as needed."
        ),
    )

    # -------------------------------------------------------------------------
    # Observability
    # -------------------------------------------------------------------------
    OTEL_EXPORTER_OTLP_ENDPOINT: str = Field(
        default="",
        description=(
            "OpenTelemetry collector OTLP endpoint. "
            "When empty, metrics fall back to structured log emission."
        ),
    )

    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Root log level for structlog",
    )

    # -------------------------------------------------------------------------
    # Dataset / evaluation
    # -------------------------------------------------------------------------
    DATASET_SIZE: int = Field(
        default=50_000,
        ge=100,
        le=250_000,
        description=(
            "Number of synthetic cases to generate for training/evaluation. "
            "Valid production range: 50,000–250,000. Smoke-test range: 100+."
        ),
    )

    BRIER_SCORE_WARNING_THRESHOLD: float = Field(
        default=0.25,
        gt=0.0,
        lt=1.0,
        description=(
            "If the LightGBM model's Brier score on the test split exceeds this threshold, "
            "`make evaluate` emits a structured warning and exits non-zero. Configurable."
        ),
    )

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------
    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Ensure DATABASE_URL uses the asyncpg driver."""
        if v and "asyncpg" not in v and v.startswith("postgresql"):
            # Auto-correct postgresql:// → postgresql+asyncpg://
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @field_validator("DATABASE_SYNC_URL")
    @classmethod
    def validate_database_sync_url(cls, v: str, info: Any) -> str:  # type: ignore[override]
        """Derive sync URL from DATABASE_URL when not explicitly set."""
        if not v:
            async_url = info.data.get("DATABASE_URL", "")
            # Replace asyncpg driver with psycopg2 for Alembic
            v = async_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
            v = v.replace("postgresql://", "postgresql+psycopg2://")
        return v

    @field_validator("CELERY_BROKER_URL")
    @classmethod
    def validate_celery_broker_url(cls, v: str, info: Any) -> str:
        """Fallback to REDIS_URL if empty."""
        if not v:
            return str(info.data.get("REDIS_URL", "redis://localhost:6379/0"))
        return v

    @field_validator("CELERY_RESULT_BACKEND")
    @classmethod
    def validate_celery_result_backend(cls, v: str, info: Any) -> str:
        """Fallback to REDIS_URL if empty."""
        if not v:
            return str(info.data.get("REDIS_URL", "redis://localhost:6379/0"))
        return v

    @field_validator("APP_SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        """Require a non-trivial secret key (minimum 32 characters)."""
        if len(v) < 32:
            msg = "APP_SECRET_KEY must be at least 32 characters long"
            raise ValueError(msg)
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings singleton.

    Use ``get_settings()`` in FastAPI dependency injection and
    ``settings`` for module-level access outside request context.
    """
    return Settings()  # type: ignore[call-arg]


# Module-level singleton for non-DI usage (e.g. Celery worker startup)
settings: Settings = get_settings()
