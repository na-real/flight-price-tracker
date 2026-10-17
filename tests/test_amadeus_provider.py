"""
Unit and integration tests for AmadeusFlightProvider.
Verifies authentication, token caching, request parameters, response normalization,
error handling (401, 403, 429, timeouts), test vs production environment behavior,
and strict provider isolation without silent fallback.
ZERO real network requests are made.
"""

import os
import time
from unittest.mock import MagicMock, patch
import pytest
import requests

from services.providers.amadeus_provider import (
    AmadeusFlightProvider,
    get_amadeus_environment,
    get_amadeus_urls,
    AMADEUS_ENV_URLS,
    _parse_iso_duration,
)
from services.providers.base import (
    ProviderQuotaExhaustedError,
    ProviderUnavailableError,
)
from services.providers import get_primary_provider
from services.flight_api import search_flights
from services.api_guardian import (
    guardian,
    PROVIDER_AMADEUS,
    PROVIDER_SERPAPI,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_QUOTA_EXHAUSTED,
)


@pytest.fixture(autouse=True)
def reset_guardian_state():
    """Reset Guardian circuit state and memory cache before and after every test."""
    guardian.reset_provider_quota(PROVIDER_AMADEUS)
    guardian.reset_provider_quota(PROVIDER_SERPAPI)
    guardian._memory_cache.clear()
    yield
    guardian.reset_provider_quota(PROVIDER_AMADEUS)
    guardian.reset_provider_quota(PROVIDER_SERPAPI)
    guardian._memory_cache.clear()


# =====================================================================
# 1. ENVIRONMENT CONFIGURATION & EXPLICIT URL MAPPING
# =====================================================================

def test_amadeus_environment_url_resolution(monkeypatch):
    """Verify test and production environments resolve to explicit, documented URLs."""
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "test")
    assert get_amadeus_environment() == "test"
    test_urls = get_amadeus_urls("test")
    assert test_urls["base_url"] == "https://test.api.amadeus.com"
    assert test_urls["auth_url"] == "https://test.api.amadeus.com/v1/security/oauth2/token"
    assert test_urls["flights_url"] == "https://test.api.amadeus.com/v2/shopping/flight-offers"

    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")
    assert get_amadeus_environment() == "production"
    prod_urls = get_amadeus_urls("production")
    assert prod_urls["base_url"] == "https://api.amadeus.com"
    assert prod_urls["auth_url"] == "https://api.amadeus.com/v1/security/oauth2/token"
    assert prod_urls["flights_url"] == "https://api.amadeus.com/v2/shopping/flight-offers"


def test_amadeus_environment_rejects_invalid_values(monkeypatch):
    """Never silently switch environments: unrecognized AMADEUS_ENVIRONMENT raises ValueError."""
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "sandbox")
    with pytest.raises(ValueError, match="Invalid AMADEUS_ENVIRONMENT: 'sandbox'"):
        get_amadeus_environment()

    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "prod")
    with pytest.raises(ValueError, match="Invalid AMADEUS_ENVIRONMENT: 'prod'"):
        get_amadeus_environment()

    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "staging")
    with pytest.raises(ValueError, match="Invalid AMADEUS_ENVIRONMENT: 'staging'"):
        provider = AmadeusFlightProvider()
        _ = provider.environment


def test_amadeus_adapter_environment_properties(monkeypatch):
    """Verify AmadeusFlightProvider exposes explicit environment properties."""
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "test")
    provider_test = AmadeusFlightProvider()
    assert provider_test.environment == "test"
    assert provider_test.is_test_environment is True
    assert "test.api.amadeus.com" in provider_test.auth_url
    assert "test.api.amadeus.com" in provider_test.flights_url

    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")
    provider_prod = AmadeusFlightProvider()
    assert provider_prod.environment == "production"
    assert provider_prod.is_test_environment is False
    assert "api.amadeus.com" in provider_prod.auth_url
    assert "test.api.amadeus.com" not in provider_prod.auth_url


# =====================================================================
# 2. CREDENTIALS & TOKEN CACHING WITH EXPIRY
# =====================================================================

def test_amadeus_unconfigured_status_and_suppression(monkeypatch):
    """When credentials are absent, adapter reports unconfigured and makes 0 outbound requests."""
    monkeypatch.delenv("AMADEUS_CLIENT_ID", raising=False)
    monkeypatch.delenv("AMADEUS_CLIENT_SECRET", raising=False)

    provider = AmadeusFlightProvider()
    assert provider.is_configured() is False
    status = provider.get_status()
    assert status.status == "unconfigured"
    assert status.is_live is False
    assert "Requires AMADEUS_CLIENT_ID" in status.message

    with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
        offers = provider.search("DEL", "BOM", "2026-11-20")
        assert offers == []
        assert mock_post.call_count == 0
        assert mock_get.call_count == 0


def test_amadeus_token_caching_and_reuse(monkeypatch):
    """Valid in-memory token is reused across searches without making extra auth calls."""
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")

    provider = AmadeusFlightProvider()

    mock_auth_resp = MagicMock(status_code=200)
    mock_auth_resp.json.return_value = {
        "access_token": "cached_token_abc123",
        "expires_in": 1800
    }

    mock_search_resp = MagicMock(status_code=200, content=b"content")
    mock_search_resp.json.return_value = {"data": []}

    with patch("requests.post", return_value=mock_auth_resp) as mock_post, \
         patch("requests.get", return_value=mock_search_resp) as mock_get:
        # First search: authenticates and caches token
        provider.search("DEL", "BOM", "2026-11-20")
        assert mock_post.call_count == 1
        assert provider.access_token == "cached_token_abc123"
        assert provider.is_token_valid() is True

        # Second search: reuses valid cached token
        provider.search("BOM", "DEL", "2026-11-25")
        assert mock_post.call_count == 1  # No additional auth call!


def test_amadeus_token_refresh_when_expired(monkeypatch):
    """Expired token triggers fresh authentication."""
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "test")

    provider = AmadeusFlightProvider()
    provider.access_token = "old_stale_token"
    # Set expiration in the past
    provider.token_expires_at = time.time() - 100
    assert provider.is_token_valid() is False

    mock_auth_resp = MagicMock(status_code=200)
    mock_auth_resp.json.return_value = {
        "access_token": "fresh_token_xyz789",
        "expires_in": 1800
    }

    mock_search_resp = MagicMock(status_code=200, content=b"content")
    mock_search_resp.json.return_value = {"data": []}

    with patch("requests.post", return_value=mock_auth_resp) as mock_post, \
         patch("requests.get", return_value=mock_search_resp) as mock_get:
        provider.search("DEL", "BOM", "2026-11-20")
        assert mock_post.call_count == 1
        assert provider.access_token == "fresh_token_xyz789"
        assert provider.is_token_valid() is True


# =====================================================================
# 3. HTTP ERROR HANDLING: 401, 403, 429, TIMEOUTS & INVALID JSON
# =====================================================================

def test_amadeus_http_401_reauth_retry_flow(monkeypatch):
    """HTTP 401 during search clears token cache, re-authenticates once, and retries search."""
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")

    provider = AmadeusFlightProvider()
    provider.access_token = "expired_token_123"
    provider.token_expires_at = time.time() + 1000

    resp_401 = MagicMock(status_code=401, text="Unauthorized: Token expired")
    resp_200 = MagicMock(status_code=200, content=b"content")
    resp_200.json.return_value = {"data": []}

    get_call_count = 0

    def mock_get(url, **kwargs):
        nonlocal get_call_count
        get_call_count += 1
        if get_call_count == 1:
            return resp_401
        return resp_200

    mock_auth = MagicMock(status_code=200)
    mock_auth.json.return_value = {"access_token": "new_reauthed_token", "expires_in": 1800}

    with patch("requests.post", return_value=mock_auth) as post_patch, \
         patch("requests.get", side_effect=mock_get):
        offers = provider.search("DEL", "BOM", "2026-11-20")

    # Re-authenticated once and retried search once
    assert post_patch.call_count == 1
    assert get_call_count == 2
    assert provider.access_token == "new_reauthed_token"
    assert provider.last_status == "active"


def test_amadeus_http_403_forbidden_non_retryable(monkeypatch):
    """HTTP 403 is non-retryable and sets provider status to error without looping."""
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")

    provider = AmadeusFlightProvider()
    provider.access_token = "valid_token"
    provider.token_expires_at = time.time() + 1000

    resp_403 = MagicMock(status_code=403, text="Forbidden: Access not granted to this API")

    with patch("requests.get", return_value=resp_403) as mock_get:
        offers = provider.search("DEL", "BOM", "2026-11-20")

    assert offers == []
    assert provider.last_status == "error"
    assert "Amadeus query failed" in provider.last_message or "403" in provider.last_message


def test_amadeus_http_429_quota_exhaustion_trips_circuit_breaker(monkeypatch):
    """HTTP 429 immediately trips circuit breaker into QUOTA_EXHAUSTED with zero repeated retries."""
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")

    provider = AmadeusFlightProvider()
    provider.access_token = "valid_token"
    provider.token_expires_at = time.time() + 1000

    resp_429 = MagicMock(status_code=429, text='{"errors":[{"status":429,"title":"RESOURCE HAS REACHED MAXIMUM NUMBER OF REQUESTS"}]}')

    with patch("requests.get", return_value=resp_429) as mock_get:
        offers = provider.search("DEL", "BOM", "2026-11-20")

    assert offers == []
    assert provider.is_quota_exhausted() is True
    status = provider.get_status()
    assert status.status == "quota_exhausted"
    assert "quota exhausted" in status.message.lower()

    # Subsequent searches are suppressed by circuit breaker with ZERO upstream requests
    with patch("requests.get") as mock_suppressed_get:
        suppressed_offers = provider.search("DEL", "BOM", "2026-11-20")
        assert suppressed_offers == []
        assert mock_suppressed_get.call_count == 0


def test_amadeus_timeout_handling_with_bounded_retries(monkeypatch):
    """Network timeouts trigger bounded retries via Guardian then report failure cleanly."""
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")

    provider = AmadeusFlightProvider()
    provider.access_token = "valid_token"
    provider.token_expires_at = time.time() + 1000

    call_count = 0

    def mock_timeout(url, **kwargs):
        nonlocal call_count
        call_count += 1
        raise requests.exceptions.Timeout("Connection timed out after 15s")

    with patch("requests.get", side_effect=mock_timeout):
        offers = provider.search("DEL", "BOM", "2026-11-20")

    assert offers == []
    # Bounded retries: 1 initial + max_retries (2) = 3 total attempts
    assert call_count == 3
    assert provider.last_status == "error"


def test_amadeus_invalid_json_handling(monkeypatch):
    """Invalid non-JSON response from upstream is handled gracefully without crashing."""
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")

    provider = AmadeusFlightProvider()
    provider.access_token = "valid_token"
    provider.token_expires_at = time.time() + 1000

    bad_resp = MagicMock(status_code=200, content=b"<html>Bad Gateway</html>")
    bad_resp.json.side_effect = ValueError("Invalid JSON")

    with patch("requests.get", return_value=bad_resp):
        offers = provider.search("DEL", "BOM", "2026-11-20")

    assert offers == []
    assert provider.last_status == "error"


# =====================================================================
# 4. RESPONSE NORMALIZATION & METADATA EXTRACTION
# =====================================================================

def test_amadeus_response_normalization_one_way(monkeypatch):
    """Verify authentic Amadeus response normalization for one-way flight offers."""
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")

    provider = AmadeusFlightProvider()
    provider.access_token = "valid_token"
    provider.token_expires_at = time.time() + 1000

    mock_amadeus_response = {
        "meta": {"count": 1},
        "data": [
            {
                "type": "flight-offer",
                "id": "1",
                "source": "GDS",
                "numberOfBookableSeats": 7,
                "itineraries": [
                    {
                        "duration": "PT4H45M",
                        "segments": [
                            {
                                "departure": {"iataCode": "DEL", "terminal": "3", "at": "2026-11-20T06:00:00"},
                                "arrival": {"iataCode": "NAG", "terminal": "1", "at": "2026-11-20T07:45:00"},
                                "carrierCode": "6E",
                                "number": "201",
                                "duration": "PT1H45M"
                            },
                            {
                                "departure": {"iataCode": "NAG", "terminal": "1", "at": "2026-11-20T08:45:00"},
                                "arrival": {"iataCode": "BOM", "terminal": "2", "at": "2026-11-20T10:45:00"},
                                "carrierCode": "6E",
                                "number": "202",
                                "duration": "PT2H00M"
                            }
                        ]
                    }
                ],
                "price": {
                    "currency": "INR",
                    "total": "5890.00",
                    "base": "5100.00"
                },
                "travelerPricings": [
                    {
                        "fareDetailsBySegment": [
                            {
                                "segmentId": "1",
                                "includedCheckedBags": {"quantity": 1}
                            }
                        ]
                    }
                ]
            }
        ],
        "dictionaries": {
            "carriers": {"6E": "IndiGo"}
        }
    }

    mock_resp = MagicMock(status_code=200, content=b"content")
    mock_resp.json.return_value = mock_amadeus_response

    with patch("requests.get", return_value=mock_resp):
        offers = provider.search("DEL", "BOM", "2026-11-20")

    assert len(offers) == 1
    offer = offers[0]
    assert offer.airline == "IndiGo"
    assert offer.flight_number == "6E 201"
    assert offer.departure_time == "06:00"
    assert offer.arrival_time == "10:45"
    assert offer.duration == 285  # PT4H45M = 4*60 + 45 = 285 mins
    assert offer.stops == 1
    assert offer.price == 5890
    assert offer.currency == "INR"
    assert offer.baggage == "1 checked bag(s) included"
    assert offer.number_of_bookable_seats == 7
    assert len(offer.segments) == 2
    assert len(offer.layovers) == 1
    assert offer.layovers[0]["id"] == "NAG"
    assert offer.layovers[0]["duration"] == 60  # 07:45 to 08:45 = 60 mins
    assert offer.booking_url == "https://amadeus.com"


def test_amadeus_response_normalization_round_trip(monkeypatch):
    """Verify authentic Amadeus response normalization for round-trip flight offers."""
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")

    provider = AmadeusFlightProvider()
    provider.access_token = "valid_token"
    provider.token_expires_at = time.time() + 1000

    mock_round_trip_data = {
        "data": [
            {
                "type": "flight-offer",
                "id": "rt_1",
                "numberOfBookableSeats": 4,
                "itineraries": [
                    {
                        "duration": "PT2H15M",
                        "segments": [{
                            "departure": {"iataCode": "DEL", "terminal": "3", "at": "2026-11-20T08:00:00"},
                            "arrival": {"iataCode": "BOM", "terminal": "2", "at": "2026-11-20T10:15:00"},
                            "carrierCode": "AI",
                            "number": "805",
                            "duration": "PT2H15M"
                        }]
                    },
                    {
                        "duration": "PT2H10M",
                        "segments": [{
                            "departure": {"iataCode": "BOM", "terminal": "2", "at": "2026-11-25T16:00:00"},
                            "arrival": {"iataCode": "DEL", "terminal": "3", "at": "2026-11-25T18:10:00"},
                            "carrierCode": "AI",
                            "number": "806",
                            "duration": "PT2H10M"
                        }]
                    }
                ],
                "price": {"currency": "INR", "total": "10450.00"}
            }
        ],
        "dictionaries": {"carriers": {"AI": "Air India"}}
    }

    mock_resp = MagicMock(status_code=200, content=b"content")
    mock_resp.json.return_value = mock_round_trip_data

    with patch("requests.get", return_value=mock_resp):
        offers = provider.search("DEL", "BOM", "2026-11-20", return_date="2026-11-25")

    assert len(offers) == 1
    offer = offers[0]
    assert offer.trip_type == "round_trip"
    assert offer.airline == "Air India"
    assert offer.departure_time == "08:00"
    assert offer.arrival_time == "10:15"
    assert offer.price == 10450
    assert offer.return_airline == "Air India"
    assert offer.return_flight_number == "AI 806"
    assert offer.return_departure_time == "16:00"
    assert offer.return_arrival_time == "18:10"
    assert len(offer.return_segments) == 1


# =====================================================================
# 5. TEST VS PRODUCTION ENVIRONMENT TRUTHFULNESS (REQUIREMENT 8)
# =====================================================================

def test_amadeus_test_environment_data_never_labeled_live(monkeypatch):
    """When AMADEUS_ENVIRONMENT=test, fares must be marked is_live=False, data_status='test', is_simulated=True."""
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "test")
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "amadeus")

    mock_data = {
        "data": [{
            "itineraries": [{
                "duration": "PT2H00M",
                "segments": [{
                    "departure": {"iataCode": "DEL", "at": "2026-11-20T08:00:00"},
                    "arrival": {"iataCode": "BOM", "at": "2026-11-20T10:00:00"},
                    "carrierCode": "AI",
                    "number": "101",
                    "duration": "PT2H00M"
                }]
            }],
            "price": {"currency": "INR", "total": "4500.00"}
        }]
    }

    mock_auth = MagicMock(status_code=200)
    mock_auth.json.return_value = {"access_token": "token", "expires_in": 1800}
    mock_get = MagicMock(status_code=200, content=b"content")
    mock_get.json.return_value = mock_data

    with patch("requests.post", return_value=mock_auth), \
         patch("requests.get", return_value=mock_get):
        results = search_flights("DEL", "BOM", "2026-11-20", force_refresh=True)

    assert len(results) == 1
    flight = results[0]
    # Truthfulness assertions
    assert flight["is_live"] is False
    assert flight["data_status"] == "test"
    assert flight["is_simulated"] is True
    assert "Test Data" in flight["source"]
    assert "Amadeus GDS (Test Data)" == flight["source"]


def test_amadeus_production_environment_labeled_live(monkeypatch):
    """When AMADEUS_ENVIRONMENT=production, fresh fares are marked is_live=True, data_status='live', is_simulated=False."""
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "amadeus")

    mock_data = {
        "data": [{
            "itineraries": [{
                "duration": "PT2H00M",
                "segments": [{
                    "departure": {"iataCode": "DEL", "at": "2026-11-20T08:00:00"},
                    "arrival": {"iataCode": "BOM", "at": "2026-11-20T10:00:00"},
                    "carrierCode": "AI",
                    "number": "101",
                    "duration": "PT2H00M"
                }]
            }],
            "price": {"currency": "INR", "total": "4500.00"}
        }]
    }

    mock_auth = MagicMock(status_code=200)
    mock_auth.json.return_value = {"access_token": "token", "expires_in": 1800}
    mock_get = MagicMock(status_code=200, content=b"content")
    mock_get.json.return_value = mock_data

    with patch("requests.post", return_value=mock_auth), \
         patch("requests.get", return_value=mock_get):
        results = search_flights("DEL", "BOM", "2026-11-20", force_refresh=True)

    assert len(results) == 1
    flight = results[0]
    assert flight["is_live"] is True
    assert flight["data_status"] == "live"
    assert flight["is_simulated"] is False
    assert flight["source"] == "Amadeus GDS"


# =====================================================================
# 6. PROVIDER SELECTION & STRICT ISOLATION (ZERO SILENT FALLBACK)
# =====================================================================

def test_amadeus_failure_never_silently_falls_back_to_serpapi(monkeypatch):
    """When Amadeus is primary and fails, it raises an error and NEVER calls SerpAPI."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "amadeus")
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")
    monkeypatch.setenv("SERPAPI_KEY", "must_never_be_touched")

    serpapi_spy = MagicMock()
    mock_auth = MagicMock(status_code=401, text="Invalid client")

    with patch("requests.post", return_value=mock_auth), \
         patch("services.flight_api._raw_serpapi_fetch", serpapi_spy):
        with pytest.raises(ProviderUnavailableError, match="Fresh flight search unavailable"):
            search_flights("DEL", "BOM", "2026-11-20", force_refresh=True)

    assert serpapi_spy.call_count == 0


def test_amadeus_quota_exhausted_never_calls_serpapi(monkeypatch):
    """When Amadeus is primary and quota is exhausted, raises ProviderQuotaExhaustedError with 0 SerpAPI calls."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "amadeus")
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "mock_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")
    monkeypatch.setenv("SERPAPI_KEY", "must_never_be_touched")

    # Force Amadeus circuit into quota exhausted state
    guardian.record_circuit_failure(PROVIDER_AMADEUS, status_code=429, error_msg="Quota limit reached")

    serpapi_spy = MagicMock()

    with patch("services.flight_api._raw_serpapi_fetch", serpapi_spy):
        with pytest.raises(ProviderQuotaExhaustedError, match="upstream provider quota is exhausted"):
            search_flights("DEL", "BOM", "2026-11-20", force_refresh=True)

    assert serpapi_spy.call_count == 0
