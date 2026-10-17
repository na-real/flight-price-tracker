"""
Pytest configuration and global test fixtures for SkyTracker.
Ensures hermetic test execution by resetting API Quota Guardian state between tests.
"""

import pytest
from app import app as flask_app
from services.api_guardian import guardian


import os

@pytest.fixture(autouse=True)
def reset_api_guardian(monkeypatch):
    """Reset all API guardian in-memory and database caches, cron locks, and metrics before every test, and prevent real network calls."""
    # Ensure tests do not make real external requests with live .env keys unless explicitly mocked
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.delenv("AMADEUS_CLIENT_ID", raising=False)
    monkeypatch.delenv("AMADEUS_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("FLIGHTAPI_API_KEY", raising=False)
    monkeypatch.delenv("PRIMARY_FLIGHT_PROVIDER", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    guardian.clear_cache()
    try:
        from db import db
        conn = db()
        conn.execute("DELETE FROM cron_locks")
        if hasattr(conn, "commit"):
            conn.commit()
        conn.close()
    except Exception:
        pass
    yield
    guardian.clear_cache()
    try:
        from db import db
        conn = db()
        conn.execute("DELETE FROM cron_locks")
        if hasattr(conn, "commit"):
            conn.commit()
        conn.close()
    except Exception:
        pass


@pytest.fixture
def client():
    """Universal Flask test client fixture for API and route tests."""
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
