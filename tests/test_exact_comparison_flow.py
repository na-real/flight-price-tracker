"""
Tests for Exact Flight Comparison and Airport Swap flow contracts.
"""

import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_search_flight_itinerary_identity_and_offers(client):
    """
    Priority 2: Verify that every flight returned has a unique, stable identity
    (itinerary_id and flight_number) and pre-attached comparison offers.
    """
    res = client.post("/api/search", json={
        "departure": "DEL",
        "arrival": "MAA",
        "outbound_date": "2026-11-25",
        "travel_class": 1
    })
    assert res.status_code == 200
    data = res.get_json()
    assert "results" in data
    flights = data["results"]
    assert len(flights) > 0

    for flight in flights:
        assert "itinerary_id" in flight
        assert flight["itinerary_id"]
        assert "flight_number" in flight
        assert flight["flight_number"]
        assert "comparison_offers" in flight
        assert isinstance(flight["comparison_offers"], list)
        assert len(flight["comparison_offers"]) >= 3

        # Verify offers genuinely correspond to this flight's identity
        for offer in flight["comparison_offers"]:
            assert "provider_name" in offer
            assert "price" in offer
            assert offer["price"] > 0
            assert "booking_url" in offer
            assert offer["booking_url"].startswith("http")
            # Verify provider URL belongs to authentic provider domains
            valid_domains = [
                "makemytrip.com",
                "cleartrip.com",
                "easemytrip.com",
                "expedia.co.in",
                "goindigo.in",
                "airindia.com",
                "airvistara.com",
                "akasaair.com",
                "spicejet.com",
                "google.com"
            ]
            assert any(domain in offer["booking_url"] for domain in valid_domains)


def test_swapped_airports_search_execution(client):
    """
    Priority 1: Verify search after airport swap (MAA -> DEL) returns itineraries
    for Chennai to Delhi, preserving reversed airport codes throughout backend.
    """
    res = client.post("/api/search", json={
        "departure": "MAA",
        "arrival": "DEL",
        "outbound_date": "2026-11-25",
        "travel_class": 1
    })
    assert res.status_code == 200
    data = res.get_json()
    flights = data["results"]
    assert len(flights) > 0

    # Ensure search was indeed from MAA to DEL
    for flight in flights:
        assert flight["departure_airport"] == "MAA"
        assert flight["arrival_airport"] == "DEL"


def test_dedicated_flight_compare_endpoint(client):
    """
    Priority 3 & 4: Dedicated exact flight compare endpoint returns
    precise itinerary comparison with best price highlighting and valid URLs.
    """
    sample_flight = {
        "airline": "Air India",
        "flight_number": "AI 804",
        "departure_airport": "DEL",
        "arrival_airport": "MAA",
        "departure_time": "09:05",
        "arrival_time": "11:55",
        "duration": 170,
        "stops": 0,
        "price": 7500,
        "currency": "INR"
    }

    res = client.post("/api/flight/compare", json={
        "flight": sample_flight,
        "departure_date": "2026-11-25",
        "passengers": 1
    })
    assert res.status_code == 200
    data = res.get_json()

    assert data["flight_summary"]["airline"] == "Air India"
    assert data["flight_summary"]["flight_number"] == "AI 804"
    assert data["flight_summary"]["departure_time"] == "09:05"
    assert data["flight_summary"]["departure_airport"] == "DEL"
    assert data["flight_summary"]["arrival_airport"] == "MAA"

    offers = data["comparison_offers"]
    assert len(offers) >= 4

    # Verify best price flag
    best_offers = [o for o in offers if o["is_best_price"]]
    assert len(best_offers) >= 1
    min_price = min(o["price"] for o in offers)
    for b in best_offers:
        assert b["price"] == min_price

    # Verify provider URLs
    for o in offers:
        assert o["booking_url"].startswith("http")


def test_standalone_route_comparison_request(client):
    """
    FIX 4 Regression: Verify standalone /compare form request (route-based without
    reference flight) generates multi-provider comparison offers and valid schema.
    """
    res = client.post("/api/flight/compare", json={
        "departure": "DEL",
        "arrival": "BOM",
        "outbound_date": "2026-11-25"
    })
    assert res.status_code == 200
    data = res.get_json()

    assert "offers" in data
    assert "comparison_offers" in data
    assert len(data["offers"]) > 0

    # Ensure provider and provider_name are present for frontend template
    for offer in data["offers"]:
        assert "provider" in offer
        assert "provider_name" in offer
        assert "price" in offer
        assert offer["price"] > 0
        assert "booking_url" in offer
        assert offer["booking_url"].startswith("http")

    assert "stats" in data
    assert data["stats"]["total_offers"] >= len(data["offers"])

    # Test error cases:
    # 1. Missing fields
    res_missing = client.post("/api/flight/compare", json={
        "departure": "DEL"
    })
    assert res_missing.status_code == 400

    # 2. Identical airports
    res_same = client.post("/api/flight/compare", json={
        "departure": "DEL",
        "arrival": "DEL",
        "outbound_date": "2026-11-25"
    })
    assert res_same.status_code == 400
    assert "identical" in res_same.get_json()["error"].lower()

    # 3. Invalid airport code
    res_invalid_iata = client.post("/api/flight/compare", json={
        "departure": "INVALID",
        "arrival": "BOM",
        "outbound_date": "2026-11-25"
    })
    assert res_invalid_iata.status_code == 400
