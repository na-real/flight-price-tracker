"""
SerpAPI Google Flights Provider Adapter.
Extracts live flights and booking partner offers from Google Flights via SerpAPI.
"""

import os
import urllib.parse
from typing import List, Dict, Any, Optional
import requests

from .base import BaseFlightProvider, FlightOffer, ProviderStatus


SERPAPI_URL = "https://serpapi.com/search.json"


class SerpApiFlightsProvider(BaseFlightProvider):
    """Integrates with Google Flights via SerpAPI."""

    def __init__(self):
        super().__init__(
            provider_id="google_flights",
            provider_name="Google Flights",
            provider_type="metasearch"
        )
        self.last_status = "active"
        self.last_message = "Ready to search Google Flights via SerpAPI"
        self.last_offer_count = 0

    def is_configured(self) -> bool:
        return bool(os.getenv("SERPAPI_KEY"))

    def get_status(self) -> ProviderStatus:
        if not self.is_configured():
            return ProviderStatus(
                provider_id=self.provider_id,
                provider_name=self.provider_name,
                provider_type=self.provider_type,
                status="unconfigured",
                is_live=False,
                message="SERPAPI_KEY is not configured in .env",
                offers_count=self.last_offer_count,
            )

        return ProviderStatus(
            provider_id=self.provider_id,
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=self.last_status,
            is_live=(self.last_status == "active"),
            message=self.last_message,
            offers_count=self.last_offer_count,
        )

    def _build_deep_link(self, departure: str, arrival: str, outbound_date: str) -> str:
        q = f"Flights to {arrival.upper()} from {departure.upper()} on {outbound_date}"
        return f"https://www.google.com/travel/flights?q={urllib.parse.quote(q)}"

    def search(
        self,
        departure: str,
        arrival: str,
        outbound_date: str,
        return_date: Optional[str] = None,
        travel_class: int = 1,
        reference_itineraries: Optional[List[Dict[str, Any]]] = None
    ) -> List[FlightOffer]:
        if not self.is_configured():
            self.last_status = "unconfigured"
            self.last_message = "SERPAPI_KEY not configured"
            return []

        api_key = os.getenv("SERPAPI_KEY")
        params = {
            "engine": "google_flights",
            "api_key": api_key,
            "departure_id": departure.upper(),
            "arrival_id": arrival.upper(),
            "outbound_date": outbound_date,
            "travel_class": travel_class,
            "currency": "INR",
            "hl": "en",
            "gl": "in",
            "show_hidden": "true"
        }
        if return_date:
            params["return_date"] = return_date
            params["type"] = "1"
        else:
            params["type"] = "2"

        offers: List[FlightOffer] = []

        try:
            response = requests.get(SERPAPI_URL, params=params, timeout=20)
            data = response.json()

            if response.status_code == 429 or "search limit reached" in str(data.get("error", "")).lower():
                self.last_status = "rate_limited"
                self.last_message = "SerpAPI monthly search limit reached. Fallback providers active."
                return []

            if data.get("error"):
                self.last_status = "error"
                self.last_message = f"SerpAPI returned error: {data.get('error')}"
                return []

            # Process best and other flights
            raw_itineraries = []
            raw_itineraries.extend(data.get("best_flights", []) or [])
            raw_itineraries.extend(data.get("other_flights", []) or [])

            for item in raw_itineraries:
                flights = item.get("flights", []) or []
                if not flights:
                    continue

                first = flights[0]
                last = flights[-1]
                airline = first.get("airline", "Unknown Airline")
                flight_no = first.get("flight_number", "")
                dep_time = (first.get("departure_airport", {}).get("time", "")).split(" ")[-1]
                arr_time = (last.get("arrival_airport", {}).get("time", "")).split(" ")[-1]
                duration = item.get("total_duration") or sum(f.get("duration", 0) for f in flights)
                price = item.get("price")
                stops = max(len(flights) - 1, 0)

                if price is not None:
                    # Google Flights base offer
                    base_offer = FlightOffer(
                        offer_id="",
                        provider_id=self.provider_id,
                        provider_name="Google Flights",
                        provider_type=self.provider_type,
                        airline=airline,
                        flight_number=flight_no,
                        departure_airport=departure.upper(),
                        arrival_airport=arrival.upper(),
                        departure_time=dep_time,
                        arrival_time=arr_time,
                        duration=int(duration),
                        stops=stops,
                        price=int(price),
                        currency="INR",
                        baggage="Cabin: 7 kg included",
                        booking_url=self._build_deep_link(departure, arrival, outbound_date),
                        trip_type="round_trip" if return_date else "one_way"
                    )
                    offers.append(base_offer)

                # Check if SerpAPI returned nested booking options
                booking_options = item.get("booking_options") or []
                for b_opt in booking_options:
                    b_partner = b_opt.get("book_with", "Partner")
                    b_price = b_opt.get("price")
                    b_url = b_opt.get("link", "")
                    if b_price:
                        partner_offer = FlightOffer(
                            offer_id="",
                            provider_id=f"serp_{b_partner.lower().replace(' ', '_')}",
                            provider_name=b_partner,
                            provider_type="ota",
                            airline=airline,
                            flight_number=flight_no,
                            departure_airport=departure.upper(),
                            arrival_airport=arrival.upper(),
                            departure_time=dep_time,
                            arrival_time=arr_time,
                            duration=int(duration),
                            stops=stops,
                            price=int(b_price),
                            currency="INR",
                            baggage="Cabin: 7 kg included",
                            booking_url=b_url or self._build_deep_link(departure, arrival, outbound_date),
                            trip_type="round_trip" if return_date else "one_way"
                        )
                        offers.append(partner_offer)

            self.last_status = "active"
            self.last_message = f"Retrieved {len(offers)} offers from Google Flights"
            self.last_offer_count = len(offers)

        except requests.RequestException as exc:
            self.last_status = "rate_limited" if "429" in str(exc) else "error"
            self.last_message = f"Connection error: {exc}"

        return offers
