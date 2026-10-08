# =============================================================================
# Averiq — Root Makefile
# Stub targets for local development. Actual implementations are wired up
# progressively as each subsystem is built.
# =============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

# Colour helpers
CYAN  := \033[0;36m
RESET := \033[0m

.PHONY: help setup dev test test-unit test-integration test-e2e test-security \
        lint format migrate seed train evaluate simulate

# ---------------------------------------------------------------------------
# help — print all targets
# ---------------------------------------------------------------------------
help:
	@echo ""
	@echo "  $(CYAN)Averiq — available make targets$(RESET)"
	@echo ""
	@echo "  setup           Bootstrap local environment (first-time)"
	@echo "  dev             Start all services in development mode"
	@echo "  test            Run unit + integration tests"
	@echo "  test-unit       Run unit tests only"
	@echo "  test-integration Run integration tests (requires Docker)"
	@echo "  test-e2e        Run Playwright end-to-end tests"
	@echo "  test-security   Run security test suite"
	@echo "  lint            Lint Python (ruff) and TypeScript (eslint)"
	@echo "  format          Auto-format Python (ruff) and TypeScript (prettier)"
	@echo "  migrate         Run Alembic database migrations"
	@echo "  seed            Seed database with demo fixtures"
	@echo "  train           Train recoverability model on synthetic data"
	@echo "  evaluate        Evaluate model quality and emit comparison table"
	@echo "  simulate        Simulate recovery workflow on test split"
	@echo ""

# ---------------------------------------------------------------------------
# setup — first-time bootstrap
# ---------------------------------------------------------------------------
setup:
	@echo "$(CYAN)Running setup...$(RESET)"
	@bash scripts/setup.sh

# ---------------------------------------------------------------------------
# dev — start all services
# ---------------------------------------------------------------------------
dev:
	@echo "$(CYAN)Starting services in development mode...$(RESET)"
	@docker compose up --build

# ---------------------------------------------------------------------------
# test targets
# ---------------------------------------------------------------------------
test:
	@echo "$(CYAN)Running unit + integration tests...$(RESET)"
	@$(MAKE) test-unit
	@$(MAKE) test-integration

test-unit:
	@echo "$(CYAN)Running unit tests...$(RESET)"
	@python -m pytest tests/unit -v -m unit

test-integration:
	@echo "$(CYAN)Running integration tests (requires running Docker services)...$(RESET)"
	@python -m pytest tests/integration -v -m integration

test-e2e:
	@echo "$(CYAN)Running Playwright E2E tests...$(RESET)"
	@python -m pytest tests/e2e -v -m e2e

test-security:
	@echo "$(CYAN)Running security test suite...$(RESET)"
	@python -m pytest tests/integration/test_security.py -v -m security

# ---------------------------------------------------------------------------
# lint / format
# ---------------------------------------------------------------------------
lint:
	@echo "$(CYAN)Linting Python with ruff...$(RESET)"
	@ruff check backend/ ai/ shared/ tests/
	@echo "$(CYAN)Linting TypeScript with eslint...$(RESET)"
	@cd frontend && npm run lint

format:
	@echo "$(CYAN)Formatting Python with ruff...$(RESET)"
	@ruff format backend/ ai/ shared/ tests/
	@echo "$(CYAN)Formatting TypeScript with prettier...$(RESET)"
	@cd frontend && npm run format

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
migrate:
	@echo "$(CYAN)Running Alembic migrations...$(RESET)"
	@cd backend && alembic upgrade head

seed:
	@echo "$(CYAN)Seeding database with demo fixtures...$(RESET)"
	@python scripts/seed.py

# ---------------------------------------------------------------------------
# AI pipeline
# ---------------------------------------------------------------------------
train:
	@echo "$(CYAN)Training recoverability model...$(RESET)"
	@python ai/training/train.py

evaluate:
	@echo "$(CYAN)Evaluating model quality...$(RESET)"
	@python ai/evaluation/evaluate.py

simulate:
	@echo "$(CYAN)Running recovery simulation on test split...$(RESET)"
	@python ai/evaluation/simulate.py
