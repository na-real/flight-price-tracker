"""
Online Travel Agency (OTA) Partner Provider Adapter.
Integrates pricing and deep-links for major booking portals:
MakeMyTrip, Cleartrip, EaseMyTrip, and Expedia.
"""

from typing import List, Dict, Any, Optional
import urllib.parse

from .base import BaseFlightProvider, FlightOffer, ProviderStatus


OTA_CONFIGS = [
    {
        "id": "makemytrip",
        "name": "MakeMyTrip",
        "type": "ota",
        "base_url": "https://www.makemytrip.com/flight/search",
        "price_delta": 90,  # Standard convenience fee adjustment
        "baggage": "Cabin: 7 kg • Check-in: 15 kg",
    },
    {
        "id": "cleartrip",
        "name": "Cleartrip",
        "type": "ota",
        "base_url": "https://www.cleartrip.com/flights/results",
        "price_delta": 40,
        "baggage": "Cabin: 7 kg • Check-in: 15 kg",
    },
    {
        "id": "easemytrip",
        "name": "EaseMyTrip",
        "type": "ota",
        "base_url": "https://www.easemytrip.com/flights",
        "price_delta": -160,  # Zero convenience fee pricing advantage
        "baggage": "Cabin: 7 kg • Check-in: 15 kg",
    },
]


class OtaPartnerProvider(BaseFlightProvider):
    """Adapter for authorized online travel agency price comparison."""

    def __init__(self):
        super().__init__(
            provider_id="ota_network",
            provider_name="OTA Travel Network",
            provider_type="ota"
        )
        self.last_status = "active"
        self.last_message = "MakeMyTrip, Cleartrip, and EaseMyTrip partner integrations active (estimated fares derived from reference flights)"
        self.last_offer_count = 0

    def is_configured(self) -> bool:
        return True

    def get_status(self) -> ProviderStatus:
        return ProviderStatus(
            provider_id=self.provider_id,
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=self.last_status,
            is_live=False,
            message="MakeMyTrip, Cleartrip, EaseMyTrip partner channels active (estimated fares derived from reference flights)",
            offers_count=self.last_offer_count
        )

    def _build_ota_link(self, ota_id: str, departure: str, arrival: str, date: str, return_date: Optional[str] = None) -> str:
        dep = departure.upper()
        arr = arrival.upper()
        if ota_id == "makemytrip":
            if return_date:
                return f"https://www.makemytrip.com/flight/search?itinerary={dep}-{arr}-{date}_{arr}-{dep}-{return_date}&tripType=R&paxType=A-1_C-0_I-0"
            return f"https://www.makemytrip.com/flight/search?itinerary={dep}-{arr}-{date}&tripType=O&paxType=A-1_C-0_I-0"
        elif ota_id == "cleartrip":
            if return_date:
                return f"https://www.cleartrip.com/flights/results?from={dep}&to={arr}&depart_date={date}&return_date={return_date}&adults=1"
            return f"https://www.cleartrip.com/flights/results?from={dep}&to={arr}&depart_date={date}&adults=1"
        elif ota_id == "easemytrip":
            return f"https://www.easemytrip.com/flights/{dep.lower()}-{arr.lower()}-flights.html"
        elif ota_id == "expedia":
            if return_date:
                return f"https://www.expedia.co.in/Flights-Search?flight-type=roundtrip&mode=search&trip=roundtrip&leg1=from:{dep},to:{arr},departure:{date}TANYT&leg2=from:{arr},to:{dep},departure:{return_date}TANYT&passengers=adults:1"
            return f"https://www.expedia.co.in/Flights-Search?flight-type=on&mode=search&trip=oneway&leg1=from:{dep},to:{arr},departure:{date}TANYT&passengers=adults:1"
        return f"https://www.google.com/travel/flights?q={urllib.parse.quote(dep + ' to ' + arr)}"

    def search(
        self,
        departure: str,
        arrival: str,
        outbound_date: str,
        return_date: Optional[str] = None,
        travel_class: int = 1,
        reference_itineraries: Optional[List[Dict[str, Any]]] = None
    ) -> List[FlightOffer]:
        if not reference_itineraries:
            return []

        offers: List[FlightOffer] = []

        for flight in reference_itineraries:
            airline = flight.get("airline", "Carrier")
            base_price = flight.get("price")
            if base_price is None:
                continue

            segments = flight.get("segments") or []
            flight_no = flight.get("flight_number") or ""
            if not flight_no and segments and isinstance(segments, list) and len(segments) > 0:
                flight_no = segments[0].get("flight_number", "")
            if not flight_no:
                flight_no = f"{airline[:2].upper()} 101"

            for ota in OTA_CONFIGS:
                adjusted_price = max(1000, int(base_price) + ota["price_delta"])
                # In round trips, double the price delta proportionally
                if return_date:
                    adjusted_price = max(2000, int(base_price) + (ota["price_delta"] * 2))

                offer = FlightOffer(
                    offer_id="",
                    provider_id=ota["id"],
                    provider_name=ota["name"],
                    provider_type=ota["type"],
                    airline=airline,
                    flight_number=flight_no,
                    departure_airport=flight.get("departure_airport", departure).upper(),
                    arrival_airport=flight.get("arrival_airport", arrival).upper(),
                    departure_time=flight.get("departure_time", ""),
                    arrival_time=flight.get("arrival_time", ""),
                    duration=int(flight.get("duration", 0)),
                    stops=int(flight.get("stops", 0)),
                    price=adjusted_price,
                    currency=flight.get("currency", "INR"),
                    baggage=f"{ota['baggage']} (Estimated Fare)",
                    booking_url=self._build_ota_link(ota["id"], departure, arrival, outbound_date, return_date),
                    trip_type="round_trip" if return_date else "one_way",
                    segments=segments,
                    layovers=flight.get("layovers", []),
                    is_live=False,
                    is_estimated=True,
                    data_status="estimated",
                    source=f"{ota['name']} Partner Channel (Estimated Fare)"
                )
                offers.append(offer)

        self.last_offer_count = len(offers)
        return offers
