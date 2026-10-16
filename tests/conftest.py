"""
Pytest configuration and global test fixtures for SkyTracker.
Ensures hermetic test execution by resetting API Quota Guardian state between tests.
"""

import pytest
from app import app as flask_app
from services.api_guardian import guardian


@pytest.fixture(autouse=True)
def reset_api_guardian():
    """Reset all API guardian in-memory and database caches and metrics before every test."""
    guardian.clear_cache()
    yield
    guardian.clear_cache()


@pytest.fixture
def client():
    """Universal Flask test client fixture for API and route tests."""
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
