import pytest
import json
from app import app
from db import init_db
from services.providers.base import FlightOffer
from services.providers.flightapi_provider import FlightApiProvider
from services.flight_api import deduplicate_flight_results, search_flights


@pytest.fixture
def client():
    app.config["TESTING"] = True
    init_db()
    with app.test_client() as client:
        yield client


def test_1_identical_provider_itineraries_deduplicated():
    """1. Identical provider itineraries are not rendered multiple times."""
    raw_results = [
        {
            "departure_airport": "DEL",
            "arrival_airport": "MAA",
            "airline": "IndiGo",
            "flight_number": "6E 214",
            "departure_time": "06:15",
            "arrival_time": "09:05",
            "price": 9944,
            "currency": "INR",
            "stops": 0,
            "trip_type": "one_way",
            "itinerary_id": "itin_1",
            "comparison_offers": [{"vendor": "IndiGo Direct", "price": 9944}]
        },
        {
            "departure_airport": "DEL",
            "arrival_airport": "MAA",
            "airline": "IndiGo",
            "flight_number": "6E 214",
            "departure_time": "06:15",
            "arrival_time": "09:05",
            "price": 9944,
            "currency": "INR",
            "stops": 0,
            "trip_type": "one_way",
            "itinerary_id": "itin_1_dup",
            "comparison_offers": [{"vendor": "MakeMyTrip", "price": 10050}]
        }
    ]

    deduped = deduplicate_flight_results(raw_results)
    assert len(deduped) == 1
    flight = deduped[0]
    assert flight["flight_number"] == "6E 214"
    assert flight["price"] == 9944
    # Both vendor options must be preserved in comparison_offers
    vendors = [o.get("vendor") or o.get("agent_name") for o in flight.get("comparison_offers", [])]
    assert "IndiGo Direct" in vendors
    assert "MakeMyTrip" in vendors


def test_2_different_flight_numbers_or_departure_times_remain_separate():
    """2. Different flight numbers or departure times remain separate results."""
    # This precisely models the screenshot: DEL -> MAA at ₹9,944 on IndiGo, but distinct flights
    screen_results = [
        {
            "departure_airport": "DEL",
            "arrival_airport": "MAA",
            "airline": "IndiGo",
            "flight_number": "6E 214",
            "departure_time": "06:15",
            "arrival_time": "09:05",
            "price": 9944,
            "stops": 0,
            "trip_type": "one_way"
        },
        {
            "departure_airport": "DEL",
            "arrival_airport": "MAA",
            "airline": "IndiGo",
            "flight_number": "6E 5025",
            "departure_time": "10:20",
            "arrival_time": "13:10",
            "price": 9944,
            "stops": 0,
            "trip_type": "one_way"
        },
        {
            "departure_airport": "DEL",
            "arrival_airport": "MAA",
            "airline": "IndiGo",
            "flight_number": "6E 6814",
            "departure_time": "14:45",
            "arrival_time": "17:35",
            "price": 9944,
            "stops": 0,
            "trip_type": "one_way"
        }
    ]

    deduped = deduplicate_flight_results(screen_results)
    # Must preserve all 3 legitimate distinct flight schedules
    assert len(deduped) == 3
    flight_nos = [f["flight_number"] for f in deduped]
    assert "6E 214" in flight_nos
    assert "6E 5025" in flight_nos
    assert "6E 6814" in flight_nos


def test_3_vendor_offers_for_one_itinerary_preserved_for_comparison():
    """3. Multiple vendor pricing options for one itinerary are aggregated into comparison_offers."""
    provider = FlightApiProvider()
    mock_flightapi_response = {
        "itineraries": [
            {
                "id": "itin_del_maa_001",
                "legs": [
                    {
                        "id": "leg_1",
                        "departure": "DEL",
                        "arrival": "MAA",
                        "departure_time": "2026-11-15T06:15:00",
                        "arrival_time": "2026-11-15T09:05:00",
                        "duration": 170,
                        "stops": 0,
                        "airline_code": "6E",
                        "airline_name": "IndiGo",
                        "flight_number": "6E 214",
                        "segments": []
                    }
                ],
                "pricing_options": [
                    {
                        "agent_name": "IndiGo",
                        "price": {"amount": 9944, "currency": "INR"},
                        "url": "https://goindigo.in"
                    },
                    {
                        "agent_name": "Cleartrip",
                        "price": {"amount": 9999, "currency": "INR"},
                        "url": "https://cleartrip.com"
                    },
                    {
                        "agent_name": "MakeMyTrip",
                        "price": {"amount": 10100, "currency": "INR"},
                        "url": "https://makemytrip.com"
                    }
                ]
            }
        ],
        "legs": [],
        "airlines": []
    }

    offers = provider._normalize_response(mock_flightapi_response, "DEL", "MAA", "2026-11-15")
    # Must normalize to 1 itinerary offer with the lowest price
    assert len(offers) == 1
    offer = offers[0]
    assert offer.price == 9944
    assert offer.flight_number == "6E 214"
    assert offer.offer_id == "itin_del_maa_001"
    # All 3 vendor pricing options must be preserved in comparison_offers
    assert len(offer.comparison_offers) == 3
    agents = [co["agent_name"] for co in offer.comparison_offers]
    assert "IndiGo" in agents
    assert "Cleartrip" in agents
    assert "MakeMyTrip" in agents


def test_4_cached_results_labeled_as_cached(client, monkeypatch):
    """4. Cached results are accurately identified as cached with observation timestamp."""
    mock_cached_flight = [{
        "departure_airport": "DEL",
        "arrival_airport": "BOM",
        "airline": "Vistara",
        "flight_number": "UK 995",
        "departure_time": "07:00",
        "arrival_time": "09:10",
        "price": 6500,
        "currency": "INR",
        "stops": 0,
        "trip_type": "one_way",
        "is_live": False,
        "is_cached": True,
        "data_status": "cached",
        "cached_at": "2026-10-16T18:00:00Z",
        "age_seconds": 600,
        "source": "Cache"
    }]

    monkeypatch.setattr("app.search_flights", lambda **kwargs: mock_cached_flight)

    res = client.post("/api/search", json={
        "departure": "DEL",
        "arrival": "BOM",
        "outbound_date": "2026-11-20",
        "trip_type": "one_way"
    })
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["results"]) == 1
    f = data["results"][0]
    assert f["is_cached"] is True
    assert f["data_status"] == "cached"
    assert f["age_seconds"] == 600


def test_5_simulated_results_labeled_demonstration(client, monkeypatch):
    """5. Simulated / benchmark results are labeled demonstration data."""
    # When all API keys are unset, search_flights falls back to demonstration data
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.delenv("FLIGHTAPI_API_KEY", raising=False)
    monkeypatch.delenv("AMADEUS_CLIENT_ID", raising=False)

    res = client.post("/api/search", json={
        "departure": "DEL",
        "arrival": "BLR",
        "outbound_date": "2026-11-25",
        "trip_type": "one_way"
    })
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["results"]) > 0
    for f in data["results"]:
        assert f.get("is_simulated") is True
        assert f.get("data_status") == "demonstration"


def test_6_malformed_provider_response_never_fabricates_fares():
    """6. Malformed or empty provider responses never produce fabricated fares or arbitrary defaults."""
    provider = FlightApiProvider()

    # Empty payload
    empty_offers = provider._normalize_response({}, "DEL", "MAA", "2026-11-15")
    assert empty_offers == []

    # Corrupt itineraries without price
    corrupt_offers = provider._normalize_response({
        "itineraries": [
            {
                "id": "corrupt_1",
                "legs": [{"departure": "DEL", "arrival": "MAA"}],
                "pricing_options": []
            }
        ]
    }, "DEL", "MAA", "2026-11-15")
    assert corrupt_offers == []


def test_7_currency_and_fare_normalization():
    """7. Currency and fare totals remain correct (normalized integer INR, no arbitrary string sorting)."""
    provider = FlightApiProvider()
    usd_response = {
        "itineraries": [
            {
                "id": "itin_intl",
                "legs": [
                    {
                        "departure": "DEL",
                        "arrival": "LHR",
                        "departure_time": "2026-11-15T02:00:00",
                        "arrival_time": "2026-11-15T06:30:00",
                        "duration": 540,
                        "stops": 0,
                        "airline_code": "BA",
                        "airline_name": "British Airways",
                        "flight_number": "BA 142",
                        "segments": []
                    }
                ],
                "pricing_options": [
                    {
                        "agent_name": "British Airways",
                        "price": {"amount": 500.0, "currency": "USD"},
                        "url": "https://ba.com"
                    }
                ]
            }
        ]
    }

    offers = provider._normalize_response(usd_response, "DEL", "LHR", "2026-11-15")
    assert len(offers) == 1
    offer = offers[0]
    # USD 500 at rate ~84.5 => ~42,250 INR
    assert offer.currency == "INR"
    assert offer.price > 40000
    assert isinstance(offer.price, int)


def test_8_compare_and_track_reference_intended_itinerary(client, monkeypatch):
    """8. Compare and Track actions reference the intended flight itinerary."""
    target_flight = {
        "departure_airport": "DEL",
        "arrival_airport": "MAA",
        "airline": "IndiGo",
        "flight_number": "6E 5025",
        "departure_time": "10:20",
        "arrival_time": "13:10",
        "departure_date": "2026-11-18",
        "price": 9944,
        "currency": "INR",
        "stops": 0,
        "trip_type": "one_way",
        "itinerary_id": "itin_6e_5025",
        "comparison_offers": [
            {"provider": "IndiGo Direct", "provider_name": "IndiGo Direct", "price": 9944, "vendor": "IndiGo Direct"},
            {"provider": "Cleartrip", "provider_name": "Cleartrip", "price": 9999, "vendor": "Cleartrip"}
        ]
    }

    # Test /api/flight/compare passes through authentic comparison offers
    res = client.post("/api/flight/compare", json={"flight": target_flight})
    assert res.status_code == 200
    comp_data = res.get_json()
    assert comp_data["flight_summary"]["flight_number"] == "6E 5025"
    offers = comp_data["offers"]
    # Verified vendor offers must be present
    providers = [o.get("provider") or o.get("provider_name") for o in offers]
    assert "IndiGo Direct" in providers
    assert "Cleartrip" in providers

    # Test /api/track accurately records tracking for the specific target flight's route and target price
    track_res = client.post("/api/track", json={
        "departure": target_flight["departure_airport"],
        "arrival": target_flight["arrival_airport"],
        "outbound_date": target_flight["departure_date"],
        "target_price": 9000,
        "email": "flight_track_user@example.com"
    })
    assert track_res.status_code == 200
    track_data = track_res.get_json()
    assert track_data.get("id") is not None
    assert "tracking started" in track_data.get("message", "").lower()
