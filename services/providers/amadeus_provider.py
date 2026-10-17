"""
Amadeus Flight Offers Provider Adapter.
Integrates with Amadeus Self-Service API (GDS flight distribution).
Supports both 'test' (https://test.api.amadeus.com) and 'production' (https://api.amadeus.com) environments.
"""

import os
import re
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
import requests

from .base import BaseFlightProvider, FlightOffer, ProviderStatus
from services.api_guardian import (
    guardian,
    PROVIDER_AMADEUS,
    CALLER_USER_SEARCH,
    STATE_OPEN,
    STATE_QUOTA_EXHAUSTED,
    is_quota_exhausted_error,
    get_guardian_config,
)

# Explicit Amadeus Environments & Base URLs
AMADEUS_ENV_URLS = {
    "test": {
        "base_url": "https://test.api.amadeus.com",
        "auth_url": "https://test.api.amadeus.com/v1/security/oauth2/token",
        "flights_url": "https://test.api.amadeus.com/v2/shopping/flight-offers",
    },
    "production": {
        "base_url": "https://api.amadeus.com",
        "auth_url": "https://api.amadeus.com/v1/security/oauth2/token",
        "flights_url": "https://api.amadeus.com/v2/shopping/flight-offers",
    },
}

# Default backwards-compatible constants
AMADEUS_AUTH_URL = AMADEUS_ENV_URLS["test"]["auth_url"]
AMADEUS_FLIGHTS_URL = AMADEUS_ENV_URLS["test"]["flights_url"]

AIRLINE_NAMES = {
    "AI": "Air India",
    "6E": "IndiGo",
    "UK": "Vistara",
    "QP": "Akasa Air",
    "SG": "SpiceJet",
    "BA": "British Airways",
    "EK": "Emirates",
    "QR": "Qatar Airways",
    "SQ": "Singapore Airlines",
    "LH": "Lufthansa",
}


def get_amadeus_environment() -> str:
    """
    Return configured Amadeus environment ('test' or 'production').
    Rejects unrecognized environments with ValueError. Never silently switches environments.
    """
    env = (os.getenv("AMADEUS_ENVIRONMENT") or "test").lower().strip()
    if env not in AMADEUS_ENV_URLS:
        raise ValueError(
            f"Invalid AMADEUS_ENVIRONMENT: '{env}'. Expected 'test' or 'production'."
        )
    return env


def get_amadeus_urls(env: Optional[str] = None) -> Dict[str, str]:
    """Return explicit endpoint URLs for the configured Amadeus environment."""
    environment = env or get_amadeus_environment()
    return AMADEUS_ENV_URLS[environment]


def _parse_iso_duration(dur_str: str) -> int:
    """Parse ISO 8601 duration string (e.g. PT2H15M, PT130M) into integer minutes."""
    if not dur_str:
        return 120
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", dur_str)
    if m:
        h = int(m.group(1) or 0)
        mins = int(m.group(2) or 0)
        total = h * 60 + mins
        return total if total > 0 else 120
    return 120


class AmadeusFlightProvider(BaseFlightProvider):
    """Adapter for Amadeus Global Distribution System API."""

    def __init__(self, environment: Optional[str] = None):
        super().__init__(
            provider_id="amadeus",
            provider_name="Amadeus GDS",
            provider_type="gds"
        )
        self._configured_env = environment
        self.access_token: Optional[str] = None
        self.token_expires_at: Optional[float] = None
        self.last_status = "unconfigured"
        self.last_message = "Requires AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET in environment"
        self.last_offer_count = 0

    @property
    def environment(self) -> str:
        """Resolve current environment explicitly."""
        if self._configured_env:
            env = self._configured_env.lower().strip()
            if env not in AMADEUS_ENV_URLS:
                raise ValueError(
                    f"Invalid AMADEUS_ENVIRONMENT: '{env}'. Expected 'test' or 'production'."
                )
            return env
        return get_amadeus_environment()

    @property
    def is_test_environment(self) -> bool:
        return self.environment == "test"

    @property
    def auth_url(self) -> str:
        return AMADEUS_ENV_URLS[self.environment]["auth_url"]

    @property
    def flights_url(self) -> str:
        return AMADEUS_ENV_URLS[self.environment]["flights_url"]

    def is_configured(self) -> bool:
        client_id = os.getenv("AMADEUS_CLIENT_ID")
        client_secret = os.getenv("AMADEUS_CLIENT_SECRET")
        return bool(client_id and client_secret)

    def is_token_valid(self) -> bool:
        """Check whether the cached token exists and has not expired."""
        if not self.access_token or not self.token_expires_at:
            return False
        return time.time() < self.token_expires_at

    def clear_token(self) -> None:
        """Purge cached access token and expiration timestamp."""
        self.access_token = None
        self.token_expires_at = None

    def _get_valid_token(self) -> Optional[str]:
        """Return valid access token from in-memory cache, or authenticate if missing/expired."""
        if self.is_token_valid():
            return self.access_token
        if self._authenticate():
            return self.access_token
        return None

    def is_quota_exhausted(self) -> bool:
        """Check whether Amadeus is currently classified as quota-exhausted."""
        state, _ = guardian.get_circuit_state(self.provider_id)
        return state in (STATE_OPEN, STATE_QUOTA_EXHAUSTED)

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

        if self.is_quota_exhausted():
            _, cool = guardian.get_circuit_state(self.provider_id)
            cool_msg = f" (cooldown until {cool})" if cool else " (reset time unknown)"
            return ProviderStatus(
                provider_id=self.provider_id,
                provider_name=self.provider_name,
                provider_type=self.provider_type,
                status="quota_exhausted",
                is_live=False,
                message=f"Amadeus quota exhausted{cool_msg}",
                offers_count=0,
            )

        # In test environment, is_live is always False to never mislead users
        is_live = (self.last_status == "active") and (not self.is_test_environment)

        return ProviderStatus(
            provider_id=self.provider_id,
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=self.last_status,
            is_live=is_live,
            message=self.last_message,
            offers_count=self.last_offer_count
        )

    def _authenticate(self) -> bool:
        client_id = os.getenv("AMADEUS_CLIENT_ID")
        client_secret = os.getenv("AMADEUS_CLIENT_SECRET")
        if not client_id or not client_secret:
            self.last_status = "unconfigured"
            self.last_message = "Requires AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET in environment"
            return False

        try:
            res = requests.post(
                self.auth_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10
            )

            if res.status_code == 200:
                payload = res.json()
                self.access_token = payload.get("access_token")
                expires_in = int(payload.get("expires_in", 1799))
                # Apply 60-second safety margin
                self.token_expires_at = time.time() + max(expires_in - 60, 60)
                self.last_status = "active"
                self.last_message = f"Authenticated with Amadeus ({self.environment} environment)"
                return True
            elif res.status_code == 401:
                self.clear_token()
                self.last_status = "error"
                self.last_message = "Amadeus authentication failed: Invalid AMADEUS_CLIENT_ID or AMADEUS_CLIENT_SECRET (HTTP 401)"
                return False
            elif res.status_code == 403:
                self.clear_token()
                self.last_status = "error"
                self.last_message = "Amadeus authentication forbidden: Account or API permissions disabled (HTTP 403)"
                return False
            elif res.status_code == 429 or is_quota_exhausted_error(res.status_code, str(res.text)):
                self.clear_token()
                guardian.record_circuit_failure(self.provider_id, status_code=429, error_msg=f"Amadeus auth quota exhausted: {res.text}")
                self.last_status = "quota_exhausted"
                self.last_message = "Amadeus authentication quota exhausted (HTTP 429)"
                return False
            else:
                self.clear_token()
                self.last_status = "error"
                self.last_message = f"Amadeus auth failed (HTTP {res.status_code}): {res.text}"
                return False
        except Exception as e:
            self.clear_token()
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
            self.last_message = "Requires AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET in environment"
            return []

        if self.is_quota_exhausted():
            self.last_status = "quota_exhausted"
            self.last_message = "Amadeus quota exhausted. Upstream search suppressed."
            return []

        token = self._get_valid_token()
        if not token:
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
                timeout = cfg.get("amadeus_timeout_seconds", cfg.get("amadeus_timeout", 15))
                headers = {"Authorization": f"Bearer {self.access_token}"}
                res = requests.get(self.flights_url, headers=headers, params=p, timeout=timeout)

                # HTTP 401: Token expired or rejected by server - clear cache and re-authenticate once
                if res.status_code == 401:
                    self.clear_token()
                    if self._authenticate():
                        headers = {"Authorization": f"Bearer {self.access_token}"}
                        res = requests.get(self.flights_url, headers=headers, params=p, timeout=timeout)

                if res.status_code == 401:
                    err = requests.exceptions.HTTPError("Amadeus flight search unauthorized (HTTP 401)", response=res)
                    setattr(err, "status_code", 401)
                    raise err

                if res.status_code == 403:
                    err = requests.exceptions.HTTPError(f"Amadeus flight search forbidden (HTTP 403): {res.text}", response=res)
                    setattr(err, "status_code", 403)
                    raise err

                if res.status_code == 429 or is_quota_exhausted_error(res.status_code, str(res.text)):
                    err = requests.exceptions.HTTPError(f"Amadeus quota exhausted: {res.text or 'HTTP 429'}", response=res)
                    setattr(err, "status_code", 429)
                    raise err

                if res.status_code >= 400:
                    err = requests.exceptions.HTTPError(f"Amadeus flight search returned HTTP {res.status_code}: {res.text}", response=res)
                    setattr(err, "status_code", res.status_code)
                    raise err

                try:
                    return res.json()
                except Exception as json_err:
                    raise ValueError(f"Amadeus returned invalid non-JSON response: {json_err}")

            guardian_resp = guardian.execute_request(
                provider_id=self.provider_id,
                endpoint="flight-offers",
                params=params,
                fetch_fn=_fetch,
                caller_type=CALLER_USER_SEARCH
            )

            if guardian_resp.status in ("blocked_budget", "circuit_open"):
                self.last_status = "quota_exhausted" if guardian_resp.status == "circuit_open" else "rate_limited"
                self.last_message = guardian_resp.error or "Amadeus provider temporarily blocked by API Guardian"
                return []

            if guardian_resp.status == "error" or not guardian_resp.data:
                self.last_status = "error"
                self.last_message = guardian_resp.error or "Amadeus query failed"
                return []

            data = guardian_resp.data
            is_cached = guardian_resp.is_cached
            is_test = self.is_test_environment

            # Truthful labeling per requirement 8:
            # Test-environment results must ALWAYS be labeled as test data and NEVER as verified live fares.
            if is_test:
                is_live_flag = False
                data_status_flag = "test"
                source_str = "Amadeus GDS (Test Data - Cached)" if is_cached else "Amadeus GDS (Test Data)"
            else:
                is_live_flag = not is_cached
                data_status_flag = "cached" if is_cached else "live"
                source_str = "Amadeus GDS (Cached)" if is_cached else "Amadeus GDS"

            raw_offers = data.get("data", [])
            dictionaries = data.get("dictionaries", {})
            carriers_dict = dictionaries.get("carriers", {})

            for item in raw_offers:
                itineraries = item.get("itineraries", [])
                if not itineraries:
                    continue
                outbound_itin = itineraries[0]
                raw_segments = outbound_itin.get("segments", [])
                if not raw_segments:
                    continue

                first_seg = raw_segments[0]
                last_seg = raw_segments[-1]
                carrier_code = first_seg.get("carrierCode", "Carrier")
                airline = carriers_dict.get(carrier_code) or AIRLINE_NAMES.get(carrier_code) or carrier_code
                flight_no = f"{carrier_code} {first_seg.get('number', '')}".strip()

                dep_raw_time = first_seg.get("departure", {}).get("at", "")
                dep_time = dep_raw_time.split("T")[-1][:5] if "T" in dep_raw_time else dep_raw_time[-5:]

                arr_raw_time = last_seg.get("arrival", {}).get("at", "")
                arr_time = arr_raw_time.split("T")[-1][:5] if "T" in arr_raw_time else arr_raw_time[-5:]

                stops = max(len(raw_segments) - 1, 0)
                price_total = float(item.get("price", {}).get("total", 0))
                currency = item.get("price", {}).get("currency", "INR")
                duration = _parse_iso_duration(outbound_itin.get("duration", ""))

                # Baggage allowance
                baggage = "Standard allowance included"
                traveler_pricings = item.get("travelerPricings", [])
                if traveler_pricings:
                    fare_details = traveler_pricings[0].get("fareDetailsBySegment", [])
                    if fare_details:
                        bags = fare_details[0].get("includedCheckedBags", {})
                        if "quantity" in bags:
                            baggage = f"{bags['quantity']} checked bag(s) included"
                        elif "weight" in bags:
                            baggage = f"{bags['weight']} {bags.get('weightUnit', 'KG')} checked baggage included"

                # Bookable seats
                num_seats = item.get("numberOfBookableSeats")

                # Build outbound segments
                built_segments = []
                for s in raw_segments:
                    s_carrier = s.get("carrierCode", "")
                    s_airline = carriers_dict.get(s_carrier) or AIRLINE_NAMES.get(s_carrier) or s_carrier or "Carrier"
                    s_dep = s.get("departure", {})
                    s_arr = s.get("arrival", {})
                    s_dt = s_dep.get("at", "")
                    s_at = s_arr.get("at", "")
                    built_segments.append({
                        "departure_airport": s_dep.get("iataCode", departure.upper()),
                        "departure_name": f"{s_dep.get('iataCode', departure.upper())} Airport",
                        "departure_terminal": s_dep.get("terminal"),
                        "departure_time": s_dt.split("T")[-1][:5] if "T" in s_dt else s_dt[-5:],
                        "arrival_airport": s_arr.get("iataCode", arrival.upper()),
                        "arrival_name": f"{s_arr.get('iataCode', arrival.upper())} Airport",
                        "arrival_terminal": s_arr.get("terminal"),
                        "arrival_time": s_at.split("T")[-1][:5] if "T" in s_at else s_at[-5:],
                        "airline": s_airline,
                        "flight_number": f"{s_carrier} {s.get('number', '')}".strip(),
                        "duration": _parse_iso_duration(s.get("duration", "")),
                        "airline_logo": ""
                    })

                # Compute layovers
                layovers = []
                for idx in range(len(built_segments) - 1):
                    curr_arr_at = raw_segments[idx].get("arrival", {}).get("at", "")
                    next_dep_at = raw_segments[idx + 1].get("departure", {}).get("at", "")
                    layover_mins = 45
                    if curr_arr_at and next_dep_at:
                        try:
                            t1 = datetime.fromisoformat(curr_arr_at)
                            t2 = datetime.fromisoformat(next_dep_at)
                            diff = int((t2 - t1).total_seconds() / 60)
                            if diff > 0:
                                layover_mins = diff
                        except Exception:
                            pass
                    layovers.append({
                        "id": built_segments[idx]["arrival_airport"],
                        "name": built_segments[idx]["arrival_name"],
                        "duration": layover_mins,
                        "overnight": layover_mins >= 720
                    })

                # Return leg if round trip present in response
                ret_built_segments = []
                ret_layovers = []
                ret_airline = None
                ret_duration = None
                ret_stops = None
                ret_dep_time = None
                ret_arr_time = None
                ret_flight_no = None

                if len(itineraries) > 1:
                    ret_itin = itineraries[1]
                    ret_raw_segs = ret_itin.get("segments", [])
                    if ret_raw_segs:
                        ret_first_seg = ret_raw_segs[0]
                        ret_last_seg = ret_raw_segs[-1]
                        ret_carrier = ret_first_seg.get("carrierCode", "")
                        ret_airline = carriers_dict.get(ret_carrier) or AIRLINE_NAMES.get(ret_carrier) or ret_carrier or "Carrier"
                        ret_flight_no = f"{ret_carrier} {ret_first_seg.get('number', '')}".strip()
                        ret_dt_raw = ret_first_seg.get("departure", {}).get("at", "")
                        ret_at_raw = ret_last_seg.get("arrival", {}).get("at", "")
                        ret_dep_time = ret_dt_raw.split("T")[-1][:5] if "T" in ret_dt_raw else ret_dt_raw[-5:]
                        ret_arr_time = ret_at_raw.split("T")[-1][:5] if "T" in ret_at_raw else ret_at_raw[-5:]
                        ret_duration = _parse_iso_duration(ret_itin.get("duration", ""))
                        ret_stops = max(len(ret_raw_segs) - 1, 0)

                        for s in ret_raw_segs:
                            s_carrier = s.get("carrierCode", "")
                            s_airline = carriers_dict.get(s_carrier) or AIRLINE_NAMES.get(s_carrier) or s_carrier or "Carrier"
                            s_dep = s.get("departure", {})
                            s_arr = s.get("arrival", {})
                            s_dt = s_dep.get("at", "")
                            s_at = s_arr.get("at", "")
                            ret_built_segments.append({
                                "departure_airport": s_dep.get("iataCode", arrival.upper()),
                                "departure_name": f"{s_dep.get('iataCode', arrival.upper())} Airport",
                                "departure_terminal": s_dep.get("terminal"),
                                "departure_time": s_dt.split("T")[-1][:5] if "T" in s_dt else s_dt[-5:],
                                "arrival_airport": s_arr.get("iataCode", departure.upper()),
                                "arrival_name": f"{s_arr.get('iataCode', departure.upper())} Airport",
                                "arrival_terminal": s_arr.get("terminal"),
                                "arrival_time": s_at.split("T")[-1][:5] if "T" in s_at else s_at[-5:],
                                "airline": s_airline,
                                "flight_number": f"{s_carrier} {s.get('number', '')}".strip(),
                                "duration": _parse_iso_duration(s.get("duration", "")),
                                "airline_logo": ""
                            })

                        for idx in range(len(ret_built_segments) - 1):
                            curr_arr_at = ret_raw_segs[idx].get("arrival", {}).get("at", "")
                            next_dep_at = ret_raw_segs[idx + 1].get("departure", {}).get("at", "")
                            layover_mins = 45
                            if curr_arr_at and next_dep_at:
                                try:
                                    t1 = datetime.fromisoformat(curr_arr_at)
                                    t2 = datetime.fromisoformat(next_dep_at)
                                    diff = int((t2 - t1).total_seconds() / 60)
                                    if diff > 0:
                                        layover_mins = diff
                                except Exception:
                                    pass
                            ret_layovers.append({
                                "id": ret_built_segments[idx]["arrival_airport"],
                                "name": ret_built_segments[idx]["arrival_name"],
                                "duration": layover_mins,
                                "overnight": layover_mins >= 720
                            })

                is_round_trip = bool(return_date or len(itineraries) > 1)

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
                    duration=duration,
                    stops=stops,
                    price=int(price_total),
                    currency=currency,
                    baggage=baggage,
                    booking_url="https://amadeus.com",
                    trip_type="round_trip" if is_round_trip else "one_way",
                    segments=built_segments,
                    layovers=layovers,
                    is_live=is_live_flag,
                    is_estimated=False,
                    data_status=data_status_flag,
                    source=source_str,
                    return_airline=ret_airline,
                    return_flight_number=ret_flight_no,
                    return_departure_time=ret_dep_time,
                    return_arrival_time=ret_arr_time,
                    return_duration=ret_duration,
                    return_stops=ret_stops,
                    return_departure_airport=arrival.upper() if ret_built_segments else None,
                    return_arrival_airport=departure.upper() if ret_built_segments else None,
                    return_segments=ret_built_segments,
                    return_layovers=ret_layovers,
                    number_of_bookable_seats=num_seats
                )
                offers.append(offer)

            self.last_status = "active"
            cached_note = " (cached)" if is_cached else ""
            test_note = " test" if is_test else ""
            self.last_message = f"Retrieved {len(offers)}{test_note} offers from Amadeus GDS{cached_note}"
            self.last_offer_count = len(offers)

        except Exception as exc:
            self.last_status = "error"
            self.last_message = f"Amadeus query error: {exc}"

        return offers
