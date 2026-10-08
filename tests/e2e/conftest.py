"""
Playwright E2E test configuration and shared fixtures.

Prerequisites (before running `make test-e2e`):
  - docker compose up   (backend on :8000, frontend on :3000)
  - OPERATING_MODE=SIMULATED in .env
  - make seed           (loads DEMO_FIXTURE records)
"""
import pytest
from playwright.sync_api import Playwright, BrowserContext


# ---------------------------------------------------------------------------
# Browser context — base URL + generous default timeout for CI
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict) -> dict:
    """Override default context args to set base URL and viewport."""
    return {
        **browser_context_args,
        "base_url": "http://localhost:3000",
        "viewport": {"width": 1280, "height": 800},
    }
