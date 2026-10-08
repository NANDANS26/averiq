# =============================================================================
# Averiq — Backend Dockerfile
# =============================================================================
# Multi-stage build:
#   builder  — installs all Python dependencies into a virtual environment
#   runtime  — copies only the venv and source; no build tools in final image
#
# Build context: project root (d:\NANDAN.S\ENGINEERING\INTERNSHIP\RAZOR)
# Docker Compose mounts the project root as the build context so that
# shared/contracts/ is accessible during the build.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install system build dependencies required by asyncpg and cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual environment so the runtime stage copies only the venv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only dependency manifests first (layer cache: only invalidated when
# dependencies change, not on every source file change)
COPY backend/pyproject.toml /build/backend/pyproject.toml

# Install runtime dependencies from backend/pyproject.toml
RUN pip install --upgrade pip==24.0 && \
    pip install -e "/build/backend[dev]"

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app"

# Install runtime-only system libraries (libpq for asyncpg; no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user for security
RUN groupadd --gid 1001 averiq && \
    useradd --uid 1001 --gid averiq --shell /bin/bash --create-home averiq

WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy source code
# Copy shared contracts first (they are a separate package)
COPY shared/ /app/shared/
# Copy backend source
COPY backend/ /app/backend/

# Ensure the non-root user owns the application files
RUN chown -R averiq:averiq /app

USER averiq

# Expose the application port
EXPOSE 8000

# Health check: verify the /health endpoint is responding
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command: start Uvicorn
# Override in docker-compose.yml for the worker/beat services:
#   worker: celery -A backend.worker worker --loglevel=INFO
#   beat:   celery -A backend.worker beat --loglevel=INFO
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
