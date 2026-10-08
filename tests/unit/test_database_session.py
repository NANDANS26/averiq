"""Unit tests for database session exports.

Verifies that the core session factory and engine objects are correctly
exported from backend.database so the rest of the application can import them.

Requirements: DATA-008
"""

import pytest
from backend.database import get_session, Base, async_session_factory, get_engine


@pytest.mark.unit
def test_session_exports_exist():
    assert callable(get_session)
    assert async_session_factory is not None
    assert get_engine is not None
    assert Base is not None
