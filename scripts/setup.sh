#!/usr/bin/env bash
# =============================================================================
# scripts/setup.sh — Averiq local environment bootstrap
#
# Usage: bash scripts/setup.sh  (or via: make setup)
#
# This script is IDEMPOTENT — safe to run multiple times.
# It will never overwrite an existing .env file.
#
# Requirements: NFR-DEV-003
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colour output helpers
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
RESET='\033[0m'

info()    { echo -e "${CYAN}INFO:${RESET}  $*"; }
success() { echo -e "${GREEN}OK:${RESET}    $*"; }
warn()    { echo -e "${YELLOW}WARN:${RESET}  $*"; }
error()   { echo -e "${RED}ERROR:${RESET} $*" >&2; }

# ---------------------------------------------------------------------------
# Resolve project root (the directory that contains this script's parent)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# Step 1 — .env file handling (never overwrite)
# ---------------------------------------------------------------------------
ENV_FILE="${PROJECT_ROOT}/.env"
ENV_EXAMPLE="${PROJECT_ROOT}/.env.example"

if [[ -f "${ENV_FILE}" ]]; then
    info ".env already exists, skipping copy"
else
    if [[ ! -f "${ENV_EXAMPLE}" ]]; then
        error ".env.example not found at ${ENV_EXAMPLE}. Cannot create .env."
        exit 1
    fi
    cp "${ENV_EXAMPLE}" "${ENV_FILE}"
    success "Created .env from .env.example"
    warn "Please review and update credentials in .env before continuing."
fi

# ---------------------------------------------------------------------------
# Step 2 — Check Docker is running
# ---------------------------------------------------------------------------
info "Checking Docker daemon..."
if ! docker info > /dev/null 2>&1; then
    error "Docker is not running or not accessible."
    error "Please start Docker Desktop (or the Docker daemon) and retry."
    exit 1
fi
success "Docker is running"

# ---------------------------------------------------------------------------
# Step 3 — Start postgres and redis via Docker Compose
# ---------------------------------------------------------------------------
info "Starting postgres and redis containers..."

# Support both Docker Compose V2 (docker compose) and V1 (docker-compose)
if docker compose version > /dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif docker-compose version > /dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
else
    error "Neither 'docker compose' (V2) nor 'docker-compose' (V1) found."
    error "Please install Docker Compose and retry."
    exit 1
fi

cd "${PROJECT_ROOT}"

${COMPOSE_CMD} up -d postgres redis
success "postgres and redis containers started"

# ---------------------------------------------------------------------------
# Step 4 — Wait for postgres to be healthy
# ---------------------------------------------------------------------------
info "Waiting for postgres to be ready..."

POSTGRES_MAX_ATTEMPTS=30
POSTGRES_SLEEP=2
POSTGRES_ATTEMPT=0

while true; do
    POSTGRES_ATTEMPT=$((POSTGRES_ATTEMPT + 1))
    if ${COMPOSE_CMD} exec -T postgres pg_isready -U averiq > /dev/null 2>&1; then
        success "postgres is ready (attempt ${POSTGRES_ATTEMPT}/${POSTGRES_MAX_ATTEMPTS})"
        break
    fi

    if [[ "${POSTGRES_ATTEMPT}" -ge "${POSTGRES_MAX_ATTEMPTS}" ]]; then
        error "postgres did not become ready after ${POSTGRES_MAX_ATTEMPTS} attempts (${POSTGRES_MAX_ATTEMPTS} × ${POSTGRES_SLEEP}s)."
        error "Check 'docker compose logs postgres' for details."
        exit 1
    fi

    warn "postgres not ready yet — attempt ${POSTGRES_ATTEMPT}/${POSTGRES_MAX_ATTEMPTS}. Retrying in ${POSTGRES_SLEEP}s..."
    sleep "${POSTGRES_SLEEP}"
done

# ---------------------------------------------------------------------------
# Step 5 — Wait for redis to be healthy
# ---------------------------------------------------------------------------
info "Waiting for redis to be ready..."

REDIS_MAX_ATTEMPTS=15
REDIS_SLEEP=1
REDIS_ATTEMPT=0

while true; do
    REDIS_ATTEMPT=$((REDIS_ATTEMPT + 1))
    if ${COMPOSE_CMD} exec -T redis redis-cli ping 2>/dev/null | grep -q "PONG"; then
        success "redis is ready (attempt ${REDIS_ATTEMPT}/${REDIS_MAX_ATTEMPTS})"
        break
    fi

    if [[ "${REDIS_ATTEMPT}" -ge "${REDIS_MAX_ATTEMPTS}" ]]; then
        error "redis did not become ready after ${REDIS_MAX_ATTEMPTS} attempts (${REDIS_MAX_ATTEMPTS} × ${REDIS_SLEEP}s)."
        error "Check 'docker compose logs redis' for details."
        exit 1
    fi

    warn "redis not ready yet — attempt ${REDIS_ATTEMPT}/${REDIS_MAX_ATTEMPTS}. Retrying in ${REDIS_SLEEP}s..."
    sleep "${REDIS_SLEEP}"
done

# ---------------------------------------------------------------------------
# Step 6 — Run database migrations
# ---------------------------------------------------------------------------
info "Running database migrations..."
make -C "${PROJECT_ROOT}" migrate
success "Database migrations complete"

# ---------------------------------------------------------------------------
# Step 7 — Seed demo fixtures
# ---------------------------------------------------------------------------
info "Seeding database..."
make -C "${PROJECT_ROOT}" seed
success "Database seeding complete"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
success "Setup complete! Run 'make dev' to start all services."
echo ""
