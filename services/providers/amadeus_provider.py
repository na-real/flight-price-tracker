"""
Amadeus Flight Offers Provider Adapter.
Integrates with Amadeus Self-Service API (GDS flight distribution).
"""

import os
from typing import List, Dict, Any, Optional
import requests

from .base import BaseFlightProvider, FlightOffer, ProviderStatus
from services.api_guardian import (
    guardian,
    PROVIDER_AMADEUS,
    CALLER_USER_SEARCH,
    get_guardian_config,
)


AMADEUS_AUTH_URL = "https://test.api.amadeus.com/v1/security/oauth2/token"
AMADEUS_FLIGHTS_URL = "https://test.api.amadeus.com/v2/shopping/flight-offers"


class AmadeusFlightProvider(BaseFlightProvider):
    """Adapter for Amadeus Global Distribution System API."""

    def __init__(self):
        super().__init__(
            provider_id="amadeus",
            provider_name="Amadeus GDS",
            provider_type="gds"
        )
        self.access_token: Optional[str] = None
        self.last_status = "unconfigured"
        self.last_message = "Requires AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET in .env"
        self.last_offer_count = 0

    def is_configured(self) -> bool:
        client_id = os.getenv("AMADEUS_CLIENT_ID")
        client_secret = os.getenv("AMADEUS_CLIENT_SECRET")
        return bool(client_id and client_secret)

    def get_status(self) -> ProviderStatus:
        if not self.is_configured():
            return ProviderStatus(
                provider_id=self.provider_id,
                provider_name=self.provider_name,
                provider_type=self.provider_type,
                status="unconfigured",
                is_live=False,
                message="Requires AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET in environment",
                offers_count=0
            )

        return ProviderStatus(
            provider_id=self.provider_id,
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=self.last_status,
            is_live=(self.last_status == "active"),
            message=self.last_message,
            offers_count=self.last_offer_count
        )

    def _authenticate(self) -> bool:
        client_id = os.getenv("AMADEUS_CLIENT_ID")
        client_secret = os.getenv("AMADEUS_CLIENT_SECRET")
        if not client_id or not client_secret:
            return False

        try:
            res = requests.post(
                AMADEUS_AUTH_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret
                },
                timeout=10
            )
            if res.status_code == 200:
                self.access_token = res.json().get("access_token")
                return True
            else:
                self.last_status = "error"
                self.last_message = f"Amadeus auth failed (HTTP {res.status_code})"
                return False
        except Exception as e:
            self.last_status = "error"
            self.last_message = f"Amadeus auth error: {e}"
            return False

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
            self.last_message = "Requires AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET"
            return []

        if not self.access_token and not self._authenticate():
            return []

        cabin_map = {1: "ECONOMY", 2: "PREMIUM_ECONOMY", 3: "BUSINESS", 4: "FIRST"}
        params = {
            "originLocationCode": departure.upper(),
            "destinationLocationCode": arrival.upper(),
            "departureDate": outbound_date,
            "adults": 1,
            "currencyCode": "INR",
            "travelClass": cabin_map.get(travel_class, "ECONOMY"),
            "max": 10
        }
        if return_date:
            params["returnDate"] = return_date

        offers: List[FlightOffer] = []
        try:
            def _fetch(p):
                cfg = get_guardian_config()
                timeout = cfg.get("amadeus_timeout", 15)
                headers = {"Authorization": f"Bearer {self.access_token}"}
                res = requests.get(AMADEUS_FLIGHTS_URL, headers=headers, params=p, timeout=timeout)
                if res.status_code == 401:
                    # Token expired, re-auth once
                    if self._authenticate():
                        headers = {"Authorization": f"Bearer {self.access_token}"}
                        res = requests.get(AMADEUS_FLIGHTS_URL, headers=headers, params=p, timeout=timeout)
                if res.status_code == 429:
                    err = requests.exceptions.HTTPError("Amadeus rate limit reached (HTTP 429)", response=res)
                    setattr(err, "status_code", 429)
                    raise err
                if res.status_code >= 400:
                    err = requests.exceptions.HTTPError(f"Amadeus flight search returned HTTP {res.status_code}", response=res)
                    setattr(err, "status_code", res.status_code)
                    raise err
                return res.json()

            guardian_resp = guardian.execute_request(
                provider_id=self.provider_id,
                endpoint="flight-offers",
                params=params,
                fetch_fn=_fetch,
                caller_type=CALLER_USER_SEARCH
            )

            if guardian_resp.status in ("blocked_budget", "circuit_open"):
                self.last_status = "rate_limited"
                self.last_message = guardian_resp.error or "Amadeus provider temporarily blocked by API Guardian"
                return []

            if guardian_resp.status == "error" or not guardian_resp.data:
                self.last_status = "error"
                self.last_message = guardian_resp.error or "Amadeus query failed"
                return []

            data = guardian_resp.data
            is_live_flag = not guardian_resp.is_cached
            data_status_flag = "cached" if guardian_resp.is_cached else "live"
            source_suffix = " (Shared Cache)" if guardian_resp.is_cached else ""

            raw_offers = data.get("data", [])
            for item in raw_offers:
                itineraries = item.get("itineraries", [])
                if not itineraries:
                    continue
                first_itinerary = itineraries[0]
                segments = first_itinerary.get("segments", [])
                if not segments:
                    continue

                first_seg = segments[0]
                last_seg = segments[-1]
                airline = first_seg.get("carrierCode", "Carrier")
                flight_no = f"{airline} {first_seg.get('number', '')}"
                dep_time = first_seg.get("departure", {}).get("at", "")[-8:-3]
                arr_time = last_seg.get("arrival", {}).get("at", "")[-8:-3]
                stops = max(len(segments) - 1, 0)
                price_total = float(item.get("price", {}).get("total", 0))

                offer = FlightOffer(
                    offer_id="",
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    provider_type=self.provider_type,
                    airline=airline,
                    flight_number=flight_no,
                    departure_airport=departure.upper(),
                    arrival_airport=arrival.upper(),
                    departure_time=dep_time,
                    arrival_time=arr_time,
                    duration=120,
                    stops=stops,
                    price=int(price_total),
                    currency="INR",
                    baggage="Standard allowance included",
                    booking_url="https://amadeus.com",
                    trip_type="round_trip" if return_date else "one_way",
                    is_live=is_live_flag,
                    is_estimated=False,
                    data_status=data_status_flag,
                    source=f"Amadeus GDS{source_suffix}"
                )
                offers.append(offer)

            self.last_status = "active"
            cached_note = " (cached)" if not is_live_flag else ""
            self.last_message = f"Retrieved {len(offers)} offers from Amadeus GDS{cached_note}"
            self.last_offer_count = len(offers)

        except Exception as exc:
            self.last_status = "error"
            self.last_message = f"Amadeus query error: {exc}"

        return offers
