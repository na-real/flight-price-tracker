# pyrefly: ignore [missing-import]
import datetime
import pytest
from app import app
from validation import (
    validate_date_iso,
    validate_date_sequence,
    validate_passenger_counts,
    validate_multi_city_legs,
)
from services.flight_api import search_multi_city_flights


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# =====================================================================
# 1. TRIP TYPE: ONE-WAY SEARCH TESTS
# =====================================================================

def test_oneway_search_success(client, monkeypatch):
    """Test one-way search requires no return date and succeeds."""
    monkeypatch.delenv("SERPAPI_KEY", raising=False)

    future_date = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    response = client.post(
        "/api/search",
        json={
            "trip_type": "one_way",
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": future_date,
            "adults": 2,
            "children": 1,
            "infants": 0,
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert "results" in data
    assert isinstance(data["results"], list)
    assert len(data["results"]) > 0
    assert data.get("trip_type") == "one_way"
    assert data["passengers"]["adults"] == 2
    assert data["passengers"]["children"] == 1
    assert data["passengers"]["total"] == 3


def test_oneway_search_ignores_or_excludes_return_date(client, monkeypatch):
    """One-way search should not require a return date."""
    monkeypatch.delenv("SERPAPI_KEY", raising=False)

    future_date = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()
    response = client.post(
        "/api/search",
        json={
            "trip_type": "one_way",
            "departure": "SFO",
            "arrival": "JFK",
            "outbound_date": future_date,
            "return_date": None,
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert len(data["results"]) > 0


# =====================================================================
# 2. TRIP TYPE: ROUND-TRIP SEARCH TESTS
# =====================================================================

def test_roundtrip_search_requires_return_date(client):
    """Round-trip search must fail if return_date is missing."""
    future_date = (datetime.date.today() + datetime.timedelta(days=15)).isoformat()
    response = client.post(
        "/api/search",
        json={
            "trip_type": "round_trip",
            "departure": "DEL",
            "arrival": "MAA",
            "outbound_date": future_date,
            "return_date": None,
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert "return date is required" in data["error"].lower()


def test_roundtrip_search_rejects_return_before_outbound(client):
    """Round-trip search must reject return date before departure date."""
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
    assert "cannot be before departure date" in data["error"].lower()


def test_roundtrip_search_success(client, monkeypatch):
    """Round-trip search succeeds with valid dates."""
    monkeypatch.delenv("SERPAPI_KEY", raising=False)

    dep_date = (datetime.date.today() + datetime.timedelta(days=15)).isoformat()
    ret_date = (datetime.date.today() + datetime.timedelta(days=22)).isoformat()

    response = client.post(
        "/api/search",
        json={
            "trip_type": "round_trip",
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": dep_date,
            "return_date": ret_date,
            "adults": 1,
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert len(data["results"]) > 0
    assert data.get("trip_type") == "round_trip"


# =====================================================================
# 3. MULTI-CITY ITINERARY & ENDPOINT TESTS
# =====================================================================

def test_multicity_search_success(client, monkeypatch):
    """Multi-city search combines legs into complete itineraries."""
    monkeypatch.delenv("SERPAPI_KEY", raising=False)

    d1 = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    d2 = (datetime.date.today() + datetime.timedelta(days=15)).isoformat()
    d3 = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()

    legs = [
        {"departure": "DEL", "arrival": "BOM", "outbound_date": d1},
        {"departure": "BOM", "arrival": "BLR", "outbound_date": d2},
        {"departure": "BLR", "arrival": "DEL", "outbound_date": d3},
    ]

    response = client.post(
        "/api/search",
        json={
            "trip_type": "multi_city",
            "legs": legs,
            "adults": 2,
            "children": 0,
            "infants": 0,
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data.get("trip_type") == "multi_city"
    assert "results" in data
    assert len(data["results"]) > 0

    # Verify legs preservation in returned itineraries
    first_result = data["results"][0]
    assert first_result.get("trip_type") == "multi_city"
    assert "legs" in first_result
    assert len(first_result["legs"]) == 3
    assert first_result["legs"][0]["departure_airport"] == "DEL"
    assert first_result["legs"][0]["arrival_airport"] == "BOM"
    assert first_result["legs"][1]["departure_airport"] == "BOM"
    assert first_result["legs"][1]["arrival_airport"] == "BLR"
    assert first_result["legs"][2]["departure_airport"] == "BLR"
    assert first_result["legs"][2]["arrival_airport"] == "DEL"

    # Price and duration must represent full multi-city trip
    assert first_result["price"] > 0
    assert first_result["duration"] > 0
    assert "itinerary_id" in first_result


def test_multicity_validation_minimum_legs(client):
    """Multi-city requires at least 2 flight legs."""
    d1 = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    response = client.post(
        "/api/search",
        json={
            "trip_type": "multi_city",
            "legs": [
                {"departure": "DEL", "arrival": "BOM", "outbound_date": d1}
            ],
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert "at least 2 flight legs" in data["error"].lower()


def test_multicity_validation_maximum_legs(client):
    """Multi-city rejects more than 5 flight legs."""
    d = datetime.date.today()
    legs = [
        {"departure": "DEL", "arrival": "BOM", "outbound_date": (d + datetime.timedelta(days=i + 5)).isoformat()}
        for i in range(6)
    ]
    response = client.post(
        "/api/search",
        json={
            "trip_type": "multi_city",
            "legs": legs,
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert "maximum of 5 flight legs" in data["error"].lower()


def test_multicity_validation_same_origin_destination(client):
    """Each leg must have different departure and arrival airports."""
    d1 = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    d2 = (datetime.date.today() + datetime.timedelta(days=15)).isoformat()

    response = client.post(
        "/api/search",
        json={
            "trip_type": "multi_city",
            "legs": [
                {"departure": "DEL", "arrival": "DEL", "outbound_date": d1},
                {"departure": "DEL", "arrival": "BOM", "outbound_date": d2},
            ],
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert "identical" in data["error"].lower()


def test_multicity_validation_descending_dates(client):
    """Multi-city legs must have sequential dates."""
    d1 = (datetime.date.today() + datetime.timedelta(days=15)).isoformat()
    d2 = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()

    response = client.post(
        "/api/search",
        json={
            "trip_type": "multi_city",
            "legs": [
                {"departure": "DEL", "arrival": "BOM", "outbound_date": d1},
                {"departure": "BOM", "arrival": "BLR", "outbound_date": d2},
            ],
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert "before" in data["error"].lower()


def test_multicity_validation_past_date(client):
    """Multi-city legs cannot have dates in the past."""
    d1 = "2020-01-01"
    d2 = "2020-01-05"

    response = client.post(
        "/api/search",
        json={
            "trip_type": "multi_city",
            "legs": [
                {"departure": "DEL", "arrival": "BOM", "outbound_date": d1},
                {"departure": "BOM", "arrival": "BLR", "outbound_date": d2},
            ],
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert "past" in data["error"].lower()


# =====================================================================
# 4. PASSENGER VALIDATION TESTS
# =====================================================================

def test_passenger_validation_min_adults(client):
    """At least 1 adult is required."""
    future_date = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()
    response = client.post(
        "/api/search",
        json={
            "trip_type": "one_way",
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": future_date,
            "adults": 0,
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert "at least 1 adult" in data["error"].lower()


def test_passenger_validation_infants_exceed_adults(client):
    """Infants in lap cannot exceed the number of adults."""
    future_date = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()
    response = client.post(
        "/api/search",
        json={
            "trip_type": "one_way",
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": future_date,
            "adults": 1,
            "infants": 2,
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert "cannot exceed" in data["error"].lower()


def test_passenger_validation_max_passengers(client):
    """Total passengers cannot exceed 9."""
    future_date = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()
    response = client.post(
        "/api/search",
        json={
            "trip_type": "one_way",
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": future_date,
            "adults": 6,
            "children": 4,
            "infants": 1,
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert "cannot exceed 9" in data["error"].lower()


# =====================================================================
# 5. MAJOR AIRPORT RECORD RESOLUTION TESTS
# =====================================================================

@pytest.mark.parametrize("iata_code", ["SFO", "HND", "JFK", "LHR", "DEL", "BOM", "BLR", "DXB", "SIN"])
def test_major_airport_records_resolve(client, iata_code):
    """Test that major global airports exist and return valid data from /api/airports."""
    response = client.get(f"/api/airports?iata={iata_code}")
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert isinstance(data, list)
    assert len(data) > 0
    assert data[0].get("iata") == iata_code
    assert "city" in data[0]
    assert "name" in data[0]


# =====================================================================
# 6. DIRECT SERVICE UNIT TESTS
# =====================================================================

def test_search_multi_city_flights_service():
    """Directly test search_multi_city_flights service function."""
    legs = [
        {"departure": "DEL", "arrival": "BOM", "outbound_date": "2026-11-10"},
        {"departure": "BOM", "arrival": "DXB", "outbound_date": "2026-11-14"},
    ]
    results = search_multi_city_flights(legs=legs, travel_class=1, adults=2, children=1, infants=0)
    assert len(results) > 0
    for itin in results:
        assert itin["trip_type"] == "multi_city"
        assert len(itin["legs"]) == 2
        assert itin["departure_airport"] == "DEL"
        assert itin["arrival_airport"] == "DXB"
        assert itin["price"] > 0
        assert itin["passengers"] == 3
