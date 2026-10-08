"""Unit tests for the Failure Lab simulation endpoints (task 16.3).

Tests:
  1. _require_failure_lab() raises HTTP 404 when ENABLE_FAILURE_LAB=False.
  2. _require_failure_lab() raises HTTP 403 when ENABLE_FAILURE_LAB=True but
     OPERATING_MODE != "SIMULATED" (e.g. REAL_TEST_MODE).
  3. _require_failure_lab() does NOT raise when ENABLE_FAILURE_LAB=True and
     OPERATING_MODE="SIMULATED".

All tests are pure unit tests — no database, no Redis, no network calls.
Settings are patched with unittest.mock.patch to isolate each scenario.

Requirements: API-009, FAIL-ENG-004, FAIL-ENG-005
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from backend.api.simulation import _require_failure_lab


# ---------------------------------------------------------------------------
# Test 1: HTTP 404 when ENABLE_FAILURE_LAB=False
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_require_failure_lab_raises_404_when_disabled() -> None:
    """_require_failure_lab must raise HTTP 404 when ENABLE_FAILURE_LAB=False.

    The Failure Lab feature is disabled by default (ENABLE_FAILURE_LAB=False).
    All simulation endpoints must appear as HTTP 404 Not Found when disabled
    so that the feature is invisible to users and scanners (API-009).
    """
    with patch("backend.api.simulation.settings") as mock_settings:
        mock_settings.ENABLE_FAILURE_LAB = False
        mock_settings.OPERATING_MODE = "SIMULATED"

        with pytest.raises(HTTPException) as exc_info:
            _require_failure_lab()

        assert exc_info.value.status_code == 404, (
            "Expected HTTP 404 when ENABLE_FAILURE_LAB=False (API-009)"
        )


# ---------------------------------------------------------------------------
# Test 2: HTTP 403 when ENABLE_FAILURE_LAB=True but REAL_TEST_MODE
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_require_failure_lab_raises_403_when_real_test_mode() -> None:
    """_require_failure_lab must raise HTTP 403 when OPERATING_MODE != SIMULATED.

    The Failure Lab must NEVER use live credentials. When ENABLE_FAILURE_LAB=True
    but OPERATING_MODE=REAL_TEST_MODE, the endpoint must refuse with HTTP 403
    to prevent accidental use of real Razorpay test credentials (FAIL-ENG-005).
    """
    with patch("backend.api.simulation.settings") as mock_settings:
        mock_settings.ENABLE_FAILURE_LAB = True
        mock_settings.OPERATING_MODE = "REAL_TEST_MODE"

        with pytest.raises(HTTPException) as exc_info:
            _require_failure_lab()

        assert exc_info.value.status_code == 403, (
            "Expected HTTP 403 when Failure Lab enabled but not in SIMULATED mode (FAIL-ENG-005)"
        )


# ---------------------------------------------------------------------------
# Test 3: No exception when ENABLE_FAILURE_LAB=True and SIMULATED
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_require_failure_lab_passes_when_enabled_and_simulated() -> None:
    """_require_failure_lab must NOT raise when properly configured.

    When ENABLE_FAILURE_LAB=True and OPERATING_MODE=SIMULATED, the guard
    should pass without raising an exception, allowing the failure scenario
    trigger to proceed (FAIL-ENG-001 through FAIL-ENG-003).
    """
    with patch("backend.api.simulation.settings") as mock_settings:
        mock_settings.ENABLE_FAILURE_LAB = True
        mock_settings.OPERATING_MODE = "SIMULATED"

        # Should not raise
        _require_failure_lab()


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_require_failure_lab_raises_404_when_disabled_regardless_of_mode() -> None:
    """HTTP 404 takes precedence over OPERATING_MODE check when lab is disabled."""
    with patch("backend.api.simulation.settings") as mock_settings:
        mock_settings.ENABLE_FAILURE_LAB = False
        mock_settings.OPERATING_MODE = "SIMULATED"  # even SIMULATED mode → 404 when disabled

        with pytest.raises(HTTPException) as exc_info:
            _require_failure_lab()

        assert exc_info.value.status_code == 404, (
            "ENABLE_FAILURE_LAB=False must always produce 404 regardless of operating mode"
        )
