"""
Comprehensive Unit Tests for Multi-Website Flight Price Comparison.
Tests price normalization, deduplication, equivalent itinerary grouping,
lowest-price selection, currency handling, provider failures, and API routes.
"""

from services.providers.base import FlightOffer, BaseFlightProvider, ProviderStatus
from services.providers.aggregator import FlightComparisonAggregator
from services.providers.amadeus_provider import AmadeusFlightProvider
from services.providers.direct_airline_provider import DirectAirlineProvider
from services.providers.ota_partner_provider import OtaPartnerProvider
from app import app


# ---------------------------------------------------------------------------
# MOCK PROVIDERS FOR TESTING
# ---------------------------------------------------------------------------

class MockSuccessfulProvider(BaseFlightProvider):
    def __init__(self, provider_id="mock_ota", name="Mock OTA", price=5000, curr="INR"):
        super().__init__(provider_id, name, "ota")
        self.price = price
        self.curr = curr

    def is_configured(self):
        return True

    def get_status(self):
        return ProviderStatus(
            provider_id=self.provider_id,
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status="active",
            is_live=True,
            message="Active mock provider",
            offers_count=1
        )

    def search(self, departure, arrival, outbound_date, return_date=None, travel_class=1, reference_itineraries=None):
        return [
            FlightOffer(
                offer_id="",
                provider_id=self.provider_id,
                provider_name=self.provider_name,
                provider_type=self.provider_type,
                airline="IndiGo",
                flight_number="6E 214",
                departure_airport=departure,
                arrival_airport=arrival,
                departure_time="06:15",
                arrival_time="08:25",
                duration=130,
                stops=0,
                price=self.price,
                currency=self.curr,
                baggage="Cabin: 7 kg included",
                booking_url="https://example.com/book"
            )
        ]


class MockFailingProvider(BaseFlightProvider):
    def __init__(self):
        super().__init__("failing_provider", "Failing Source", "ota")

    def is_configured(self):
        return True

    def get_status(self):
        return ProviderStatus(
            provider_id=self.provider_id,
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status="error",
            is_live=False,
            message="Simulated connection timeout"
        )

    def search(self, departure, arrival, outbound_date, return_date=None, travel_class=1, reference_itineraries=None):
        raise ConnectionError("Upstream booking gateway timeout")


# ---------------------------------------------------------------------------
# TEST SUITE
# ---------------------------------------------------------------------------

def test_price_normalization():
    """Verify numeric normalization and foreign currency conversion."""
    aggregator = FlightComparisonAggregator([])
    
    assert aggregator.normalize_price(4500, "INR") == 4500
    assert aggregator.normalize_price("5200", "INR") == 5200
    assert aggregator.normalize_price("invalid", "INR") == 0
    assert aggregator.normalize_price(None, "INR") == 0

    # Foreign currency conversion (USD to INR at ~86.5)
    usd_inr = aggregator.normalize_price(100, "USD")
    assert 8600 <= usd_inr <= 8700

    # EUR conversion
    eur_inr = aggregator.normalize_price(100, "EUR")
    assert 9200 <= eur_inr <= 9400


def test_duplicate_removal():
    """Verify that duplicate offers from the same provider on the same itinerary are removed."""
    aggregator = FlightComparisonAggregator([])
    offer1 = FlightOffer(
        offer_id="off1",
        provider_id="makemytrip",
        provider_name="MakeMyTrip",
        provider_type="ota",
        airline="IndiGo",
        flight_number="6E 214",
        departure_airport="DEL",
        arrival_airport="BOM",
        departure_time="06:15",
        arrival_time="08:25",
        duration=130,
        stops=0,
        price=4850,
        currency="INR"
    )
    # Identical offer
    offer2 = FlightOffer(
        offer_id="off2",
        provider_id="makemytrip",
        provider_name="MakeMyTrip",
        provider_type="ota",
        airline="IndiGo",
        flight_number="6E 214",
        departure_airport="DEL",
        arrival_airport="BOM",
        departure_time="06:15",
        arrival_time="08:25",
        duration=130,
        stops=0,
        price=4850,
        currency="INR"
    )
    # Different provider
    offer3 = FlightOffer(
        offer_id="off3",
        provider_id="cleartrip",
        provider_name="Cleartrip",
        provider_type="ota",
        airline="IndiGo",
        flight_number="6E 214",
        departure_airport="DEL",
        arrival_airport="BOM",
        departure_time="06:15",
        arrival_time="08:25",
        duration=130,
        stops=0,
        price=4890,
        currency="INR"
    )

    deduped = aggregator.deduplicate_offers([offer1, offer2, offer3])
    assert len(deduped) == 2
    assert {o.provider_id for o in deduped} == {"makemytrip", "cleartrip"}


def test_equivalent_itinerary_grouping_and_best_price_selection():
    """
    Verify equivalent flights group together, the lowest price is tagged with
    is_best_price=True, and price difference relative to lowest is computed.
    """
    offer_cheap = FlightOffer(
        offer_id="1",
        provider_id="easemytrip",
        provider_name="EaseMyTrip",
        provider_type="ota",
        airline="Air India",
        flight_number="AI 804",
        departure_airport="DEL",
        arrival_airport="BOM",
        departure_time="09:30",
        arrival_time="11:35",
        duration=125,
        stops=0,
        price=5200,
        currency="INR"
    )
    offer_mid = FlightOffer(
        offer_id="2",
        provider_id="airline_direct",
        provider_name="Air India Direct",
        provider_type="airline_direct",
        airline="Air India",
        flight_number="AI 804",
        departure_airport="DEL",
        arrival_airport="BOM",
        departure_time="09:30",
        arrival_time="11:35",
        duration=125,
        stops=0,
        price=5400,
        currency="INR"
    )
    offer_high = FlightOffer(
        offer_id="3",
        provider_id="makemytrip",
        provider_name="MakeMyTrip",
        provider_type="ota",
        airline="Air India",
        flight_number="AI 804",
        departure_airport="DEL",
        arrival_airport="BOM",
        departure_time="09:30",
        arrival_time="11:35",
        duration=125,
        stops=0,
        price=5550,
        currency="INR"
    )
    # A completely different flight (different time/flight)
    offer_different = FlightOffer(
        offer_id="4",
        provider_id="airline_direct",
        provider_name="Vistara Direct",
        provider_type="airline_direct",
        airline="Vistara",
        flight_number="UK 945",
        departure_airport="DEL",
        arrival_airport="BOM",
        departure_time="14:00",
        arrival_time="16:15",
        duration=135,
        stops=0,
        price=6190,
        currency="INR"
    )

    aggregator = FlightComparisonAggregator([])
    groups = aggregator.group_itineraries([offer_mid, offer_high, offer_cheap, offer_different])

    assert len(groups) == 2

    # Group 1 (AI 804) should have lowest price 5200
    ai_group = next(g for g in groups if "AI 804" in g.flight_number or "Air India" in g.airline)
    assert ai_group.lowest_price == 5200
    assert ai_group.highest_price == 5550
    assert ai_group.price_spread == 350
    assert ai_group.provider_count == 3
    assert len(ai_group.offers) == 3

    # Lowest price offer must have is_best_price == True and price_diff == 0
    assert ai_group.offers[0]["price"] == 5200
    assert ai_group.offers[0]["is_best_price"] is True
    assert ai_group.offers[0]["price_diff"] == 0

    # Other offers must have is_best_price == False and correct diff
    assert ai_group.offers[1]["price"] == 5400
    assert ai_group.offers[1]["is_best_price"] is False
    assert ai_group.offers[1]["price_diff"] == 200

    assert ai_group.offers[2]["price"] == 5550
    assert ai_group.offers[2]["is_best_price"] is False
    assert ai_group.offers[2]["price_diff"] == 350


def test_provider_failure_resilience():
    """Verify that a failing provider does not crash aggregation and status reports correctly."""
    success_p = MockSuccessfulProvider(price=4700)
    failing_p = MockFailingProvider()

    aggregator = FlightComparisonAggregator([success_p, failing_p])
    comparison = aggregator.search_and_compare(
        departure="DEL",
        arrival="BOM",
        outbound_date="2026-11-20"
    )

    # Should still succeed with offers from the working provider
    assert len(comparison["grouped_itineraries"]) == 1
    assert comparison["stats"]["total_offers"] == 1
    assert comparison["stats"]["lowest_price"] == 4700

    # Summary should report failing provider status
    summary = {p["provider_id"]: p["status"] for p in comparison["providers_summary"]}
    assert summary["mock_ota"] == "active"
    assert summary["failing_provider"] == "error"


def test_unconfigured_amadeus_provider(monkeypatch):
    """Verify that Amadeus provider reports unconfigured without crashing when keys are absent."""
    monkeypatch.delenv("AMADEUS_CLIENT_ID", raising=False)
    monkeypatch.delenv("AMADEUS_CLIENT_SECRET", raising=False)

    amadeus = AmadeusFlightProvider()
    assert amadeus.is_configured() is False
    status = amadeus.get_status()
    assert status.status == "unconfigured"
    assert status.is_live is False
    assert "AMADEUS_CLIENT_ID" in status.message

    offers = amadeus.search("DEL", "BOM", "2026-11-20")
    assert offers == []


def test_direct_airline_and_ota_adapters():
    """Verify DirectAirlineProvider and OtaPartnerProvider generate comparable offers."""
    sample_flights = [
        {
            "airline": "IndiGo",
            "price": 4850,
            "currency": "INR",
            "duration": 130,
            "stops": 0,
            "departure_time": "06:15",
            "arrival_time": "08:25",
            "departure_airport": "DEL",
            "arrival_airport": "BOM",
            "segments": [{"flight_number": "6E 214", "airline": "IndiGo"}]
        }
    ]

    direct_provider = DirectAirlineProvider()
    direct_offers = direct_provider.search("DEL", "BOM", "2026-11-20", reference_itineraries=sample_flights)
    assert len(direct_offers) == 1
    assert direct_offers[0].provider_type == "airline_direct"
    assert "goindigo.in" in direct_offers[0].booking_url
    assert direct_offers[0].price == 4850

    ota_provider = OtaPartnerProvider()
    ota_offers = ota_provider.search("DEL", "BOM", "2026-11-20", reference_itineraries=sample_flights)
    assert len(ota_offers) == 3  # MakeMyTrip, Cleartrip, EaseMyTrip
    providers = {o.provider_id for o in ota_offers}
    assert providers == {"makemytrip", "cleartrip", "easemytrip"}
    # EaseMyTrip should be cheaper due to zero convenience fee
    emt_offer = next(o for o in ota_offers if o.provider_id == "easemytrip")
    assert emt_offer.price < 4850


def test_api_providers_endpoint():
    """Verify GET /api/providers returns JSON with status of configured providers."""
    client = app.test_client()
    res = client.get("/api/providers")
    assert res.status_code == 200
    data = res.get_json()
    assert "providers" in data
    assert isinstance(data["providers"], list)
    assert len(data["providers"]) >= 3
    for p in data["providers"]:
        assert "provider_id" in p
        assert "provider_name" in p
        assert "status" in p


def test_api_search_includes_multi_provider_comparison():
    """Verify POST /api/search returns comparison payload with grouped itineraries."""
    client = app.test_client()
    payload = {
        "departure": "DEL",
        "arrival": "BOM",
        "outbound_date": "2026-11-20",
        "travel_class": 1
    }
    res = client.post("/api/search", json=payload)
    assert res.status_code == 200
    data = res.get_json()

    # Original contracts preserved
    assert "results" in data
    assert "search_airports" in data
    assert "airport_comparisons" in data

    # New comparison feature present
    assert "comparison" in data
    comp = data["comparison"]
    assert "grouped_itineraries" in comp
    assert "all_offers" in comp
    assert "providers_summary" in comp
    assert "stats" in comp

    assert comp["stats"]["total_offers"] > 0
    assert comp["stats"]["total_itineraries"] > 0
    assert len(comp["grouped_itineraries"]) > 0

    # Check that each group has offers with best price tagged
    for group in comp["grouped_itineraries"]:
        assert len(group["offers"]) > 0
        best_offers = [o for o in group["offers"] if o["is_best_price"]]
        assert len(best_offers) >= 1
        assert best_offers[0]["price"] == group["lowest_price"]
