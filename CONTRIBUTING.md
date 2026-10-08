# Contributing to Averiq

Thank you for your interest in contributing to **Averiq** — AI Revenue Recovery Decision Engine.

Averiq is an engineering simulation and decision platform designed for high-reliability financial systems, counterfactual uplift modeling, and resilient payment recovery orchestration.

---

## 1. Code of Conduct

All contributors and maintainers are expected to maintain a professional, respectful, and collaborative environment.

---

## 2. Core Architectural Principles & Invariants

Before proposing any changes, please note the following **frozen architectural invariants**:

1. **Integer-Paise Monetary Representation**:
   - All financial amounts (transaction amount, gross recovery, net surplus, action costs) are represented strictly as 64-bit integer paise (`BigInteger` in PostgreSQL).
   - Zero floating-point arithmetic is permitted in monetary persistence or ledger accounting.
2. **Canonical Economic Definitions (Phase 3.6)**:
   - Incremental Recovery: $(\hat{Y}_{\text{selected}} - \hat{Y}_0) \times \text{Amount}$
   - Net Incremental Surplus: $\text{Incremental Recovery} - \text{Cost}$
   - Mandatory control baseline: $P_0$ (`NO_INTERVENTION`).
   - Unclamped accounting: Underperforming actions yield negative surplus honestly.
3. **Model Fallback Guarantee**:
   - The engine must fall back unconditionally to empirical baseline $P_0 = 0.35$ if ML models or features are missing.
4. **Separation of Policy and ML**:
   - ML models output calibrated probabilities $\hat{Y}$.
   - Deterministic safety policies and hard rules have absolute veto power over ML suggestions.
5. **Idempotency & Concurrency**:
   - All external execution commands must acquire distributed Redis locks with TTL and token verification.
   - Outbox tasks and webhook handlers must verify idempotency keys before executing side effects.

---

## 3. Development Setup

### Prerequisites
- Python 3.12+
- Node.js 18+ and npm
- PostgreSQL 18
- Redis 6379

### Environment Setup

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd averiq
   ```

2. **Python Virtual Environment**:
   ```bash
   python -m venv .venv
   # Windows PowerShell
   .\.venv\Scripts\Activate.ps1
   # Linux/macOS
   source .venv/bin/activate

   pip install -e ".[dev]"
   ```

3. **Configure Environment Variables**:
   ```bash
   cp .env.example .env
   # Edit .env with your local PostgreSQL and Redis credentials
   ```

4. **Frontend Setup**:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

---

## 4. Testing & Verification Requirements

Every Pull Request must pass the full verification matrix:

1. **Unit & Integration Tests**:
   ```bash
   pytest tests/unit/ tests/integration/ -m "not slow" -q
   ```
   All 704+ tests must pass with 0 failures.

2. **Phase 4 E2E Verification Matrix**:
   ```bash
   python -m backend.simulation.full_verification_matrix
   ```
   All 15 execution scenarios must reach 100% terminal state verification.

3. **Frontend Build & Typecheck**:
   ```bash
   cd frontend
   npm run build
   ```
   Must compile with 0 TypeScript and build errors.

4. **Code Quality**:
   ```bash
   ruff check .
   ruff format --check .
   ```

---

## 5. Pull Request Guidelines

- **Atomic Commits**: Write clear, descriptive commit messages adhering to Conventional Commits (e.g., `feat: ...`, `fix: ...`, `docs: ...`, `test: ...`).
- **Evidence-Based PRs**: Include test outputs and scenario execution summaries in your PR description.
- **Do Not Break Contracts**: Never alter established database schemas without backward-compatible Alembic migrations.
