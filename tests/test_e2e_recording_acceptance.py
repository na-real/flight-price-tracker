"""
End-to-End Regression & Acceptance Verification Suite.
Validates the 6 critical production criteria:
1. Fresh search page has blank fields (no demo airports or dates).
2. One-way search succeeds without a return date.
3. Round-trip search rejects a missing return date.
4. Airport autocomplete and swapping preserve the correct canonical IATA codes.
5. Provider failure produces a clear error without fake fares.
6. A valid mocked FlightAPI response reaches the comparison flow correctly.
"""

import pytest
from unittest.mock import patch, MagicMock
from app import app
from services.airports import search_airports
from validation import resolve_airport_code


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# =====================================================================
# 1. FRESH SEARCH PAGE HAS BLANK FIELDS
# =====================================================================

def test_fresh_search_page_has_blank_fields(client):
    """
    Criterion 1: Fresh search page must NOT have prefilled demo airports or departure dates.
    """
    res = client.get("/flights")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Clean placeholders must be present
    assert 'placeholder="Enter origin city or airport"' in html
    assert 'placeholder="Enter destination city or airport"' in html
    assert 'placeholder="Select departure date"' in html
    assert 'placeholder="Select return date"' in html

    # Hardcoded demo airports must NOT be present as values
    assert 'value="LHR"' not in html
    assert 'value="KIX"' not in html
    assert 'value="London"' not in html

    # Hidden trip type input must be present
    assert 'id="tripTypeInput"' in html

    # Index page also has clean search form
    res_index = client.get("/")
    assert res_index.status_code == 200
    html_index = res_index.get_data(as_text=True)
    assert 'placeholder="Enter origin city or airport"' in html_index
    assert 'placeholder="Enter destination city or airport"' in html_index


# =====================================================================
# 2. ONE-WAY SEARCH SUCCEEDS WITHOUT A RETURN DATE
# =====================================================================

def test_one_way_search_succeeds_without_return_date(client, monkeypatch):
    """
    Criterion 2: One-way search requires origin, destination, and departure date only.
    Return date is optional and ignored; does NOT trigger return-date validation error.
    """
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "flightapi")
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "test_mock_key")

    mock_flightapi_response = {
        "itineraries": [{
            "id": "itin_oneway_1",
            "leg_ids": ["leg_1"],
            "pricing_options": [{
                "price": {"amount": 4500},
                "items": [{"deep_link": "https://flightapi.io/book/oneway"}]
            }]
        }],
        "legs": [{
            "id": "leg_1",
            "departure": "2026-11-20T09:00:00",
            "arrival": "2026-11-20T11:45:00",
            "duration": 165,
            "stop_count": 0,
            "departure_airport_code": "DEL",
            "arrival_airport_code": "MAA",
            "airline_codes": ["6E"]
        }],
        "carriers": [
            {"id": "6E", "code": "6E", "name": "IndiGo"}
        ]
    }

    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = mock_flightapi_response

    with patch("requests.get", return_value=mock_resp):
        res = client.post("/api/search", json={
            "trip_type": "one_way",
            "departure": "DEL",
            "arrival": "MAA",
            "outbound_date": "2026-11-20",
            "return_date": None
        })

    assert res.status_code == 200
    data = res.get_json()
    assert data["trip_type"] == "one_way"
    assert "results" in data
    assert len(data["results"]) >= 1
    flight = data["results"][0]
    assert flight["departure_airport"] == "DEL"
    assert flight["arrival_airport"] == "MAA"
    assert flight["price"] == 4500


# =====================================================================
# 3. ROUND-TRIP SEARCH REJECTS A MISSING RETURN DATE
# =====================================================================

def test_round_trip_search_rejects_missing_return_date(client):
    """
    Criterion 3: Round-trip search requires return date and cannot precede departure date.
    """
    # Missing return date entirely
    res1 = client.post("/api/search", json={
        "trip_type": "round_trip",
        "departure": "DEL",
        "arrival": "MAA",
        "outbound_date": "2026-11-20",
        "return_date": None
    })
    assert res1.status_code == 400
    assert "return date is required" in res1.get_json()["error"].lower()

    # Empty string return date
    res2 = client.post("/api/search", json={
        "trip_type": "round_trip",
        "departure": "DEL",
        "arrival": "MAA",
        "outbound_date": "2026-11-20",
        "return_date": ""
    })
    assert res2.status_code == 400
    assert "return date is required" in res2.get_json()["error"].lower()

    # Inverted dates: return date before departure date
    res3 = client.post("/api/search", json={
        "trip_type": "round_trip",
        "departure": "DEL",
        "arrival": "MAA",
        "outbound_date": "2026-11-20",
        "return_date": "2026-11-10"
    })
    assert res3.status_code == 400
    assert "before" in res3.get_json()["error"].lower()


# =====================================================================
# 4. AIRPORT AUTOCOMPLETE AND SWAPPING PRESERVE CORRECT IATA CODES
# =====================================================================

def test_airport_autocomplete_and_swapping_canonical_codes(client):
    """
    Criterion 4: Airport autocomplete and swapping preserve correct canonical IATA codes.
    Tests representative route DEL <-> MAA bidirectionally.
    """
    # Autocomplete for Delhi
    res_del = client.get("/api/airports?q=delhi")
    assert res_del.status_code == 200
    del_airports = res_del.get_json()
    assert len(del_airports) > 0
    assert del_airports[0]["iata"] == "DEL"

    # Autocomplete for Chennai / Madras
    res_maa = client.get("/api/airports?q=madras")
    assert res_maa.status_code == 200
    maa_airports = res_maa.get_json()
    assert len(maa_airports) > 0
    assert maa_airports[0]["iata"] == "MAA"

    # Canonical resolution
    dep_code = resolve_airport_code("delhi")
    arr_code = resolve_airport_code("chennai")
    assert dep_code == "DEL"
    assert arr_code == "MAA"

    # Simulating swap
    swapped_dep, swapped_arr = arr_code, dep_code
    assert swapped_dep == "MAA"
    assert swapped_arr == "DEL"

    # Reverse resolution
    assert resolve_airport_code(swapped_dep) == "MAA"
    assert resolve_airport_code(swapped_arr) == "DEL"


# =====================================================================
# 5. PROVIDER FAILURE PRODUCES CLEAR ERROR WITHOUT FAKE FARES
# =====================================================================

def test_provider_failure_produces_clear_error_without_fake_fares(client, monkeypatch):
    """
    Criterion 5: Upstream provider failure (e.g. 429 quota exhausted or 500 error)
    produces a clear HTTP 503 error and NEVER invents fake fares.
    """
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "flightapi")
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "test_mock_key")
    monkeypatch.delenv("ALLOW_SIMULATED_FALLBACK", raising=False)

    # Mock provider quota exhaustion (HTTP 429)
    mock_exhausted_resp = MagicMock(status_code=429, text="FlightAPI Monthly credits limit exceeded")

    with patch("requests.get", return_value=mock_exhausted_resp):
        res = client.post("/api/search", json={
            "trip_type": "one_way",
            "departure": "DEL",
            "arrival": "MAA",
            "outbound_date": "2026-11-20"
        })

    assert res.status_code == 503
    data = res.get_json()
    assert "error" in data
    assert "quota" in data["error"].lower() or "exhausted" in data["error"].lower() or "unavailable" in data["error"].lower()
    # Must return empty results, never fabricated or simulated fares
    assert data["results"] == []


# =====================================================================
# 6. MOCKED FLIGHTAPI RESPONSE REACHES COMPARISON FLOW CORRECTLY
# =====================================================================

def test_mocked_flightapi_response_reaches_comparison_flow(client, monkeypatch):
    """
    Criterion 6: A valid mocked FlightAPI response normalizes and reaches comparison
    flow with accurate provider labeling, pricing, and comparison offers.
    """
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "flightapi")
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "test_mock_key")

    mock_flightapi_response = {
        "itineraries": [{
            "id": "itin_comp_1",
            "leg_ids": ["leg_comp_1"],
            "pricing_options": [{
                "price": {"amount": 5400},
                "items": [{"deep_link": "https://flightapi.io/deal/5400"}]
            }]
        }],
        "legs": [{
            "id": "leg_comp_1",
            "departure": "2026-11-20T07:15:00",
            "arrival": "2026-11-20T10:00:00",
            "duration": 165,
            "stop_count": 0,
            "departure_airport_code": "DEL",
            "arrival_airport_code": "MAA",
            "airline_codes": ["AI"]
        }],
        "carriers": [
            {"id": "AI", "code": "AI", "name": "Air India"}
        ]
    }

    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = mock_flightapi_response

    with patch("requests.get", return_value=mock_resp):
        res = client.post("/api/search", json={
            "trip_type": "one_way",
            "departure": "DEL",
            "arrival": "MAA",
            "outbound_date": "2026-11-20"
        })

    assert res.status_code == 200
    data = res.get_json()
    assert "results" in data
    assert len(data["results"]) >= 1

    flight = data["results"][0]
    assert flight["source"] == "FlightAPI"
    assert flight["airline"] == "Air India"
    assert flight["price"] == 5400
    assert flight["departure_airport"] == "DEL"
    assert flight["arrival_airport"] == "MAA"
    assert flight["is_live"] is True
    assert flight["is_simulated"] is False

    # Comparison payload must be populated
    assert "comparison" in data
    comparison = data["comparison"]
    assert comparison is not None
    assert "grouped_itineraries" in comparison
    assert "all_offers" in comparison
    assert "stats" in comparison
    assert len(comparison["grouped_itineraries"]) >= 1
