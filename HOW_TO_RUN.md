# How to Run Averiq

Complete step-by-step guide to running the entire application stack.

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Docker Desktop | 24+ | https://docker.com |
| Python | 3.12+ | https://python.org |
| Node.js | 20+ | https://nodejs.org |
| Git | any | https://git-scm.com |

---

## 1. Clone and First-Time Setup

```bash
git clone <repo-url>
cd averiq

# This single command:
#   - Copies .env.example → .env (if .env doesn't exist)
#   - Starts postgres + redis via Docker Compose
#   - Waits for health checks
#   - Runs all 6 Alembic migrations
#   - Seeds DEMO_FIXTURE data
make setup
```

After `make setup` finishes you will see:
```
✓ Demo fixtures seeded successfully.
  Admin:    admin@averiq.demo / demo_password_123
  Operator: operator@averiq.demo / demo_password_123
```

---

## 2. Start All Services

```bash
make dev
```

This runs `docker compose up --build` which starts:

| Service | Port | What it is |
|---------|------|-----------|
| `postgres` | 5432 | PostgreSQL 15 database |
| `redis` | 6379 | Redis 7 (broker + rate limiter) |
| `backend` | 8000 | FastAPI server (Uvicorn, hot-reload) |
| `worker` | — | Celery worker (webhook, decisions, verification queues) |
| `beat` | — | Celery beat scheduler (outbox reconciliation every 30 s) |
| `frontend` | 3000 | React SPA (Vite dev server with HMR) |

### URLs

| URL | Description |
|-----|-------------|
| http://localhost:3000 | Frontend application |
| http://localhost:8000/api/v1/docs | Interactive API docs (Swagger UI) |
| http://localhost:8000/api/v1/redoc | ReDoc API docs |
| http://localhost:8000/health | Liveness probe |

---

## 3. Running Individual Components

### Backend Only (without Docker)

```bash
cd averiq

# Install backend dependencies
pip install -e "backend/[dev]"

# Start database + redis first
docker compose up -d postgres redis

# Run migrations
make migrate

# Seed demo data
make seed

# Start the API server
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### Celery Workers (without Docker)

```bash
# Worker (processes webhook, decision, verification tasks)
celery -A backend.worker worker --loglevel=INFO --concurrency=4 -Q default,webhook,decisions,verification

# Beat scheduler (outbox reconciliation every 30 s)
celery -A backend.worker beat --loglevel=INFO
```

### Frontend Only (without Docker)

```bash
cd frontend

# Install Node dependencies
npm install

# Start Vite dev server (proxies /api/* to localhost:8000)
npm run dev
```

Vite dev server: http://localhost:5173 (or 3000 in Docker)

---

## 4. ML Pipeline

```bash
# Train both models (LightGBM + Logistic Regression baseline)
# Default: 50,000 synthetic cases, seed=42
make train

# Smaller dataset for local smoke testing
DATASET_SIZE=500 make train

# Evaluate model quality — prints comparison table, exits non-zero if Brier > threshold
make evaluate

# Run simulation on test split — reports Recovery_Rate, IRR, etc.
make simulate
```

Artifacts are saved to `model_artifacts/` as `.pkl` files.

Promote a trained model to active via:
```bash
curl -X POST http://localhost:8000/api/v1/models/{model_id}/promote \
  -H "Authorization: Bearer <admin_token>"
```

---

## 5. Database Migrations

```bash
# Run all pending migrations
make migrate

# This runs: cd backend && alembic upgrade head
```

---

## 6. Seeding Demo Data

```bash
make seed
```

Creates (idempotent — safe to run multiple times):
- 1 demo merchant (SIMULATED mode)
- 1 ADMIN user + 1 OPERATOR user
- 1 active policy (sensible defaults)
- 6 demo revenue risk cases across different states
- Associated demo payments and customers

---

## 7. Running Tests

```bash
# Unit tests only (no Docker required)
make test-unit

# Unit + integration tests
make test

# Integration tests (requires postgres + redis running)
make test-integration

# Security test suite
make test-security

# Playwright E2E (requires all services running on ports 3000 + 8000)
make test-e2e
```

### Setting up Playwright

```bash
# Install Playwright browsers (first time only)
pip install playwright pytest-playwright
playwright install chromium
```

---

## 8. Failure Lab

Enable to test system resilience:

```bash
# In .env
ENABLE_FAILURE_LAB=true
OPERATING_MODE=SIMULATED
```

Then restart services and navigate to http://localhost:3000/failure-lab.

**Warning**: Only use with `OPERATING_MODE=SIMULATED`. Never with live credentials.

---

## 9. Observability (Optional)

Start the OpenTelemetry collector alongside other services:

```bash
docker compose --profile observability up
```

Collector endpoints:
- gRPC: localhost:4317
- HTTP: localhost:4318

---

## 10. Linting and Formatting

```bash
# Lint Python + TypeScript
make lint

# Auto-format Python + TypeScript
make format
```

---

## 11. Environment Variables Reference

See `.env.example` for the complete list with descriptions and defaults.

Critical variables to configure for production-like testing:

```bash
# Required
APP_SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
DATABASE_URL=postgresql+asyncpg://averiq:averiq@localhost:5432/averiq
REDIS_URL=redis://localhost:6379/0

# For REAL_TEST_MODE (optional)
OPERATING_MODE=REAL_TEST_MODE
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
```

---

## 12. Troubleshooting

### "postgres did not become ready"
```bash
docker compose logs postgres
docker compose restart postgres
```

### "Module not found" errors
```bash
# Make sure you're running from the project root
cd <project-root>
pip install -e "backend/[dev]"
```

### Frontend not connecting to backend
The Vite dev proxy forwards `/api` to `http://localhost:8000`. Ensure the backend is running on port 8000.

### Celery tasks not processing
```bash
# Check worker is consuming the right queues
celery -A backend.worker inspect active_queues
```
