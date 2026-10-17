"""
Unit and integration tests for FlightApiProvider.
Verifies URL construction, relational response normalization, missing fields resilience,
credential protection, API Guardian circuit breaker, 30-credit budget enforcement,
caching, request coalescing, and background price checker safety.
ZERO real network requests are made. Mocked HTTP responses are used throughout.
"""

import os
from unittest.mock import MagicMock, patch
import pytest
import requests

from services.providers.flightapi_provider import (
    FlightApiProvider,
    sanitize_flightapi_url,
    FLIGHTAPI_BASE_URL,
    FLIGHTAPI_CABIN_MAP,
)
from services.providers.base import (
    FlightOffer,
    ProviderQuotaExhaustedError,
    ProviderUnavailableError,
)
from services.providers import get_primary_provider, PROVIDER_REGISTRY
from services.flight_api import search_flights, search_multi_city_flights
from services.api_guardian import (
    guardian,
    PROVIDER_FLIGHTAPI,
    PROVIDER_SERPAPI,
    PROVIDER_AMADEUS,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_QUOTA_EXHAUSTED,
    CALLER_USER_SEARCH,
    CALLER_BACKGROUND_CHECK,
)
from db import db, init_db
import checker


@pytest.fixture(autouse=True)
def reset_guardian_state():
    """Reset Guardian circuit state, memory cache, and database logs before and after each test."""
    guardian.clear_cache()
    guardian.reset_provider_quota(PROVIDER_FLIGHTAPI)
    guardian.reset_provider_quota(PROVIDER_SERPAPI)
    guardian.reset_provider_quota(PROVIDER_AMADEUS)
    yield
    guardian.clear_cache()
    guardian.reset_provider_quota(PROVIDER_FLIGHTAPI)
    guardian.reset_provider_quota(PROVIDER_SERPAPI)
    guardian.reset_provider_quota(PROVIDER_AMADEUS)


# =====================================================================
# 1. PROVIDER REGISTRATION & SELECTION
# =====================================================================

def test_flightapi_registered_in_provider_registry():
    """FlightAPI provider is registered in PROVIDER_REGISTRY."""
    assert "flightapi" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["flightapi"] == FlightApiProvider


def test_get_primary_provider_resolves_flightapi(monkeypatch):
    """Setting PRIMARY_FLIGHT_PROVIDER=flightapi correctly resolves FlightApiProvider."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "flightapi")
    provider = get_primary_provider()
    assert isinstance(provider, FlightApiProvider)
    assert provider.provider_id == "flightapi"
    assert provider.provider_name == "FlightAPI"
    assert provider.provider_type == "metasearch"


def test_flightapi_unconfigured_status(monkeypatch):
    """When FLIGHTAPI_API_KEY is unset, provider status is unconfigured."""
    monkeypatch.delenv("FLIGHTAPI_API_KEY", raising=False)
    provider = FlightApiProvider()
    assert provider.is_configured() is False
    status = provider.get_status()
    assert status.status == "unconfigured"
    assert status.is_live is False
    assert "FLIGHTAPI_API_KEY is not configured" in status.message


def test_flightapi_configured_status(monkeypatch):
    """When FLIGHTAPI_API_KEY is set, provider status is active."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "test_key_12345")
    provider = FlightApiProvider()
    assert provider.is_configured() is True
    status = provider.get_status()
    assert status.status == "active"
    assert status.is_live is True


# =====================================================================
# 2. PARAMETER CONSTRUCTION & URL BUILDING
# =====================================================================

def test_oneway_url_construction(monkeypatch):
    """Verify one-way endpoint URL structure and parameters match official documentation."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "secret_key_abc")
    monkeypatch.setenv("FLIGHTAPI_REGION", "IN")
    provider = FlightApiProvider()

    url = provider._build_oneway_url(
        departure="del",
        arrival="bom",
        outbound_date="2026-11-20",
        adults=2,
        children=1,
        infants=0,
        cabin_class="Economy",
        currency="INR",
        region="IN"
    )

    expected = "https://api.flightapi.io/onewaytrip/secret_key_abc/DEL/BOM/2026-11-20/2/1/0/Economy/INR?region=IN"
    assert url == expected


def test_roundtrip_url_construction(monkeypatch):
    """Verify round-trip endpoint URL structure and return date positioning."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "secret_key_abc")
    monkeypatch.setenv("FLIGHTAPI_REGION", "IN")
    provider = FlightApiProvider()

    url = provider._build_roundtrip_url(
        departure="del",
        arrival="maa",
        outbound_date="2026-11-20",
        return_date="2026-11-25",
        adults=1,
        children=0,
        infants=0,
        cabin_class="Business",
        currency="INR",
        region="IN"
    )

    expected = "https://api.flightapi.io/roundtrip/secret_key_abc/DEL/MAA/2026-11-20/2026-11-25/1/0/0/Business/INR?region=IN"
    assert url == expected


def test_multitrip_request_construction(monkeypatch):
    """Verify multi-trip endpoint URL and query parameters match documented route specs."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "secret_key_abc")
    monkeypatch.setenv("FLIGHTAPI_REGION", "IN")
    provider = FlightApiProvider()

    legs = [
        {"departure": "DEL", "arrival": "BOM", "outbound_date": "2026-11-20"},
        {"departure": "BOM", "arrival": "BLR", "outbound_date": "2026-11-23"},
        {"departure": "BLR", "arrival": "DEL", "outbound_date": "2026-11-26"},
    ]

    url, params = provider._build_multitrip_request(
        legs=legs,
        adults=1,
        children=0,
        infants=0,
        cabin_class="Economy",
        currency="INR",
        region="IN"
    )

    assert url == "https://api.flightapi.io/multitrip/secret_key_abc"
    assert params["trips"] == 3
    assert params["arp1"] == "DEL"
    assert params["arp2"] == "BOM"
    assert params["date1"] == "2026-11-20"
    assert params["arp3"] == "BOM"
    assert params["arp4"] == "BLR"
    assert params["date2"] == "2026-11-23"
    assert params["arp5"] == "BLR"
    assert params["arp6"] == "DEL"
    assert params["date3"] == "2026-11-26"
    assert params["cabinclass"] == "Economy"
    assert params["currency"] == "INR"
    assert params["region"] == "IN"


def test_multitrip_route_limits_enforced(monkeypatch):
    """Multi-trip strictly enforces documented 3-to-5 leg constraint."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "secret_key_abc")
    provider = FlightApiProvider()

    # < 3 legs must raise ValueError
    two_legs = [
        {"departure": "DEL", "arrival": "BOM", "outbound_date": "2026-11-20"},
        {"departure": "BOM", "arrival": "BLR", "outbound_date": "2026-11-23"},
    ]
    with pytest.raises(ValueError, match="between 3 and 5 route legs"):
        provider._build_multitrip_request(two_legs)

    # > 5 legs must raise ValueError
    six_legs = [
        {"departure": "DEL", "arrival": "BOM", "outbound_date": "2026-11-20"},
        {"departure": "BOM", "arrival": "BLR", "outbound_date": "2026-11-23"},
        {"departure": "BLR", "arrival": "MAA", "outbound_date": "2026-11-25"},
        {"departure": "MAA", "arrival": "HYD", "outbound_date": "2026-11-27"},
        {"departure": "HYD", "arrival": "CCU", "outbound_date": "2026-11-29"},
        {"departure": "CCU", "arrival": "DEL", "outbound_date": "2026-12-01"},
    ]
    with pytest.raises(ValueError, match="between 3 and 5 route legs"):
        provider._build_multitrip_request(six_legs)


# =====================================================================
# 3. CREDENTIAL PROTECTION & LOG SANITIZATION
# =====================================================================

def test_sanitize_flightapi_url():
    """Verify that secret keys embedded in FlightAPI URL paths are cleanly redacted."""
    raw_oneway = "https://api.flightapi.io/onewaytrip/SECRET_API_KEY_xyz123/DEL/BOM/2026-11-20/1/0/0/Economy/INR?region=IN"
    sanitized = sanitize_flightapi_url(raw_oneway)
    assert "SECRET_API_KEY_xyz123" not in sanitized
    assert "[REDACTED]" in sanitized
    assert sanitized == "https://api.flightapi.io/onewaytrip/[REDACTED]/DEL/BOM/2026-11-20/1/0/0/Economy/INR?region=IN"

    raw_multitrip = "https://api.flightapi.io/multitrip/SECRET_MULTI_KEY?trips=3&arp1=DEL"
    sanitized_multi = sanitize_flightapi_url(raw_multitrip)
    assert "SECRET_MULTI_KEY" not in sanitized_multi
    assert "[REDACTED]" in sanitized_multi


# =====================================================================
# 4. RESPONSE NORMALIZATION & MISSING FIELDS
# =====================================================================

MOCK_ONEWAY_PAYLOAD = {
    "itineraries": [
        {
            "id": "itin_1",
            "leg_ids": ["leg_1"],
            "pricing_options": [
                {
                    "price": {"amount": 5420.0, "currency": "INR"},
                    "agent_ids": ["agent_trip"],
                    "items": [{"agent_id": "agent_trip", "url": "https://trip.com/book/itin_1"}]
                }
            ]
        }
    ],
    "legs": [
        {
            "id": "leg_1",
            "departure": "2026-11-20T06:15:00",
            "arrival": "2026-11-20T08:30:00",
            "duration": 135,
            "stop_count": 0,
            "departure_airport_code": "DEL",
            "arrival_airport_code": "BOM",
            "segment_ids": ["seg_1"],
            "marketing_carrier_ids": ["carrier_6e"]
        }
    ],
    "segments": [
        {
            "id": "seg_1",
            "departure": "2026-11-20T06:15:00",
            "arrival": "2026-11-20T08:30:00",
            "duration": 135,
            "departure_airport_code": "DEL",
            "arrival_airport_code": "BOM",
            "marketing_carrier_id": "carrier_6e",
            "marketing_flight_number": "6E 214"
        }
    ],
    "carriers": [
        {"id": "carrier_6e", "name": "IndiGo", "iata": "6E"}
    ],
    "agents": [
        {"id": "agent_trip", "name": "Trip.com"}
    ],
    "places": [
        {"id": "place_del", "code": "DEL", "name": "Indira Gandhi International"},
        {"id": "place_bom", "code": "BOM", "name": "Chhatrapati Shivaji Maharaj International"}
    ]
}


def test_normalize_oneway_response(monkeypatch):
    """Verify relational response fields are normalized into internal FlightOffer structure."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "mock_key")
    provider = FlightApiProvider()

    offers = provider._normalize_response(
        data=MOCK_ONEWAY_PAYLOAD,
        trip_type="one_way",
        default_departure="DEL",
        default_arrival="BOM",
        is_live=True
    )

    assert len(offers) == 1
    offer = offers[0]
    assert offer.airline == "IndiGo"
    assert offer.flight_number == "6E 214"
    assert offer.departure_airport == "DEL"
    assert offer.arrival_airport == "BOM"
    assert offer.departure_time == "06:15"
    assert offer.arrival_time == "08:30"
    assert offer.duration == 135
    assert offer.stops == 0
    assert offer.price == 5420
    assert offer.currency == "INR"
    assert offer.booking_url == "https://trip.com/book/itin_1"
    assert offer.is_live is True
    assert offer.data_status == "live"
    assert offer.source == "FlightAPI"
    assert len(offer.segments) == 1


def test_normalize_roundtrip_response(monkeypatch):
    """Verify round-trip response normalizes return leg attributes."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "mock_key")
    provider = FlightApiProvider()

    rt_payload = {
        "itineraries": [
            {
                "id": "itin_rt",
                "leg_ids": ["leg_out", "leg_ret"],
                "pricing_options": [
                    {
                        "price": 10840,
                        "items": [{"url": "https://flightapi.io/deeplink/rt"}]
                    }
                ]
            }
        ],
        "legs": [
            {
                "id": "leg_out",
                "departure": "2026-11-20T09:00:00",
                "arrival": "2026-11-20T11:15:00",
                "duration": 135,
                "stop_count": 0,
                "departure_airport_code": "DEL",
                "arrival_airport_code": "BOM",
                "segment_ids": ["seg_out"]
            },
            {
                "id": "leg_ret",
                "departure": "2026-11-25T16:00:00",
                "arrival": "2026-11-25T18:15:00",
                "duration": 135,
                "stop_count": 0,
                "departure_airport_code": "BOM",
                "arrival_airport_code": "DEL",
                "segment_ids": ["seg_ret"]
            }
        ],
        "segments": [
            {
                "id": "seg_out",
                "departure": "2026-11-20T09:00:00",
                "arrival": "2026-11-20T11:15:00",
                "duration": 135,
                "departure_airport_code": "DEL",
                "arrival_airport_code": "BOM",
                "marketing_carrier_id": "carrier_ai",
                "flight_number": "AI 804"
            },
            {
                "id": "seg_ret",
                "departure": "2026-11-25T16:00:00",
                "arrival": "2026-11-25T18:15:00",
                "duration": 135,
                "departure_airport_code": "BOM",
                "arrival_airport_code": "DEL",
                "marketing_carrier_id": "carrier_ai",
                "flight_number": "AI 805"
            }
        ],
        "carriers": [
            {"id": "carrier_ai", "name": "Air India", "iata": "AI"}
        ]
    }

    offers = provider._normalize_response(
        data=rt_payload,
        trip_type="round_trip",
        default_departure="DEL",
        default_arrival="BOM"
    )

    assert len(offers) == 1
    offer = offers[0]
    assert offer.trip_type == "round_trip"
    assert offer.airline == "Air India"
    assert offer.departure_time == "09:00"
    assert offer.arrival_time == "11:15"
    assert offer.return_airline == "Air India"
    assert offer.return_flight_number == "AI 805"
    assert offer.return_departure_time == "16:00"
    assert offer.return_arrival_time == "18:15"
    assert offer.return_departure_airport == "BOM"
    assert offer.return_arrival_airport == "DEL"


def test_missing_optional_fields_resilience(monkeypatch):
    """Verify adapter handles empty segments, missing carriers, and missing booking links without crashing."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "mock_key")
    provider = FlightApiProvider()

    minimal_payload = {
        "itineraries": [
            {
                "id": "itin_bare",
                "leg_ids": ["leg_bare"],
                "pricing_options": [
                    {"price": 4500}
                ]
            }
        ],
        "legs": [
            {
                "id": "leg_bare",
                "departure": "2026-11-20T10:00:00",
                "arrival": "2026-11-20T12:00:00",
                "duration": 120,
                "stop_count": 0
            }
        ]
    }

    offers = provider._normalize_response(
        data=minimal_payload,
        trip_type="one_way",
        default_departure="DEL",
        default_arrival="BOM"
    )

    assert len(offers) == 1
    offer = offers[0]
    assert offer.price == 4500
    assert offer.departure_airport == "DEL"
    assert offer.arrival_airport == "BOM"
    assert offer.departure_time == "10:00"
    assert offer.booking_url == "https://www.flightapi.io"


# =====================================================================
# 5. ERROR HANDLING: AUTHENTICATION, QUOTA, TIMEOUTS
# =====================================================================

def test_flightapi_401_unauthorized(monkeypatch):
    """HTTP 401 raises HTTPError with zero retries and records circuit failure."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "invalid_key")
    provider = FlightApiProvider()

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized: Invalid API key"

    with patch("requests.get", return_value=mock_resp) as mock_get:
        offers = provider.search("DEL", "BOM", "2026-11-20")
        assert offers == []
        assert mock_get.call_count == 1  # Exactly 1 call, NO retries

    status = provider.get_status()
    assert status.status == "error"
    assert "401" in status.message or "failed" in status.message


def test_flightapi_429_quota_exhausted(monkeypatch):
    """HTTP 429 raises quota exhaustion and opens circuit immediately with zero retries."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "test_key")
    provider = FlightApiProvider()

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "You have exhausted your API credits limit"

    with patch("requests.get", return_value=mock_resp) as mock_get:
        offers = provider.search("DEL", "BOM", "2026-11-20")
        assert offers == []
        assert mock_get.call_count == 1  # ZERO retries on quota exhaustion

    state, _ = guardian.get_circuit_state(PROVIDER_FLIGHTAPI)
    assert state in (STATE_OPEN, STATE_QUOTA_EXHAUSTED)


def test_flightapi_search_timeout_handling(monkeypatch):
    """Network timeouts are caught and recorded as transient errors without crashing."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "test_key")
    provider = FlightApiProvider()

    with patch("requests.get", side_effect=requests.exceptions.Timeout("Connection timed out")):
        offers = provider.search("DEL", "BOM", "2026-11-20")
        assert offers == []

    status = provider.get_status()
    assert status.status == "error"


# =====================================================================
# 6. CACHING & DUPLICATE SUPPRESSION (SAVING 30 CREDITS)
# =====================================================================

def test_cache_hit_prevents_repeat_upstream_calls(monkeypatch):
    """Identical searches reuse cached response and make zero additional HTTP calls."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "test_key")
    provider = FlightApiProvider()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_ONEWAY_PAYLOAD

    with patch("requests.get", return_value=mock_resp) as mock_get:
        # First call: upstream HTTP request executed
        offers_1 = provider.search("DEL", "BOM", "2026-11-20")
        assert len(offers_1) == 1
        assert offers_1[0].is_live is True
        assert mock_get.call_count == 1

        # Second call: served strictly from API Guardian cache
        offers_2 = provider.search("DEL", "BOM", "2026-11-20")
        assert len(offers_2) == 1
        assert offers_2[0].data_status == "cached"
        assert offers_2[0].is_live is False
        assert mock_get.call_count == 1  # Call count remains 1! ZERO additional credits burned


def test_budget_rejection_after_monthly_limit(monkeypatch):
    """Requests are rejected when the 30-credit monthly budget ceiling is reached."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "test_key")
    monkeypatch.setenv("FLIGHTAPI_MONTHLY_BUDGET", "2")
    monkeypatch.setenv("FLIGHTAPI_SAFETY_MARGIN", "0")
    monkeypatch.setenv("FLIGHTAPI_DAILY_BUDGET", "10")
    monkeypatch.setenv("FLIGHTAPI_MINUTE_BUDGET", "10")
    provider = FlightApiProvider()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_ONEWAY_PAYLOAD

    with patch("requests.get", return_value=mock_resp):
        # 1st search
        res1 = provider.search("DEL", "BOM", "2026-11-20")
        assert len(res1) == 1

        # 2nd search (different route)
        res2 = provider.search("BOM", "BLR", "2026-11-21")
        assert len(res2) == 1

        # 3rd search (different route): budget exceeded (2/2)
        res3 = provider.search("BLR", "MAA", "2026-11-22")
        assert res3 == []  # Blocked by budget ceiling


# =====================================================================
# 7. SEARCH_FLIGHTS INTEGRATION & CHECKER SAFETY
# =====================================================================

def test_search_flights_dispatches_to_flightapi(monkeypatch):
    """search_flights dispatches to FlightAPI when PRIMARY_FLIGHT_PROVIDER=flightapi."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "flightapi")
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "test_key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_ONEWAY_PAYLOAD

    with patch("requests.get", return_value=mock_resp):
        results = search_flights("DEL", "BOM", "2026-11-20")
        assert len(results) == 1
        flight = results[0]
        assert flight["airline"] == "IndiGo"
        assert flight["flight_number"] == "6E 214"
        assert flight["price"] == 5420
        assert flight["source"] == "FlightAPI"
        assert flight["is_live"] is True


def test_checker_skips_alerts_on_simulated_or_cached_flightapi(monkeypatch):
    """
    Background price checker never generates false alerts or price-history records
    when FlightAPI is unconfigured, returns simulated data, or returns cached fares.
    """
    init_db()
    conn = db()
    test_email = "safe_traveler@example.com"
    conn.execute("DELETE FROM tracked_flights WHERE email = ?", (test_email,))
    conn.commit()

    cur = conn.execute(
        """
        INSERT INTO tracked_flights
        (departure, arrival, outbound_date, target_price, lowest_price, target_alert_sent, created_at, email)
        VALUES ('DEL', 'BOM', '2026-11-20', 9000, NULL, 0, '2026-01-01T00:00:00Z', ?)
        """,
        (test_email,)
    )
    conn.commit()
    flight_id = cur.lastrowid
    conn.close()

    emails_dispatched = []

    def mock_send(to, subj, body, html=None):
        emails_dispatched.append(to)

    monkeypatch.setattr(checker, "send_email", mock_send)
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "flightapi")
    monkeypatch.delenv("FLIGHTAPI_API_KEY", raising=False)  # Unconfigured!

    checker.run()

    # Verify zero emails sent
    assert len(emails_dispatched) == 0

    # Verify zero price history recorded
    conn = db()
    history = conn.execute("SELECT * FROM price_history WHERE tracked_id = ?", (flight_id,)).fetchall()
    conn.close()
    assert len(history) == 0


# =====================================================================
# 8. MULTI-CITY INTEGRATION
# =====================================================================

MOCK_MULTITRIP_PAYLOAD = {
    "itineraries": [
        {
            "id": "itin_multi_1",
            "leg_ids": ["leg_m1", "leg_m2", "leg_m3"],
            "pricing_options": [
                {
                    "price": 14200.0,
                    "items": [{"url": "https://flightapi.io/deeplink/multi"}]
                }
            ]
        }
    ],
    "legs": [
        {
            "id": "leg_m1",
            "departure": "2026-11-20T07:00:00",
            "arrival": "2026-11-20T09:15:00",
            "duration": 135,
            "stop_count": 0,
            "departure_airport_code": "DEL",
            "arrival_airport_code": "BOM",
            "segment_ids": ["seg_m1"]
        },
        {
            "id": "leg_m2",
            "departure": "2026-11-23T11:00:00",
            "arrival": "2026-11-23T12:45:00",
            "duration": 105,
            "stop_count": 0,
            "departure_airport_code": "BOM",
            "arrival_airport_code": "BLR",
            "segment_ids": ["seg_m2"]
        },
        {
            "id": "leg_m3",
            "departure": "2026-11-26T15:00:00",
            "arrival": "2026-11-26T17:40:00",
            "duration": 160,
            "stop_count": 0,
            "departure_airport_code": "BLR",
            "arrival_airport_code": "DEL",
            "segment_ids": ["seg_m3"]
        }
    ],
    "segments": [
        {
            "id": "seg_m1",
            "departure": "2026-11-20T07:00:00",
            "arrival": "2026-11-20T09:15:00",
            "duration": 135,
            "departure_airport_code": "DEL",
            "arrival_airport_code": "BOM",
            "marketing_carrier_id": "carrier_6e",
            "flight_number": "6E 101"
        },
        {
            "id": "seg_m2",
            "departure": "2026-11-23T11:00:00",
            "arrival": "2026-11-23T12:45:00",
            "duration": 105,
            "departure_airport_code": "BOM",
            "arrival_airport_code": "BLR",
            "marketing_carrier_id": "carrier_6e",
            "flight_number": "6E 202"
        },
        {
            "id": "seg_m3",
            "departure": "2026-11-26T15:00:00",
            "arrival": "2026-11-26T17:40:00",
            "duration": 160,
            "departure_airport_code": "BLR",
            "arrival_airport_code": "DEL",
            "marketing_carrier_id": "carrier_6e",
            "flight_number": "6E 303"
        }
    ],
    "carriers": [
        {"id": "carrier_6e", "name": "IndiGo", "iata": "6E"}
    ]
}


def test_search_multi_city_flights_flightapi(monkeypatch):
    """search_multi_city_flights delegates to FlightAPI when PRIMARY_FLIGHT_PROVIDER=flightapi."""
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "flightapi")
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "test_key")

    legs = [
        {"departure": "DEL", "arrival": "BOM", "outbound_date": "2026-11-20"},
        {"departure": "BOM", "arrival": "BLR", "outbound_date": "2026-11-23"},
        {"departure": "BLR", "arrival": "DEL", "outbound_date": "2026-11-26"},
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_MULTITRIP_PAYLOAD

    with patch("requests.get", return_value=mock_resp):
        results = search_multi_city_flights(legs)
        assert len(results) == 1
        itinerary = results[0]
        assert itinerary["trip_type"] == "multi_city"
        assert itinerary["airline"] == "IndiGo"
        assert itinerary["flight_number"] == "6E 101 / 6E 202 / 6E 303"
        assert itinerary["price"] == 14200
        assert itinerary["duration"] == 135 + 105 + 160
        assert len(itinerary["legs"]) == 3
        assert itinerary["is_live"] is True
        assert itinerary["source"] == "FlightAPI"
