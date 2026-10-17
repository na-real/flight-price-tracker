"""
Tests for SkyTracker Provider Registry, Upstream Labeling, and Routing Isolation.
Verifies that:
1. Misleading 'google_flights' alias is eliminated.
2. Provider configuration reflects actual upstream services ('serpapi' -> SerpAPI, 'amadeus' -> Amadeus GDS).
3. SerpAPI-backed results are labeled 'SerpAPI' (never independent Google Flights).
4. Amadeus adapter performs genuine mocked search using documented OAuth2 + Flight Offers API.
5. Primary provider routing strictly uses the chosen adapter without silent cross-provider fallback.
6. When unconfigured or exhausted, honest unavailable responses or cached results are returned.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from services.providers import (
    PROVIDER_REGISTRY,
    get_primary_provider,
    SerpApiFlightsProvider,
    AmadeusFlightProvider,
    BaseFlightProvider,
)
from services.providers.base import (
    ProviderQuotaExhaustedError,
    ProviderUnavailableError,
)
from services.api_guardian import (
    guardian,
    PROVIDER_SERPAPI,
    PROVIDER_AMADEUS,
    STATE_OPEN,
    STATE_QUOTA_EXHAUSTED,
)
from services.flight_api import search_flights


# =====================================================================
# 1. PROVIDER REGISTRY & ALIAS PURGE TESTS
# =====================================================================

def test_provider_registry_contains_only_authentic_upstream_services():
    """Verify provider registry maps genuine services without misleading aliases."""
    assert "serpapi" in PROVIDER_REGISTRY
    assert "amadeus" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["serpapi"] is SerpApiFlightsProvider
    assert PROVIDER_REGISTRY["amadeus"] is AmadeusFlightProvider

    # Misleading alias 'google_flights' must be purged
    assert "google_flights" not in PROVIDER_REGISTRY


def test_default_primary_provider_is_serpapi(monkeypatch):
    """Verify default provider is explicitly SerpAPI, not a fictitious Google Flights API."""
    monkeypatch.delenv("PRIMARY_FLIGHT_PROVIDER", raising=False)
    provider = get_primary_provider()
    assert isinstance(provider, SerpApiFlightsProvider)
    assert provider.provider_id == "serpapi"
    assert provider.provider_name == "SerpAPI"


def test_rejection_of_misleading_google_flights_alias(monkeypatch):
    """Selecting 'google_flights' must fail explicitly rather than aliasing to SerpAPI."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "google_flights")
    with pytest.raises(ValueError, match="Unknown or unconfigured flight provider: 'google_flights'"):
        get_primary_provider()


def test_primary_provider_switch_to_amadeus(monkeypatch):
    """Setting PRIMARY_FLIGHT_PROVIDER=amadeus resolves Amadeus adapter."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "amadeus")
    provider = get_primary_provider()
    assert isinstance(provider, AmadeusFlightProvider)
    assert provider.provider_id == "amadeus"
    assert provider.provider_name == "Amadeus GDS"


# =====================================================================
# 2. SERPAPI UPSTREAM LABELING TRUTHFULNESS
# =====================================================================

def test_serpapi_results_labeled_honestly(monkeypatch):
    """Verify SerpAPI searches label results as SerpAPI, never pretending to be Google Flights direct."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "serpapi")
    monkeypatch.setenv("SERPAPI_KEY", "mock_serpapi_key")

    mock_serp_data = {
        "best_flights": [{
            "flights": [{
                "airline": "IndiGo",
                "flight_number": "6E 202",
                "departure_airport": {"id": "DEL", "name": "Indira Gandhi", "time": "2026-11-20 06:00"},
                "arrival_airport": {"id": "BOM", "name": "Chhatrapati Shivaji", "time": "2026-11-20 08:15"},
                "duration": 135
            }],
            "price": 4500,
            "total_duration": 135
        }]
    }

    with patch("services.flight_api._raw_serpapi_fetch", return_value=mock_serp_data):
        results = search_flights("DEL", "BOM", "2026-11-20", force_refresh=True)

    assert len(results) == 1
    flight = results[0]
    assert flight["source"] == "SerpAPI"
    assert flight["is_live"] is True
    assert flight["data_status"] == "live"


def test_serpapi_cached_results_labeled_honestly(monkeypatch):
    """Verify cached SerpAPI results explicitly identify as SerpAPI (Cached)."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "serpapi")
    monkeypatch.setenv("SERPAPI_KEY", "mock_serpapi_key")

    mock_serp_data = {
        "best_flights": [{
            "flights": [{
                "airline": "Air India",
                "flight_number": "AI 804",
                "departure_airport": {"id": "DEL", "name": "Indira Gandhi", "time": "2026-11-20 09:00"},
                "arrival_airport": {"id": "BOM", "name": "Chhatrapati Shivaji", "time": "2026-11-20 11:15"},
                "duration": 135
            }],
            "price": 5200,
            "total_duration": 135
        }]
    }

    with patch("services.flight_api._raw_serpapi_fetch", return_value=mock_serp_data):
        # 1st call populates cache
        search_flights("DEL", "BOM", "2026-11-20", force_refresh=True)
        # 2nd call reads cache
        cached = search_flights("DEL", "BOM", "2026-11-20", force_refresh=False)

    assert len(cached) == 1
    assert cached[0]["source"] == "SerpAPI (Cached)"
    assert cached[0]["is_cached"] is True
    assert cached[0]["is_live"] is False
    assert cached[0]["data_status"] == "cached"


# =====================================================================
# 3. AMADEUS ADAPTER GENUINE FLIGHT SEARCH VERIFICATION
# =====================================================================

def test_amadeus_adapter_genuine_search_flow(monkeypatch):
    """
    Verify that Amadeus adapter executes authenticated search against documented endpoints:
    1. POST https://test.api.amadeus.com/v1/security/oauth2/token
    2. GET https://test.api.amadeus.com/v2/shopping/flight-offers
    Without making real network requests.
    """
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "test_client_id_xyz")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "test_client_secret_abc")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")

    provider = AmadeusFlightProvider()
    assert provider.is_configured() is True

    # Documented Amadeus flight offers response
    mock_amadeus_flight_offers = {
        "meta": {"count": 1},
        "data": [{
            "type": "flight-offer",
            "id": "1",
            "source": "GDS",
            "itineraries": [{
                "duration": "PT2H15M",
                "segments": [{
                    "departure": {"iataCode": "DEL", "terminal": "3", "at": "2026-11-20T08:00:00"},
                    "arrival": {"iataCode": "BOM", "terminal": "2", "at": "2026-11-20T10:15:00"},
                    "carrierCode": "AI",
                    "number": "805",
                    "duration": "PT2H15M",
                    "numberOfStops": 0
                }]
            }],
            "price": {
                "currency": "INR",
                "total": "5420.00"
            }
        }]
    }

    mock_auth_resp = MagicMock()
    mock_auth_resp.status_code = 200
    mock_auth_resp.json.return_value = {"access_token": "amadeus_mock_token_123"}

    mock_search_resp = MagicMock()
    mock_search_resp.status_code = 200
    mock_search_resp.content = b"content"
    mock_search_resp.json.return_value = mock_amadeus_flight_offers

    def mock_requests_post(url, **kwargs):
        if "oauth2/token" in url:
            assert kwargs["data"]["client_id"] == "test_client_id_xyz"
            assert kwargs["data"]["client_secret"] == "test_client_secret_abc"
            return mock_auth_resp
        raise RuntimeError(f"Unexpected POST url: {url}")

    def mock_requests_get(url, **kwargs):
        if "flight-offers" in url:
            assert kwargs["headers"]["Authorization"] == "Bearer amadeus_mock_token_123"
            assert kwargs["params"]["originLocationCode"] == "DEL"
            assert kwargs["params"]["destinationLocationCode"] == "BOM"
            assert kwargs["params"]["departureDate"] == "2026-11-20"
            return mock_search_resp
        raise RuntimeError(f"Unexpected GET url: {url}")

    with patch("requests.post", side_effect=mock_requests_post), \
         patch("requests.get", side_effect=mock_requests_get):
        offers = provider.search("DEL", "BOM", "2026-11-20")

    assert len(offers) == 1
    offer = offers[0]
    assert offer.provider_id == "amadeus"
    assert offer.provider_name == "Amadeus GDS"
    assert offer.airline == "Air India"
    assert offer.flight_number == "AI 805"
    assert offer.departure_time == "08:00"
    assert offer.arrival_time == "10:15"
    assert offer.duration == 135  # PT2H15M -> 135 mins
    assert offer.price == 5420
    assert offer.source == "Amadeus GDS"
    assert offer.is_live is True


# =====================================================================
# 4. PRIMARY PROVIDER ROUTING & NO SILENT FALLBACK GUARANTEES
# =====================================================================

def test_amadeus_primary_routing_does_not_invoke_serpapi(monkeypatch):
    """When Amadeus is primary, searches route through Amadeus adapter without touching SerpAPI."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "amadeus")
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "test_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")
    monkeypatch.setenv("SERPAPI_KEY", "should_never_be_called")

    mock_amadeus_data = {
        "data": [{
            "itineraries": [{
                "duration": "PT2H10M",
                "segments": [{
                    "departure": {"iataCode": "DEL", "at": "2026-11-20T07:15:00"},
                    "arrival": {"iataCode": "BOM", "at": "2026-11-20T09:25:00"},
                    "carrierCode": "6E",
                    "number": "214",
                    "duration": "PT2H10M"
                }]
            }],
            "price": {"currency": "INR", "total": "4850.00"}
        }]
    }

    mock_auth_resp = MagicMock(status_code=200)
    mock_auth_resp.json.return_value = {"access_token": "amadeus_token"}
    mock_search_resp = MagicMock(status_code=200, content=b"content")
    mock_search_resp.json.return_value = mock_amadeus_data

    serpapi_spy = MagicMock()

    with patch("requests.post", return_value=mock_auth_resp), \
         patch("requests.get", return_value=mock_search_resp), \
         patch("services.flight_api._raw_serpapi_fetch", serpapi_spy):
        results = search_flights("DEL", "BOM", "2026-11-20", force_refresh=True)

    # SerpAPI MUST NOT have been invoked
    assert serpapi_spy.call_count == 0

    assert len(results) == 1
    flight = results[0]
    assert flight["airline"] == "IndiGo"
    assert flight["flight_number"] == "6E 214"
    assert flight["price"] == 4850
    assert flight["duration"] == 130
    assert flight["source"] == "Amadeus GDS"
    assert flight["is_live"] is True


def test_amadeus_primary_unconfigured_honest_unavailable_no_fallback(monkeypatch):
    """When Amadeus is primary but unconfigured, it raises ProviderUnavailableError and does not fall back to SerpAPI."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "amadeus")
    monkeypatch.delenv("AMADEUS_CLIENT_ID", raising=False)
    monkeypatch.delenv("AMADEUS_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("SERPAPI_KEY", "serpapi_is_configured_but_must_not_be_used")

    serpapi_spy = MagicMock()

    with patch("services.flight_api._raw_serpapi_fetch", serpapi_spy):
        with pytest.raises(ProviderUnavailableError, match="Amadeus flight search is unconfigured"):
            search_flights("DEL", "BOM", "2026-11-20", force_refresh=True)

    # Must NOT have silently fallen back to SerpAPI!
    assert serpapi_spy.call_count == 0


def test_amadeus_primary_quota_exhausted_raises_error_no_fallback(monkeypatch):
    """When Amadeus is primary and quota is exhausted, raises ProviderQuotaExhaustedError without silent fallback."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "amadeus")
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "test_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("SERPAPI_KEY", "serpapi_key_available")

    # Manually trigger circuit breaker open for Amadeus
    guardian.record_circuit_failure(PROVIDER_AMADEUS, status_code=429, error_msg="Rate limit exceeded")

    serpapi_spy = MagicMock()

    with patch("services.flight_api._raw_serpapi_fetch", serpapi_spy):
        with pytest.raises(ProviderQuotaExhaustedError, match="upstream provider quota is exhausted"):
            search_flights("DEL", "BOM", "2026-11-20", force_refresh=True)

    # Must NOT have silently fallen back to SerpAPI
    assert serpapi_spy.call_count == 0


def test_amadeus_primary_returns_valid_cached_results_when_circuit_open(monkeypatch):
    """When Amadeus quota is exhausted but valid cached data exists, returns clearly labeled cached results."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "amadeus")
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "test_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "test_secret")

    # Seed Amadeus cache with verified prior search
    cache_key = guardian.build_cache_key(
        PROVIDER_AMADEUS,
        "search_flights",
        {"dep": "DEL", "arr": "BOM", "outbound": "2026-11-20", "return": None, "class": 1}
    )
    verified_amadeus_data = [{
        "airline": "Air India",
        "flight_number": "AI 805",
        "price": 5400,
        "source": "Amadeus GDS",
        "departure_airport": "DEL",
        "arrival_airport": "BOM",
        "departure_time": "08:00",
        "arrival_time": "10:15",
        "duration": 135,
        "stops": 0,
        "segments": [],
        "layovers": []
    }]
    guardian.set_cached(cache_key, PROVIDER_AMADEUS, "search_flights", verified_amadeus_data, ttl_seconds=1800, is_live=True)

    # Now open circuit for Amadeus
    guardian.record_circuit_failure(PROVIDER_AMADEUS, status_code=429, error_msg="Quota exhausted")

    # Search should safely return cached data labeled as cached
    results = search_flights("DEL", "BOM", "2026-11-20", force_refresh=False)
    assert len(results) == 1
    assert results[0]["source"] == "Amadeus GDS (Cached)"
    assert results[0]["is_cached"] is True
    assert results[0]["data_status"] == "cached"
    assert results[0]["is_live"] is False


def test_serpapi_primary_quota_exhausted_does_not_call_amadeus(monkeypatch):
    """When SerpAPI is primary and exhausted, it raises ProviderQuotaExhaustedError and never calls Amadeus."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "serpapi")
    monkeypatch.setenv("SERPAPI_KEY", "serp_key")
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "amadeus_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "amadeus_secret")

    guardian.record_circuit_failure(PROVIDER_SERPAPI, status_code=429, error_msg="Search limit reached")

    amadeus_spy = MagicMock()

    with patch("services.providers.amadeus_provider.AmadeusFlightProvider.search", amadeus_spy):
        with pytest.raises(ProviderQuotaExhaustedError, match="upstream provider quota is exhausted"):
            search_flights("DEL", "BOM", "2026-11-20", force_refresh=True)

    assert amadeus_spy.call_count == 0


# =====================================================================
# 5. FLASK API ENDPOINT VERIFICATION
# =====================================================================

def test_api_providers_endpoint_reflects_actual_upstream(client):
    """Verify /api/providers metadata endpoint identifies providers truthfully."""
    res = client.get("/api/providers")
    assert res.status_code == 200
    data = res.get_json()
    providers = data.get("providers", [])

    provider_ids = [p["provider_id"] for p in providers]
    assert "serpapi" in provider_ids
    assert "amadeus" in provider_ids
    assert "google_flights" not in provider_ids

    serp_item = next(p for p in providers if p["provider_id"] == "serpapi")
    assert serp_item["provider_name"] == "SerpAPI"

    amadeus_item = next(p for p in providers if p["provider_id"] == "amadeus")
    assert amadeus_item["provider_name"] == "Amadeus GDS"


def test_api_search_endpoint_with_amadeus_primary(client, monkeypatch):
    """Verify /api/search routes through Amadeus when PRIMARY_FLIGHT_PROVIDER=amadeus."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "amadeus")
    monkeypatch.setenv("AMADEUS_CLIENT_ID", "test_id")
    monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("AMADEUS_ENVIRONMENT", "production")

    mock_amadeus_data = {
        "data": [{
            "itineraries": [{
                "duration": "PT2H05M",
                "segments": [{
                    "departure": {"iataCode": "DEL", "at": "2026-11-20T10:00:00"},
                    "arrival": {"iataCode": "BOM", "at": "2026-11-20T12:05:00"},
                    "carrierCode": "AI",
                    "number": "806",
                    "duration": "PT2H05M"
                }]
            }],
            "price": {"currency": "INR", "total": "5100.00"}
        }]
    }

    mock_auth_resp = MagicMock(status_code=200)
    mock_auth_resp.json.return_value = {"access_token": "amadeus_token"}
    mock_search_resp = MagicMock(status_code=200, content=b"content")
    mock_search_resp.json.return_value = mock_amadeus_data

    with patch("requests.post", return_value=mock_auth_resp), \
         patch("requests.get", return_value=mock_search_resp):
        res = client.post("/api/search", json={
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": "2026-11-20",
            "trip_type": "one_way"
        })

    assert res.status_code == 200
    resp_data = res.get_json()
    assert "results" in resp_data
    results = resp_data["results"]
    assert len(results) >= 1
    assert any(r.get("source") == "Amadeus GDS" for r in results)


def test_primary_provider_switch_to_flightapi(monkeypatch):
    """Setting PRIMARY_FLIGHT_PROVIDER=flightapi resolves FlightApiProvider."""
    from services.providers.flightapi_provider import FlightApiProvider
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "flightapi")
    provider = get_primary_provider()
    assert isinstance(provider, FlightApiProvider)
    assert provider.provider_id == "flightapi"
    assert provider.provider_name == "FlightAPI"


def test_search_endpoint_routes_through_flightapi(client, monkeypatch):
    """Verify /api/search routes through FlightAPI when PRIMARY_FLIGHT_PROVIDER=flightapi."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "flightapi")
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "test_flightapi_key_valid")

    mock_flightapi_data = {
        "itineraries": [{
            "id": "itin_fl_1",
            "leg_ids": ["leg_fl_1"],
            "pricing_options": [{
                "price": {"amount": 5250},
                "items": [{"deep_link": "https://flightapi.io/book/123"}]
            }]
        }],
        "legs": [{
            "id": "leg_fl_1",
            "departure": "2026-11-20T08:30:00",
            "arrival": "2026-11-20T11:15:00",
            "duration": 165,
            "stop_count": 0,
            "departure_airport_code": "DEL",
            "arrival_airport_code": "BOM",
            "airline_codes": ["6E"]
        }],
        "carriers": [
            {"id": "6E", "code": "6E", "name": "IndiGo"}
        ]
    }

    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = mock_flightapi_data

    with patch("requests.get", return_value=mock_resp):
        res = client.post("/api/search", json={
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": "2026-11-20",
            "trip_type": "one_way"
        })

    assert res.status_code == 200
    data = res.get_json()
    assert "results" in data
    assert len(data["results"]) >= 1
    flight = data["results"][0]
    assert flight["source"] == "FlightAPI"
    assert flight["price"] == 5250
    assert flight["is_live"] is True
    assert flight["is_simulated"] is False


def test_flightapi_failure_returns_honest_error_no_fake_fares(client, monkeypatch):
    """Verify upstream provider failure returns HTTP 503 and never invents fake fares."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "flightapi")
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "test_flightapi_key_valid")
    monkeypatch.delenv("ALLOW_SIMULATED_FALLBACK", raising=False)

    mock_resp = MagicMock(status_code=429, text="Quota exceeded for trial token")

    with patch("requests.get", return_value=mock_resp):
        res = client.post("/api/search", json={
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": "2026-11-20",
            "trip_type": "one_way"
        })

    assert res.status_code == 503
    data = res.get_json()
    assert "error" in data
    assert "quota" in data["error"].lower() or "unavailable" in data["error"].lower()
    assert data["results"] == []
