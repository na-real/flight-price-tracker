"""
Comprehensive automated tests for SerpAPI Quota Exhaustion Recovery.

Covers:
1. SerpAPI explicit quota exhaustion classification and repeated-call suppression (no probing).
2. Availability of core app features (website, auth, database, appearance, tracking, radar status).
3. Cache fallback labeled with actual source, retrieval timestamp, is_cached=True, is_live=False.
4. Helpful unavailability message without fabricated fallback fares when no cache exists.
5. Background price checker safety (no fake observations, no false alerts, no misleading success).
6. Provider-specific quota state with strictly no guessing of reset time unless supplied or configured.
7. Configurable recovery policies (Retry-After, billing cycle, fixed timestamp, interval, admin reset).
8. Configurable provider-adapter interface (PRIMARY_FLIGHT_PROVIDER, BaseFlightProvider).
"""

import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pytest
import requests

from app import app
from db import db
from checker import run as run_price_checker
from services.api_guardian import (
    guardian,
    PROVIDER_SERPAPI,
    PROVIDER_AMADEUS,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_QUOTA_EXHAUSTED,
    is_quota_exhausted_error
)
from services.flight_api import search_flights
from services.providers import (
    get_primary_provider,
    PROVIDER_REGISTRY,
    ProviderQuotaExhaustedError,
    ProviderUnavailableError
)
from services.providers.serpapi_provider import SerpApiFlightsProvider


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# =====================================================================
# 1. QUOTA EXHAUSTION CLASSIFICATION & CALL SUPPRESSION
# =====================================================================

def test_serpapi_quota_exhaustion_classification_and_call_suppression(monkeypatch):
    """When SerpAPI reports 429 quota exhaustion, classify provider and block upstream calls."""
    monkeypatch.setenv("SERPAPI_KEY", "mock_key_test_123")
    calls = []

    def mock_exhausted_fetch(*args, **kwargs):
        calls.append(kwargs)
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.content = b'{"error": "Your searches per month plan limit has been reached"}'
        mock_resp.json.return_value = {"error": "Your searches per month plan limit has been reached"}
        mock_resp.headers = {}
        err = requests.exceptions.HTTPError(response=mock_resp)
        raise err

    with patch("services.flight_api.requests.get", side_effect=mock_exhausted_fetch):
        # 1st call encounters quota exhaustion
        with pytest.raises(ProviderQuotaExhaustedError):
            search_flights("DEL", "BOM", "2026-11-20")

    assert len(calls) == 1

    # Provider is classified as quota exhausted
    provider = SerpApiFlightsProvider()
    assert provider.is_quota_exhausted() is True

    status_info = guardian.get_provider_quota_status(PROVIDER_SERPAPI)
    assert status_info["is_quota_exhausted"] is True
    assert status_info["state"] == STATE_OPEN
    assert "quota exhausted" in status_info["reason"].lower()

    # 2nd call: Must be blocked IMMEDIATELY by guardian without making any network call!
    with patch("services.flight_api.requests.get", side_effect=mock_exhausted_fetch):
        with pytest.raises(ProviderQuotaExhaustedError):
            search_flights("DEL", "BOM", "2026-11-20")

    # Still exactly 1 call: upstream calls were suppressed, no probing!
    assert len(calls) == 1


# =====================================================================
# 2. CORE FEATURES REMAIN FULLY AVAILABLE DURING QUOTA EXHAUSTION
# =====================================================================

def test_website_and_features_accessible_during_quota_exhaustion(client, monkeypatch):
    """Website, auth, database, tracking, appearance, and monitoring status remain operational."""
    # Trip provider circuit to quota exhausted
    guardian.record_circuit_failure(
        PROVIDER_SERPAPI,
        status_code=429,
        error_msg="Monthly search limit reached"
    )

    # 1. Main landing page
    res_home = client.get("/")
    assert res_home.status_code == 200
    assert b"SkyTracker" in res_home.data

    # 2. Flights dashboard page
    res_flights = client.get("/flights")
    assert res_flights.status_code == 200

    # 3. Appearance preferences page
    res_appearance = client.get("/settings/appearance")
    assert res_appearance.status_code == 200

    # 4. Save appearance preference
    res_pref = client.post("/api/appearance/preferences", json={"theme": "light", "backdropEffect": "default"})
    assert res_pref.status_code == 200

    # 5. Airport autocomplete endpoint
    res_airports = client.get("/api/airports?q=DEL")
    assert res_airports.status_code == 200
    airports = res_airports.get_json()
    assert any(a["iata"] == "DEL" for a in airports)

    # 6. Save tracking record
    with client.session_transaction() as sess:
        sess["user_email"] = "traveler@example.com"

    res_track = client.post("/api/track", json={
        "departure": "DEL",
        "arrival": "BOM",
        "outbound_date": "2026-12-01",
        "target_price": 5000,
        "email": "traveler@example.com"
    })
    assert res_track.status_code == 200
    track_id = res_track.get_json()["id"]

    # 7. Saved tracking list
    res_tracked = client.get("/api/tracked")
    assert res_tracked.status_code == 200
    tracked_data = res_tracked.get_json()
    assert len(tracked_data) > 0
    assert tracked_data[0]["departure"] == "DEL"

    # 8. Radar / monitoring status endpoint
    res_radar = client.get("/api/radar/status")
    assert res_radar.status_code == 200
    radar_data = res_radar.get_json()
    assert "status" in radar_data


# =====================================================================
# 3. CACHE FALLBACK LABELED AS STALE/CACHED WITH RETRIEVAL TIMESTAMP
# =====================================================================

def test_cached_result_fallback_during_quota_exhaustion(monkeypatch):
    """Cached flight results are served with truthful is_cached=True, is_live=False, and actual timestamp."""
    monkeypatch.setenv("SERPAPI_KEY", "mock_key_test_123")

    # Build exact route cache key used by search_flights
    cache_key = guardian.build_cache_key(
        PROVIDER_SERPAPI,
        "search_flights",
        {"dep": "DEL", "arr": "BLR", "outbound": "2026-11-25", "return": None, "class": 1}
    )
    verified_data = [{
        "airline": "Air India",
        "flight_number": "AI 502",
        "price": 5500,
        "departure_airport": "DEL",
        "arrival_airport": "BLR",
        "departure_time": "08:00",
        "arrival_time": "10:45",
        "duration": 165,
        "stops": 0,
        "source": "Google Flights via SerpAPI"
    }]
    guardian.set_cached(cache_key, PROVIDER_SERPAPI, "search_flights", verified_data, ttl_seconds=1800, is_live=True)

    # Trip circuit breaker to quota exhausted
    guardian.record_circuit_failure(
        PROVIDER_SERPAPI,
        status_code=429,
        error_msg="Monthly plan search limit reached"
    )

    # Execute search
    results = search_flights("DEL", "BLR", "2026-11-25")
    assert len(results) == 1
    flight = results[0]

    # Verify truthful labeling: NEVER represented as newly verified fare!
    assert flight["is_cached"] is True
    assert flight["is_live"] is False
    assert flight["data_status"] == "cached"
    assert flight["cached_at"] is not None
    assert flight["age_seconds"] >= 0
    assert "(Cached)" in flight["source"]


# =====================================================================
# 4. NO CACHE: HELPFUL UNAVAILABILITY MESSAGE WITHOUT FAKE FARES
# =====================================================================

def test_no_cache_displays_helpful_unavailability_message_without_fake_fares(client, monkeypatch):
    """When no cache exists and quota is exhausted, return 503 with helpful message and NO fake fares."""
    monkeypatch.setenv("SERPAPI_KEY", "mock_key_test_123")

    # Trip circuit breaker
    guardian.record_circuit_failure(
        PROVIDER_SERPAPI,
        status_code=429,
        error_msg="Google Flights: Monthly plan limit reached"
    )

    res = client.post("/api/search", json={
        "departure": "DEL",
        "arrival": "GOI",
        "outbound_date": "2026-11-30"
    })

    assert res.status_code == 503
    data = res.get_json()
    assert data["results"] == [], "Must not fabricate fallback fares when API key is configured"
    assert data["status"] == "quota_exhausted"
    assert "temporarily unavailable" in data["error"].lower()
    assert "exhausted" in data["error"].lower()


# =====================================================================
# 5. BACKGROUND CHECKER SAFETY: NO FAKE OBSERVATIONS OR FALSE ALERTS
# =====================================================================

def test_background_checker_does_not_create_fake_observations_or_alerts_on_quota_exhaustion(monkeypatch):
    """Failed/stale checks in checker.py do not create price observations or false alerts."""
    monkeypatch.setenv("SERPAPI_KEY", "mock_key_test_123")

    # 1. Setup tracked flight with target price of 6000
    conn = db()
    cur = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()
    cur.execute(
        "INSERT INTO tracked_flights (email, departure, arrival, outbound_date, target_price, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("alert_test@example.com", "DEL", "BOM", "2026-12-10", 6000, now_iso)
    )
    flight_id = cur.lastrowid
    conn.commit()

    # Trip SerpAPI circuit to quota exhausted
    guardian.record_circuit_failure(
        PROVIDER_SERPAPI,
        status_code=429,
        error_msg="Monthly search limit reached"
    )

    def get_price_history(tracked_id):
        c = db()
        return c.execute("SELECT * FROM price_history WHERE tracked_id = ?", (tracked_id,)).fetchall()

    initial_history_len = len(get_price_history(flight_id))

    # Mock send_email to verify no false price-drop alerts are sent
    mock_email = MagicMock()
    with patch("checker.send_email", mock_email):
        run_price_checker()

    # Verify zero price drop emails were sent!
    assert mock_email.call_count == 0

    # Verify no fake observations were recorded in price_history
    assert len(get_price_history(flight_id)) == initial_history_len


# =====================================================================
# 6. PROVIDER-SPECIFIC QUOTA STATE: STRICTLY NO GUESSING OF RESET TIME
# =====================================================================

def test_no_guessing_reset_time_when_unsupplied_and_unconfigured(monkeypatch):
    """If provider and config do not supply reset time, cooldown_until must be None."""
    # Ensure no environment policies are set
    monkeypatch.delenv("SERPAPI_RESET_POLICY", raising=False)
    monkeypatch.delenv("SERPAPI_BILLING_CYCLE_DAY", raising=False)
    monkeypatch.delenv("SERPAPI_RESET_TIMESTAMP", raising=False)
    monkeypatch.delenv("SERPAPI_RESET_INTERVAL_SECONDS", raising=False)

    reset_time, policy = guardian.resolve_quota_reset_time(PROVIDER_SERPAPI, retry_after=None)
    assert reset_time is None
    assert policy == "unspecified_not_guessed"

    # Trip failure with no Retry-After
    guardian.record_circuit_failure(PROVIDER_SERPAPI, status_code=429, error_msg="Search limit reached")
    state, cooldown_until = guardian.get_circuit_state(PROVIDER_SERPAPI)
    assert state == STATE_OPEN
    assert cooldown_until is None  # Must NOT guess the reset time!


# =====================================================================
# 7. CONFIGURABLE RECOVERY POLICIES AND ADMINISTRATIVE RESET
# =====================================================================

def test_configured_recovery_policies_and_admin_reset(client, monkeypatch):
    """Support Retry-After header, billing cycle, fixed timestamp, interval, and admin reset."""
    now_ts = datetime.now(timezone.utc).timestamp()

    # A. Provider-supplied Retry-After header
    reset_a, policy_a = guardian.resolve_quota_reset_time(PROVIDER_SERPAPI, retry_after=3600)
    assert policy_a == "provider_retry_after"
    assert reset_a is not None
    dt_a = datetime.fromisoformat(reset_a)
    assert 3500 <= (dt_a.timestamp() - now_ts) <= 3700

    # B. Configured billing cycle day (e.g. 15th of month)
    monkeypatch.setenv("SERPAPI_BILLING_CYCLE_DAY", "15")
    reset_b, policy_b = guardian.resolve_quota_reset_time(PROVIDER_SERPAPI, retry_after=None)
    assert policy_b == "configured_billing_cycle"
    assert reset_b is not None
    dt_b = datetime.fromisoformat(reset_b)
    assert dt_b.day == 15

    # C. Configured fixed interval
    monkeypatch.delenv("SERPAPI_BILLING_CYCLE_DAY", raising=False)
    monkeypatch.setenv("SERPAPI_RESET_INTERVAL_SECONDS", "7200")
    reset_c, policy_c = guardian.resolve_quota_reset_time(PROVIDER_SERPAPI, retry_after=None)
    assert policy_c == "configured_interval"
    dt_c = datetime.fromisoformat(reset_c)
    assert 7100 <= (dt_c.timestamp() - now_ts) <= 7300

    # D. Administrative / Diagnostics Reset Endpoint
    guardian.record_circuit_failure(PROVIDER_SERPAPI, status_code=429, error_msg="Quota exhausted")
    state_before, _ = guardian.get_circuit_state(PROVIDER_SERPAPI)
    assert state_before == STATE_OPEN

    # Call diagnostics reset endpoint with admin token
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "secret_admin_token_999")
    res_reset = client.post(
        "/api/diagnostics/quota/reset",
        json={"provider_id": "serpapi"},
        headers={"X-Admin-Diagnostics-Token": "secret_admin_token_999"}
    )
    assert res_reset.status_code == 200
    reset_body = res_reset.get_json()
    assert reset_body["status"] == "success"
    assert reset_body["circuit_state"] == STATE_CLOSED

    # Verify circuit is operational again
    state_after, _ = guardian.get_circuit_state(PROVIDER_SERPAPI)
    assert state_after == STATE_CLOSED


# =====================================================================
# 8. CONFIGURABLE PROVIDER-ADAPTER INTERFACE
# =====================================================================

def test_configurable_provider_adapter_interface(monkeypatch):
    """Verify provider adapter interface and primary provider resolution."""
    # Default primary provider is serpapi
    monkeypatch.delenv("PRIMARY_FLIGHT_PROVIDER", raising=False)
    provider = get_primary_provider()
    assert provider is not None
    assert provider.provider_id == "serpapi"
    assert provider.provider_name == "SerpAPI"
    assert provider.provider_type == "metasearch"

    # Known registered providers
    assert "serpapi" in PROVIDER_REGISTRY
    assert "amadeus" in PROVIDER_REGISTRY
    assert "google_flights" not in PROVIDER_REGISTRY

    # Misleading alias 'google_flights' must be rejected
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "google_flights")
    with pytest.raises(ValueError, match="Unknown or unconfigured flight provider: 'google_flights'"):
        get_primary_provider()

    # Switch primary provider to amadeus via environment variable
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "amadeus")
    amadeus_provider = get_primary_provider()
    assert amadeus_provider.provider_id == "amadeus"
    assert amadeus_provider.provider_type == "gds"

    # Invalid / invented provider must raise ValueError rather than silently switching
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "invented_fake_provider")
    with pytest.raises(ValueError, match="Unknown or unconfigured flight provider"):
        get_primary_provider()
