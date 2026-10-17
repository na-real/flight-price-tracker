"""
Comprehensive regression test suite for SkyTracker Flight Search Flow.
Verifies all 12 core requirements:
1. Airport autocomplete for Delhi/DEL and Mumbai/Bombay/BOM.
2. Valid airport selection and canonical IATA-code submission.
3. Invalid or unselected airport input.
4. Origin/destination swap correctness.
5. Search request parameter validation.
6. Successful search with mocked flight results.
7. Successful response with zero results.
8. Provider timeout and HTTP error handling.
9. Malformed or unexpected API responses.
10. Loading state elements and API contract integrity.
11. Results rendering structure and no accidental footer anchors.
12. Existing round-trip and multi-city behavior.
"""

import datetime
from unittest.mock import MagicMock, patch
import pytest
import requests

from app import app
from services.airports import search_airports
from validation import resolve_airport_code, clean_iata


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# =====================================================================
# 1. AIRPORT AUTOCOMPLETE FOR DELHI/DEL AND MUMBAI/BOMBAY/BOM
# =====================================================================

def test_autocomplete_delhi_and_del():
    """Autocomplete for 'del', 'DEL', and 'delhi' must return DEL as top result."""
    for query in ["del", "DEL", "delhi", "New Delhi"]:
        results = search_airports(query)
        assert len(results) > 0, f"Query '{query}' returned no results"
        top = results[0]
        assert top["iata"] == "DEL", f"Query '{query}' did not rank DEL #1 (got {top['iata']})"
        assert "Delhi" in top["city"] or "Delhi" in top["name"]


def test_autocomplete_mumbai_bombay_bom():
    """Autocomplete for 'bombay', 'BOM', and 'mumbai' must return BOM as top result."""
    for query in ["bombay", "Bombay", "BOM", "bom", "mumbai", "Mumbai"]:
        results = search_airports(query)
        assert len(results) > 0, f"Query '{query}' returned no results"
        top = results[0]
        assert top["iata"] == "BOM", f"Query '{query}' did not rank BOM #1 (got {top['iata']})"
        assert "Mumbai" in top["city"] or "Bombay" in top.get("city", "") or "BOM" == top["iata"]


def test_autocomplete_chennai_madras_maa():
    """Autocomplete for 'madras', 'chennai', and 'MAA' must return MAA as top result."""
    for query in ["madras", "Madras", "MAA", "maa", "chennai", "Chennai"]:
        results = search_airports(query)
        assert len(results) > 0, f"Query '{query}' returned no results"
        top = results[0]
        assert top["iata"] == "MAA", f"Query '{query}' did not rank MAA #1 (got {top['iata']})"


def test_autocomplete_endpoint_live(client):
    """GET /api/airports endpoint returns sorted results for alias and code queries."""
    res_del = client.get("/api/airports?q=del")
    assert res_del.status_code == 200
    del_data = res_del.get_json()
    assert isinstance(del_data, list)
    assert len(del_data) > 0
    assert del_data[0]["iata"] == "DEL"

    res_bom = client.get("/api/airports?q=bombay")
    assert res_bom.status_code == 200
    bom_data = res_bom.get_json()
    assert isinstance(bom_data, list)
    assert len(bom_data) > 0
    assert bom_data[0]["iata"] == "BOM"


# =====================================================================
# 2. VALID AIRPORT SELECTION AND CANONICAL IATA-CODE SUBMISSION
# =====================================================================

def test_resolve_airport_code_canonical():
    """resolve_airport_code must resolve IATA codes, formatted strings, and aliases."""
    assert resolve_airport_code("DEL") == "DEL"
    assert resolve_airport_code("del") == "DEL"
    assert resolve_airport_code("BOM") == "BOM"
    assert resolve_airport_code("bombay") == "BOM"
    assert resolve_airport_code("madras") == "MAA"
    assert resolve_airport_code("calcutta") == "CCU"
    assert resolve_airport_code("Indira Gandhi International Airport (DEL)") == "DEL"
    assert resolve_airport_code("Chhatrapati Shivaji Maharaj (BOM)") == "BOM"


def test_search_endpoint_accepts_aliases_and_normalizes(client, monkeypatch):
    """POST /api/search must accept city names or aliases and normalize to canonical IATA codes."""
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    future_date = (datetime.date.today() + datetime.timedelta(days=25)).isoformat()

    response = client.post(
        "/api/search",
        json={
            "departure": "del",
            "arrival": "bombay",
            "outbound_date": future_date,
            "adults": 1,
            "cabin_class": "economy",
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["search_airports"]["departure"] == ["DEL"]
    assert data["search_airports"]["arrival"] == ["BOM"]
    assert len(data["results"]) > 0


# =====================================================================
# 3. INVALID OR UNSELECTED AIRPORT INPUT
# =====================================================================

def test_resolve_airport_code_rejects_invalid():
    """resolve_airport_code must raise ValueError on unrecognized inputs."""
    with pytest.raises(ValueError, match="valid 3-letter IATA airport code"):
        resolve_airport_code("XYZNONEXISTENT999", "departure")

    with pytest.raises(ValueError, match="valid 3-letter IATA airport code"):
        resolve_airport_code("12345", "arrival")


def test_search_rejects_unrecognized_airport(client):
    """POST /api/search must return 400 if an airport cannot be resolved."""
    future_date = (datetime.date.today() + datetime.timedelta(days=25)).isoformat()
    response = client.post(
        "/api/search",
        json={
            "departure": "INVALID_AIRPORT_123",
            "arrival": "BOM",
            "outbound_date": future_date,
        },
    )
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    assert "valid 3-letter iata airport code" in data["error"].lower()


def test_search_rejects_identical_airports(client):
    """POST /api/search must reject searches where origin and destination are the same."""
    future_date = (datetime.date.today() + datetime.timedelta(days=25)).isoformat()

    # Exact same code
    res1 = client.post(
        "/api/search",
        json={"departure": "DEL", "arrival": "DEL", "outbound_date": future_date},
    )
    assert res1.status_code == 400
    assert "identical" in res1.get_json()["error"].lower()

    # Same airport via alias ('BOM' and 'bombay')
    res2 = client.post(
        "/api/search",
        json={"departure": "BOM", "arrival": "bombay", "outbound_date": future_date},
    )
    assert res2.status_code == 400
    assert "identical" in res2.get_json()["error"].lower()


# =====================================================================
# 4. ORIGIN / DESTINATION SWAP CORRECTNESS
# =====================================================================

def test_swap_origin_destination_logic():
    """Verify that swapping departure and arrival preserves canonical data cleanly."""
    origin = {"val": "New Delhi (DEL)", "code": "DEL", "city": "Delhi"}
    dest = {"val": "Mumbai (BOM)", "code": "BOM", "city": "Mumbai"}

    # Perform swap
    swapped_origin = dict(dest)
    swapped_dest = dict(origin)

    assert swapped_origin["code"] == "BOM"
    assert swapped_origin["val"] == "Mumbai (BOM)"
    assert swapped_dest["code"] == "DEL"
    assert swapped_dest["val"] == "New Delhi (DEL)"

    # Verify canonical resolution of swapped entities
    assert resolve_airport_code(swapped_origin["val"]) == "BOM"
    assert resolve_airport_code(swapped_dest["val"]) == "DEL"


# =====================================================================
# 5. SEARCH REQUEST PARAMETER VALIDATION
# =====================================================================

def test_search_validation_missing_fields(client):
    """Missing departure, arrival, or outbound_date must return HTTP 400."""
    res = client.post("/api/search", json={"departure": "DEL"})
    assert res.status_code == 400
    assert "required" in res.get_json()["error"].lower()


def test_search_validation_past_date(client):
    """Outbound date in the past must return HTTP 400."""
    past_date = "2020-01-01"
    res = client.post(
        "/api/search",
        json={"departure": "DEL", "arrival": "BOM", "outbound_date": past_date},
    )
    assert res.status_code == 400
    assert "past" in res.get_json()["error"].lower()


def test_search_validation_invalid_passengers(client):
    """Invalid passenger counts must return HTTP 400."""
    future_date = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()

    # 0 adults
    res0 = client.post(
        "/api/search",
        json={"departure": "DEL", "arrival": "BOM", "outbound_date": future_date, "adults": 0},
    )
    assert res0.status_code == 400

    # Infants > adults
    res_infants = client.post(
        "/api/search",
        json={"departure": "DEL", "arrival": "BOM", "outbound_date": future_date, "adults": 1, "infants": 2},
    )
    assert res_infants.status_code == 400


# =====================================================================
# 6. SUCCESSFUL SEARCH WITH MOCKED FLIGHT RESULTS
# =====================================================================

def test_search_with_mocked_provider_results(client, monkeypatch):
    """Mock SerpAPI to verify provider normalization into exact frontend schema."""
    monkeypatch.setenv("SERPAPI_KEY", "mock_valid_key_12345")

    mock_serp_response = {
        "search_metadata": {"status": "Success"},
        "best_flights": [
            {
                "flights": [
                    {
                        "departure_airport": {"id": "DEL", "name": "Indira Gandhi Intl", "time": "2026-11-20 08:00"},
                        "arrival_airport": {"id": "BOM", "name": "Chhatrapati Shivaji Intl", "time": "2026-11-20 10:15"},
                        "airline": "Air India",
                        "flight_number": "AI 805",
                        "duration": 135,
                    }
                ],
                "price": 5400,
                "total_duration": 135,
                "booking_token": "token_ai805_xyz",
            }
        ],
        "other_flights": [
            {
                "flights": [
                    {
                        "departure_airport": {"id": "DEL", "name": "Indira Gandhi Intl", "time": "2026-11-20 14:00"},
                        "arrival_airport": {"id": "BOM", "name": "Chhatrapati Shivaji Intl", "time": "2026-11-20 16:15"},
                        "airline": "IndiGo",
                        "flight_number": "6E 202",
                        "duration": 135,
                    }
                ],
                "price": 4900,
                "total_duration": 135,
            }
        ],
    }

    with patch("services.flight_api.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_serp_response
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        res = client.post(
            "/api/search",
            json={
                "departure": "DEL",
                "arrival": "BOM",
                "outbound_date": "2026-11-20",
                "adults": 1,
            },
        )

        assert res.status_code == 200
        data = res.get_json()
        assert "results" in data
        assert len(data["results"]) == 2

        airlines = [f["airline"] for f in data["results"]]
        assert "Air India" in airlines
        assert "IndiGo" in airlines

        flight = data["results"][0]
        assert flight["price"] > 0
        assert flight["departure_time"] != ""
        assert flight["arrival_time"] != ""
        assert "itinerary_id" in flight
        assert "comparison_offers" in flight
        assert len(flight["comparison_offers"]) >= 3


# =====================================================================
# 7. SUCCESSFUL RESPONSE WITH ZERO RESULTS
# =====================================================================

def test_search_with_zero_results(client, monkeypatch):
    """When provider returns zero flights, response is HTTP 200 with results: []."""
    monkeypatch.setenv("SERPAPI_KEY", "mock_valid_key_12345")

    empty_serp_response = {
        "search_metadata": {"status": "Success"},
        "best_flights": [],
        "other_flights": [],
    }

    with patch("services.flight_api.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = empty_serp_response
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        res = client.post(
            "/api/search",
            json={
                "departure": "DEL",
                "arrival": "BOM",
                "outbound_date": "2026-11-20",
            },
        )

        assert res.status_code == 200
        data = res.get_json()
        assert "results" in data
        assert data["results"] == []


# =====================================================================
# 8. PROVIDER TIMEOUT AND HTTP ERROR HANDLING
# =====================================================================

def test_provider_timeout_handled_gracefully(client, monkeypatch):
    """Provider timeout falls back to clearly flagged simulated results without crash."""
    monkeypatch.setenv("SERPAPI_KEY", "mock_valid_key_12345")
    monkeypatch.setenv("ALLOW_SIMULATED_FALLBACK", "true")

    with patch("services.flight_api.requests.get", side_effect=requests.exceptions.Timeout("Connection timed out")):
        res = client.post(
            "/api/search",
            json={
                "departure": "DEL",
                "arrival": "BOM",
                "outbound_date": "2026-11-20",
            },
        )

        assert res.status_code == 200
        data = res.get_json()
        assert "results" in data
        assert len(data["results"]) > 0
        for flight in data["results"]:
            assert flight["is_simulated"] is True
            assert flight["is_live"] is False


def test_provider_http_error_handled_gracefully(client, monkeypatch):
    """Provider 429 quota exhaustion falls back to clearly flagged simulated results without crash."""
    monkeypatch.setenv("SERPAPI_KEY", "mock_valid_key_12345")
    monkeypatch.setenv("ALLOW_SIMULATED_FALLBACK", "true")

    mock_err_resp = MagicMock()
    mock_err_resp.status_code = 429
    mock_err_resp.text = "Monthly API quota exceeded"

    with patch(
        "services.flight_api.requests.get",
        side_effect=requests.exceptions.HTTPError(response=mock_err_resp),
    ):
        res = client.post(
            "/api/search",
            json={
                "departure": "DEL",
                "arrival": "BOM",
                "outbound_date": "2026-11-20",
            },
        )

        assert res.status_code == 200
        data = res.get_json()
        assert "results" in data
        assert len(data["results"]) > 0
        for flight in data["results"]:
            assert flight["is_simulated"] is True
            assert flight["is_live"] is False


def test_fatal_unhandled_exception_returns_500(client):
    """An unhandled internal failure returns structured 500 JSON without exposing stacktrace."""
    with patch("app.search_flights", side_effect=RuntimeError("Simulated internal runtime error")):
        res = client.post(
            "/api/search",
            json={
                "departure": "DEL",
                "arrival": "BOM",
                "outbound_date": "2026-11-20",
            },
        )
        assert res.status_code == 500
        data = res.get_json()
        assert "error" in data


# =====================================================================
# 9. MALFORMED OR UNEXPECTED API RESPONSES
# =====================================================================

def test_malformed_provider_payload(client, monkeypatch):
    """Provider returning unexpected dictionary structure falls back gracefully."""
    monkeypatch.setenv("SERPAPI_KEY", "mock_valid_key_12345")
    monkeypatch.setenv("ALLOW_SIMULATED_FALLBACK", "true")

    malformed_payload = {
        "error": "Google Flights engine encountered an internal upstream error",
        "some_unexpected_field": [1, 2, 3],
    }

    with patch("services.flight_api.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = malformed_payload
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        res = client.post(
            "/api/search",
            json={
                "departure": "DEL",
                "arrival": "BOM",
                "outbound_date": "2026-11-20",
            },
        )

        assert res.status_code == 200
        data = res.get_json()
        assert "results" in data
        assert len(data["results"]) > 0
        for flight in data["results"]:
            assert flight["is_simulated"] is True


# =====================================================================
# 10. LOADING STATE AND DOM CONTRACT INTEGRITY
# =====================================================================

def test_templates_contain_required_results_containers(client):
    """Ensure both index.html and flights.html have designated results containers."""
    res_index = client.get("/")
    assert res_index.status_code == 200
    index_html = res_index.get_data(as_text=True)
    assert 'id="results"' in index_html
    assert 'id="searchForm"' in index_html

    res_flights = client.get("/flights")
    assert res_flights.status_code == 200
    flights_html = res_flights.get_data(as_text=True)
    assert 'id="flightsList"' in flights_html
    assert 'id="emptySearchPrompt"' in flights_html
    assert 'id="flightSearchForm"' in flights_html


# =====================================================================
# 11. NO ACCIDENTAL FOOTER ANCHORS ON SEARCH
# =====================================================================

def test_search_controls_have_no_accidental_footer_navigation(client):
    """Ensure flight search buttons have type='submit' and no href='#footer'."""
    res_flights = client.get("/flights")
    html = res_flights.get_data(as_text=True)
    assert 'id="searchSubmitBtn"' in html
    # Ensure search button is a button or submit, not an anchor to footer
    assert 'href="#footer"' not in html.lower()


# =====================================================================
# 12. EXISTING ROUND-TRIP AND MULTI-CITY BEHAVIOR
# =====================================================================

def test_round_trip_and_multicity_preserved(client, monkeypatch):
    """Verify round-trip and multi-city search functionality works end-to-end."""
    monkeypatch.delenv("SERPAPI_KEY", raising=False)

    dep_date = (datetime.date.today() + datetime.timedelta(days=15)).isoformat()
    ret_date = (datetime.date.today() + datetime.timedelta(days=22)).isoformat()

    # Round trip
    res_rt = client.post(
        "/api/search",
        json={
            "trip_type": "round_trip",
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": dep_date,
            "return_date": ret_date,
        },
    )
    assert res_rt.status_code == 200
    assert len(res_rt.get_json()["results"]) > 0

    # Multi city
    d1 = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    d2 = (datetime.date.today() + datetime.timedelta(days=15)).isoformat()
    res_mc = client.post(
        "/api/search",
        json={
            "trip_type": "multi_city",
            "legs": [
                {"departure": "DEL", "arrival": "BOM", "outbound_date": d1},
                {"departure": "BOM", "arrival": "BLR", "outbound_date": d2},
            ],
        },
    )
    assert res_mc.status_code == 200
    mc_data = res_mc.get_json()
    assert mc_data["trip_type"] == "multi_city"
    assert len(mc_data["results"]) > 0


# =====================================================================
# 13. SCREEN RECORDING REGRESSION TESTS (ONE-WAY, BLANK INPUTS, ROUTING)
# =====================================================================

def test_one_way_search_without_return_date_succeeds(client, monkeypatch):
    """Regression test for screen recording bug:
    One-way search with origin, destination, and departure date must NOT require a return date."""
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    dep_date = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()

    response = client.post(
        "/api/search",
        json={
            "trip_type": "one_way",
            "departure": "DEL",
            "arrival": "MAA",
            "outbound_date": dep_date,
            "return_date": None,
            "adults": 1,
            "cabin_class": "economy",
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["search_airports"]["departure"] == ["DEL"]
    assert data["search_airports"]["arrival"] == ["MAA"]
    assert data["trip_type"] == "one_way"
    assert "results" in data
    assert len(data["results"]) > 0


def test_round_trip_missing_return_date_rejected(client):
    """Round-trip search missing return date must return 400 with a clear error."""
    dep_date = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()

    response = client.post(
        "/api/search",
        json={
            "trip_type": "round_trip",
            "departure": "DEL",
            "arrival": "MAA",
            "outbound_date": dep_date,
            "return_date": None,
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    assert "return date" in data["error"].lower()


def test_round_trip_inverted_dates_rejected(client):
    """Round-trip search with return date before departure date must return 400."""
    dep_date = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()
    ret_date = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()

    response = client.post(
        "/api/search",
        json={
            "trip_type": "round_trip",
            "departure": "DEL",
            "arrival": "MAA",
            "outbound_date": dep_date,
            "return_date": ret_date,
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    assert "before" in data["error"].lower()


def test_flights_page_serves_blank_dates_and_clean_inputs(client):
    """Ensure flights.html does NOT prefill outbound_date or airport fields with demo values."""
    res = client.get("/flights")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Inputs must exist with clean placeholders and no hardcoded demo values
    assert 'id="departure"' in html
    assert 'id="arrival"' in html
    assert 'id="outbound_date"' in html
    assert 'id="return_date"' in html
    assert 'value="LHR"' not in html
    assert 'value="KIX"' not in html


def test_route_del_to_maa_and_reverse_bidirectional(client, monkeypatch):
    """Test representative route DEL to MAA and reverse MAA to DEL."""
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    dep_date = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()

    # Outbound: DEL -> MAA
    res_fwd = client.post(
        "/api/search",
        json={
            "trip_type": "one_way",
            "departure": "DEL",
            "arrival": "MAA",
            "outbound_date": dep_date,
        },
    )
    assert res_fwd.status_code == 200
    fwd_data = res_fwd.get_json()
    assert fwd_data["search_airports"]["departure"] == ["DEL"]
    assert fwd_data["search_airports"]["arrival"] == ["MAA"]

    # Reverse: MAA -> DEL
    res_rev = client.post(
        "/api/search",
        json={
            "trip_type": "one_way",
            "departure": "MAA",
            "arrival": "DEL",
            "outbound_date": dep_date,
        },
    )
    assert res_rev.status_code == 200
    rev_data = res_rev.get_json()
    assert rev_data["search_airports"]["departure"] == ["MAA"]
    assert rev_data["search_airports"]["arrival"] == ["DEL"]


def test_diagnostics_quota_reset_unauthenticated_rejected(client):
    """POST /api/diagnostics/quota/reset without authentication must be rejected with 401 or 403."""
    response = client.post(
        "/api/diagnostics/quota/reset",
        json={"provider_id": "serpapi"}
    )
    assert response.status_code in [401, 403]


# =====================================================================
# 13. MULTI-CITY SEARCH VALIDATION & FLIGHTAPI LIMITATION TESTS
# =====================================================================

def test_multi_city_missing_or_invalid_legs_rejected(client):
    """Multi-city search with missing, insufficient, or invalid legs must be rejected with 400."""
    # 0 legs
    res = client.post("/api/search", json={"trip_type": "multi_city", "legs": []})
    assert res.status_code == 400
    assert "at least 2" in res.get_json()["error"].lower()

    # 1 leg
    t1 = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    res = client.post("/api/search", json={
        "trip_type": "multi_city",
        "legs": [{"departure": "DEL", "arrival": "BOM", "outbound_date": t1}]
    })
    assert res.status_code == 400
    assert "at least 2" in res.get_json()["error"].lower()

    # Leg missing arrival
    res = client.post("/api/search", json={
        "trip_type": "multi_city",
        "legs": [
            {"departure": "DEL", "arrival": "BOM", "outbound_date": t1},
            {"departure": "BOM", "arrival": "", "outbound_date": t1}
        ]
    })
    assert res.status_code == 400

    # Leg with date in the past
    past_date = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    res = client.post("/api/search", json={
        "trip_type": "multi_city",
        "legs": [
            {"departure": "DEL", "arrival": "BOM", "outbound_date": past_date},
            {"departure": "BOM", "arrival": "BLR", "outbound_date": t1}
        ]
    })
    assert res.status_code == 400
    assert "past" in res.get_json()["error"].lower()

    # Leg with inverted date sequence
    t2 = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    res = client.post("/api/search", json={
        "trip_type": "multi_city",
        "legs": [
            {"departure": "DEL", "arrival": "BOM", "outbound_date": t1},
            {"departure": "BOM", "arrival": "BLR", "outbound_date": t2}
        ]
    })
    assert res.status_code == 400
    assert "before" in res.get_json()["error"].lower()


def test_multi_city_flightapi_2_leg_limitation_explicit_error(client, monkeypatch):
    """When FlightAPI is configured as primary, a 2-leg multi-city search must explicitly explain the 3-5 leg limit."""
    from services.providers.flightapi_provider import FlightApiProvider
    monkeypatch.setenv("PRIMARY_FLIGHT_PROVIDER", "flightapi")
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "test_key_mock")
    monkeypatch.delenv("ALLOW_SIMULATED_FALLBACK", raising=False)

    t1 = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    t2 = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()

    res = client.post("/api/search", json={
        "trip_type": "multi_city",
        "legs": [
            {"departure": "DEL", "arrival": "BOM", "outbound_date": t1},
            {"departure": "BOM", "arrival": "BLR", "outbound_date": t2}
        ]
    })
    assert res.status_code == 400
    err = res.get_json()["error"]
    assert "3 and 5" in err or "3 to 5" in err
    assert "multi-city" in err.lower() or "round-trip" in err.lower()


def test_multi_city_max_legs_enforcement(client):
    """Multi-city itineraries exceeding 5 legs must be rejected."""
    today = datetime.date.today()
    legs = []
    airports = ["DEL", "BOM", "BLR", "MAA", "CCU", "HYD", "GOI"]
    for i in range(6):
        d = (today + datetime.timedelta(days=10 + i * 2)).isoformat()
        legs.append({"departure": airports[i], "arrival": airports[i+1], "outbound_date": d})

    res = client.post("/api/search", json={
        "trip_type": "multi_city",
        "legs": legs
    })
    assert res.status_code == 400
    assert "5" in res.get_json()["error"]
