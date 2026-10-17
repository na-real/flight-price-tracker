"""
FlightAPI.io Provider Adapter.
Integrates with FlightAPI.io (https://www.flightapi.io) for one-way, round-trip,
and multi-city flight searches.
All requests are routed through API Guardian for budget enforcement, request coalescing,
two-tier caching, and quota protection.
"""

import os
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
import requests

from .base import (
    BaseFlightProvider,
    FlightOffer,
    ProviderStatus,
    ProviderQuotaExhaustedError,
    ProviderUnavailableError,
)
from services.api_guardian import (
    guardian,
    PROVIDER_FLIGHTAPI,
    CALLER_USER_SEARCH,
    STATE_OPEN,
    STATE_QUOTA_EXHAUSTED,
    is_quota_exhausted_error,
    get_guardian_config,
)

FLIGHTAPI_BASE_URL = "https://api.flightapi.io"

# SkyTracker travel_class integer to FlightAPI cabin_class string
FLIGHTAPI_CABIN_MAP = {
    1: "Economy",
    2: "Premium_Economy",
    3: "Business",
    4: "First",
}

CURRENCY_RATES = {
    "INR": 1.0,
    "USD": 86.5,
    "EUR": 93.0,
    "GBP": 110.0,
    "AED": 23.5,
    "SGD": 64.0,
}

DEFAULT_CURRENCY = "INR"
DEFAULT_REGION = "IN"


def sanitize_flightapi_url(url_or_text: str) -> str:
    """
    Redact the secret API key from FlightAPI URLs, paths, and error messages to prevent credential leakage.
    FlightAPI embeds the key in the path: /onewaytrip/{api_key}/... or /multitrip/{api_key}?...
    """
    if not url_or_text:
        return ""
    text = str(url_or_text)
    # Redact key in path: /onewaytrip/<key>/, /roundtrip/<key>/, /multitrip/<key>
    redacted = re.sub(
        r"/(onewaytrip|roundtrip|multitrip)/([^/?\s\"']+)",
        r"/\1/[REDACTED]",
        text
    )
    # Also explicitly redact the exact raw key if present in the text
    api_key = os.getenv("FLIGHTAPI_API_KEY")
    if api_key and len(api_key.strip()) >= 5 and api_key in redacted:
        redacted = redacted.replace(api_key, "[REDACTED]")
    return redacted



def _safe_int(val: Any, default: int = 0) -> int:
    try:
        if val is None:
            return default
        return int(float(val))
    except (ValueError, TypeError):
        return default


def _extract_hhmm(iso_or_time_str: Optional[str]) -> str:
    """Extract HH:MM format from ISO timestamp or return as-is."""
    if not iso_or_time_str:
        return "12:00"
    s = str(iso_or_time_str).strip()
    if "T" in s:
        time_part = s.split("T")[1]
        return time_part[:5]
    elif " " in s:
        time_part = s.split(" ")[1]
        return time_part[:5]
    return s[:5] if len(s) >= 5 else s


def _build_lookup(data_collection: Any) -> Dict[str, Dict[str, Any]]:
    """Build a dictionary lookup keyed by ID or code from either a list or dict."""
    lookup: Dict[str, Dict[str, Any]] = {}
    if isinstance(data_collection, list):
        for item in data_collection:
            if isinstance(item, dict):
                item_id = str(item.get("id") or item.get("code") or "")
                if item_id:
                    lookup[item_id] = item
    elif isinstance(data_collection, dict):
        for k, v in data_collection.items():
            if isinstance(v, dict):
                lookup[str(k)] = v
                if "id" in v:
                    lookup[str(v["id"])] = v
                if "code" in v:
                    lookup[str(v["code"])] = v
    return lookup


class FlightApiProvider(BaseFlightProvider):
    """
    Adapter for FlightAPI.io metasearch engine.
    Supports:
    - One-way flight searches (/onewaytrip)
    - Round-trip flight searches (/roundtrip)
    - Multi-city flight searches (/multitrip, strictly 3-5 legs as documented)
    """

    def __init__(self):
        super().__init__(
            provider_id="flightapi",
            provider_name="FlightAPI",
            provider_type="metasearch"
        )
        self.last_status = "active"
        self.last_message = "Ready to search flights via FlightAPI"
        self.last_offer_count = 0

    @property
    def api_key(self) -> Optional[str]:
        return os.getenv("FLIGHTAPI_API_KEY")

    @property
    def region(self) -> str:
        return (os.getenv("FLIGHTAPI_REGION") or DEFAULT_REGION).strip().upper()

    def is_configured(self) -> bool:
        """Check whether the necessary FLIGHTAPI_API_KEY is configured in the environment."""
        key = self.api_key
        return bool(key and key.strip())

    def is_quota_exhausted(self) -> bool:
        """Check whether FlightAPI is currently in a quota-exhausted state."""
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
                message="FLIGHTAPI_API_KEY is not configured in .env",
                offers_count=self.last_offer_count,
            )

        if self.is_quota_exhausted():
            q_status = guardian.get_provider_quota_status(self.provider_id)
            reason = q_status.get("reason", "")
            _, cool = guardian.get_circuit_state(self.provider_id)
            cool_msg = f" (cooldown until {cool})" if cool else " (reset time unknown)"

            if is_quota_exhausted_error(0, reason):
                return ProviderStatus(
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    provider_type=self.provider_type,
                    status="quota_exhausted",
                    is_live=False,
                    message=f"FlightAPI quota exhausted{cool_msg}",
                    offers_count=0,
                )

            auth_desc = " authentication failed (HTTP 401)" if ("Authentication" in reason or "Invalid" in reason) else ""
            return ProviderStatus(
                provider_id=self.provider_id,
                provider_name=self.provider_name,
                provider_type=self.provider_type,
                status="error",
                is_live=False,
                message=f"FlightAPI{auth_desc} unavailable: {reason}{cool_msg}",
                offers_count=0,
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

    def _build_oneway_url(
        self,
        departure: str,
        arrival: str,
        outbound_date: str,
        adults: int = 1,
        children: int = 0,
        infants: int = 0,
        cabin_class: str = "Economy",
        currency: str = DEFAULT_CURRENCY,
        region: Optional[str] = None
    ) -> str:
        key = self.api_key or ""
        reg = region or self.region
        return (
            f"{FLIGHTAPI_BASE_URL}/onewaytrip/{key}/"
            f"{departure.upper()}/{arrival.upper()}/{outbound_date}/"
            f"{adults}/{children}/{infants}/{cabin_class}/{currency}?region={reg}"
        )

    def _build_roundtrip_url(
        self,
        departure: str,
        arrival: str,
        outbound_date: str,
        return_date: str,
        adults: int = 1,
        children: int = 0,
        infants: int = 0,
        cabin_class: str = "Economy",
        currency: str = DEFAULT_CURRENCY,
        region: Optional[str] = None
    ) -> str:
        key = self.api_key or ""
        reg = region or self.region
        return (
            f"{FLIGHTAPI_BASE_URL}/roundtrip/{key}/"
            f"{departure.upper()}/{arrival.upper()}/{outbound_date}/{return_date}/"
            f"{adults}/{children}/{infants}/{cabin_class}/{currency}?region={reg}"
        )

    def _build_multitrip_request(
        self,
        legs: List[Dict[str, Any]],
        adults: int = 1,
        children: int = 0,
        infants: int = 0,
        cabin_class: str = "Economy",
        currency: str = DEFAULT_CURRENCY,
        region: Optional[str] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Build URL and query parameters for FlightAPI multi-trip endpoint.
        Documented route limit: strictly 3 to 5 trips.
        """
        num_legs = len(legs)
        if num_legs < 3 or num_legs > 5:
            raise ValueError(
                f"FlightAPI multi-trip API requires between 3 and 5 route legs as documented "
                f"(received {num_legs}). For 1 or 2 legs, please use one-way or round-trip search."
            )

        key = self.api_key or ""
        reg = region or self.region
        url = f"{FLIGHTAPI_BASE_URL}/multitrip/{key}"

        params: Dict[str, Any] = {
            "trips": num_legs,
            "adults": adults,
            "children": children,
            "infants": infants,
            "cabinclass": cabin_class,
            "currency": currency,
            "region": reg,
        }

        for i, leg in enumerate(legs, start=1):
            dep = str(leg.get("departure") or "").strip().upper()
            arr = str(leg.get("arrival") or "").strip().upper()
            dt = str(leg.get("outbound_date") or "").strip()
            params[f"arp{2 * i - 1}"] = dep
            params[f"arp{2 * i}"] = arr
            params[f"date{i}"] = dt

        return url, params

    def _normalize_response(
        self,
        data: Dict[str, Any],
        trip_type: str = "one_way",
        default_departure: str = "",
        default_arrival: str = "",
        is_live: bool = True,
        is_cached: bool = False
    ) -> List[FlightOffer]:
        """
        Normalize FlightAPI relational JSON response into SkyTracker FlightOffer objects.
        Safely handles missing fields, empty options, and nested booking deep links.
        """
        if not isinstance(data, dict):
            return []

        raw_itineraries = data.get("itineraries") or []
        if not raw_itineraries:
            return []

        legs_map = _build_lookup(data.get("legs"))
        segments_map = _build_lookup(data.get("segments"))
        carriers_map = _build_lookup(data.get("carriers"))
        agents_map = _build_lookup(data.get("agents"))
        places_map = _build_lookup(data.get("places"))

        offers: List[FlightOffer] = []
        seen_offers: Dict[str, FlightOffer] = {}

        data_status = "cached" if is_cached else ("live" if is_live else "estimated")
        source_name = "FlightAPI (Cached)" if is_cached else "FlightAPI"

        for itin in raw_itineraries:
            if not isinstance(itin, dict):
                continue

            itin_id = str(itin.get("id") or "")
            pricing_options = itin.get("pricing_options") or []
            if not pricing_options:
                continue

            # Extract all genuine vendor pricing options for exact price comparison
            vendor_offers: List[Dict[str, Any]] = []
            best_opt = None
            lowest_amount = float("inf")
            booking_link = ""
            primary_vendor = "FlightAPI Partner"

            for opt in pricing_options:
                if not isinstance(opt, dict):
                    continue
                # Price extraction & currency conversion
                price_field = opt.get("price")
                amount = None
                curr = DEFAULT_CURRENCY
                if isinstance(price_field, dict):
                    amount = price_field.get("amount")
                    curr = price_field.get("currency") or DEFAULT_CURRENCY
                elif price_field is not None:
                    amount = price_field

                if amount is None:
                    continue

                try:
                    raw_val = float(amount)
                except (TypeError, ValueError):
                    continue

                if raw_val <= 0:
                    continue

                curr_str = str(curr).upper().strip()
                if curr_str != "INR":
                    rate = CURRENCY_RATES.get(curr_str, 1.0)
                    num_amt = max(1, int(round(raw_val * rate)))
                else:
                    num_amt = max(1, int(round(raw_val)))

                # Booking link for this specific vendor option
                opt_link = ""
                items = opt.get("items") or []
                if items and isinstance(items[0], dict) and items[0].get("url"):
                    opt_link = items[0]["url"]
                elif opt.get("url"):
                    opt_link = opt["url"]
                elif opt.get("deep_link"):
                    opt_link = opt["deep_link"]

                # Agent / vendor name
                opt_vendor = "FlightAPI Partner"
                agent_ids = opt.get("agent_ids") or []
                if agent_ids:
                    agent_obj = agents_map.get(str(agent_ids[0]))
                    if agent_obj and agent_obj.get("name"):
                        opt_vendor = agent_obj["name"]
                elif opt.get("agent_name"):
                    opt_vendor = opt["agent_name"]
                elif opt.get("vendor"):
                    opt_vendor = opt["vendor"]

                vendor_offers.append({
                    "provider": opt_vendor,
                    "provider_name": opt_vendor,
                    "vendor": opt_vendor,
                    "agent_name": opt_vendor,
                    "provider_type": "ota",
                    "price": num_amt,
                    "currency": DEFAULT_CURRENCY,
                    "booking_url": opt_link or "https://www.flightapi.io",
                    "itinerary_id": itin_id,
                })

                if num_amt < lowest_amount:
                    lowest_amount = num_amt
                    best_opt = opt
                    booking_link = opt_link
                    primary_vendor = opt_vendor

            if lowest_amount == float("inf"):
                continue

            leg_ids = itin.get("leg_ids") or []
            out_leg = {}
            if leg_ids:
                out_leg_id = str(leg_ids[0])
                out_leg = legs_map.get(out_leg_id) or {}
            elif itin.get("legs"):
                embedded = itin.get("legs")
                if isinstance(embedded, list) and embedded:
                    if isinstance(embedded[0], dict):
                        out_leg = embedded[0]
                    else:
                        out_leg = legs_map.get(str(embedded[0])) or {}

            if not out_leg:
                continue

            dep_code = (
                out_leg.get("departure_airport_code")
                or (places_map.get(str(out_leg.get("origin_place_id", ""))) or {}).get("code")
                or default_departure.upper()
            )
            arr_code = (
                out_leg.get("arrival_airport_code")
                or (places_map.get(str(out_leg.get("destination_place_id", ""))) or {}).get("code")
                or default_arrival.upper()
            )

            dep_time = _extract_hhmm(out_leg.get("departure"))
            arr_time = _extract_hhmm(out_leg.get("arrival"))
            duration = _safe_int(out_leg.get("duration"), 120)
            stops = _safe_int(out_leg.get("stop_count"), 0)

            # Resolve carrier & flight number from segments
            seg_ids = out_leg.get("segment_ids") or []
            airline_name = "Airline"
            flight_num = ""
            segments_list = []
            layovers_list = []

            prev_seg_arrival_time = None
            prev_seg_airport = None

            for s_id in seg_ids:
                seg = segments_map.get(str(s_id)) or {}
                s_dep = (
                    seg.get("departure_airport_code")
                    or (places_map.get(str(seg.get("origin_place_id", ""))) or {}).get("code")
                    or dep_code
                )
                s_arr = (
                    seg.get("arrival_airport_code")
                    or (places_map.get(str(seg.get("destination_place_id", ""))) or {}).get("code")
                    or arr_code
                )
                s_dep_t = _extract_hhmm(seg.get("departure"))
                s_arr_t = _extract_hhmm(seg.get("arrival"))
                s_dur = _safe_int(seg.get("duration"), 90)

                carrier_id = str(seg.get("marketing_carrier_id") or "")
                carrier_obj = carriers_map.get(carrier_id) or {}
                s_carrier = carrier_obj.get("name") or airline_name
                s_logo = carrier_obj.get("logo_url") or carrier_obj.get("logo") or ""

                f_no = str(seg.get("marketing_flight_number") or seg.get("flight_number") or "").strip()
                c_code = (carrier_obj.get("iata") or carrier_obj.get("code") or "").strip()
                if f_no and c_code and not f_no.startswith(c_code) and not f_no.startswith(s_carrier):
                    f_no = f"{c_code} {f_no}".strip()

                if not flight_num and f_no:
                    flight_num = f_no
                if airline_name == "Airline" and s_carrier != "Airline":
                    airline_name = s_carrier

                # Layover calculation between segments
                if prev_seg_arrival_time and prev_seg_airport:
                    layovers_list.append({
                        "id": prev_seg_airport,
                        "name": f"{prev_seg_airport} Airport",
                        "duration": max(30, _safe_int(seg.get("layover_duration"), 60)),
                        "overnight": False,
                    })

                prev_seg_arrival_time = s_arr_t
                prev_seg_airport = s_arr

                segments_list.append({
                    "departure_airport": s_dep,
                    "departure_name": f"{s_dep} Airport",
                    "departure_time": s_dep_t,
                    "arrival_airport": s_arr,
                    "arrival_name": f"{s_arr} Airport",
                    "arrival_time": s_arr_t,
                    "airline": s_carrier,
                    "flight_number": f_no or "Flight",
                    "duration": s_dur,
                    "airline_logo": s_logo,
                })

            if airline_name == "Airline":
                if out_leg.get("airline_name"):
                    airline_name = out_leg["airline_name"]
                elif out_leg.get("airline"):
                    airline_name = out_leg["airline"]
                else:
                    leg_carrier_codes = out_leg.get("airline_codes") or out_leg.get("carrier_ids") or []
                    if isinstance(leg_carrier_codes, list) and leg_carrier_codes:
                        c_id = str(leg_carrier_codes[0])
                        c_obj = carriers_map.get(c_id) or {}
                        if c_obj.get("name"):
                            airline_name = c_obj["name"]
                        elif c_obj.get("iata") or c_obj.get("code"):
                            airline_name = c_obj.get("iata") or c_obj.get("code")
                    elif out_leg.get("marketing_carrier_id") or out_leg.get("carrier_id"):
                        c_id = str(out_leg.get("marketing_carrier_id") or out_leg.get("carrier_id"))
                        c_obj = carriers_map.get(c_id) or {}
                        if c_obj.get("name"):
                            airline_name = c_obj["name"]
                        elif c_obj.get("iata") or c_obj.get("code"):
                            airline_name = c_obj.get("iata") or c_obj.get("code")

            if not flight_num:
                if out_leg.get("flight_number"):
                    flight_num = out_leg["flight_number"]
                elif out_leg.get("marketing_flight_number"):
                    flight_num = out_leg["marketing_flight_number"]
                else:
                    flight_num = f"{airline_name} Flight"

            # Fallback segments if no segment details provided in response
            if not segments_list:
                segments_list.append({
                    "departure_airport": dep_code,
                    "departure_name": f"{dep_code} Airport",
                    "departure_time": dep_time,
                    "arrival_airport": arr_code,
                    "arrival_name": f"{arr_code} Airport",
                    "arrival_time": arr_time,
                    "airline": airline_name,
                    "flight_number": flight_num,
                    "duration": duration,
                    "airline_logo": "",
                })

            # Handle Return Leg for Round-Trip
            ret_airline = None
            ret_flight_num = None
            ret_dep_time = None
            ret_arr_time = None
            ret_duration = None
            ret_stops = None
            ret_dep_code = None
            ret_arr_code = None
            ret_segments = []
            ret_layovers = []

            if trip_type == "round_trip" and len(leg_ids) > 1:
                ret_leg_id = str(leg_ids[1])
                ret_leg = legs_map.get(ret_leg_id) or {}

                ret_dep_code = (
                    ret_leg.get("departure_airport_code")
                    or (places_map.get(str(ret_leg.get("origin_place_id", ""))) or {}).get("code")
                    or arr_code
                )
                ret_arr_code = (
                    ret_leg.get("arrival_airport_code")
                    or (places_map.get(str(ret_leg.get("destination_place_id", ""))) or {}).get("code")
                    or dep_code
                )
                ret_dep_time = _extract_hhmm(ret_leg.get("departure"))
                ret_arr_time = _extract_hhmm(ret_leg.get("arrival"))
                ret_duration = _safe_int(ret_leg.get("duration"), duration)
                ret_stops = _safe_int(ret_leg.get("stop_count"), 0)

                ret_seg_ids = ret_leg.get("segment_ids") or []
                ret_airline = airline_name

                for s_id in ret_seg_ids:
                    seg = segments_map.get(str(s_id)) or {}
                    s_dep = (
                        seg.get("departure_airport_code")
                        or (places_map.get(str(seg.get("origin_place_id", ""))) or {}).get("code")
                        or ret_dep_code
                    )
                    s_arr = (
                        seg.get("arrival_airport_code")
                        or (places_map.get(str(seg.get("destination_place_id", ""))) or {}).get("code")
                        or ret_arr_code
                    )
                    s_dep_t = _extract_hhmm(seg.get("departure"))
                    s_arr_t = _extract_hhmm(seg.get("arrival"))
                    s_dur = _safe_int(seg.get("duration"), 90)

                    c_id = str(seg.get("marketing_carrier_id") or "")
                    c_obj = carriers_map.get(c_id) or {}
                    s_carrier = c_obj.get("name") or ret_airline
                    s_logo = c_obj.get("logo_url") or c_obj.get("logo") or ""

                    f_no = str(seg.get("marketing_flight_number") or seg.get("flight_number") or "").strip()
                    c_code = (c_obj.get("iata") or c_obj.get("code") or "").strip()
                    if f_no and c_code and not f_no.startswith(c_code) and not f_no.startswith(s_carrier):
                        f_no = f"{c_code} {f_no}".strip()

                    if not ret_flight_num and f_no:
                        ret_flight_num = f_no
                    if ret_airline == airline_name and s_carrier != airline_name:
                        ret_airline = s_carrier

                    ret_segments.append({
                        "departure_airport": s_dep,
                        "departure_name": f"{s_dep} Airport",
                        "departure_time": s_dep_t,
                        "arrival_airport": s_arr,
                        "arrival_name": f"{s_arr} Airport",
                        "arrival_time": s_arr_t,
                        "airline": s_carrier,
                        "flight_number": f_no or "Flight",
                        "duration": s_dur,
                        "airline_logo": s_logo,
                    })

                if not ret_flight_num:
                    ret_flight_num = f"{ret_airline} Return"
                if not ret_segments:
                    ret_segments.append({
                        "departure_airport": ret_dep_code,
                        "departure_name": f"{ret_dep_code} Airport",
                        "departure_time": ret_dep_time,
                        "arrival_airport": ret_arr_code,
                        "arrival_name": f"{ret_arr_code} Airport",
                        "arrival_time": ret_arr_time,
                        "airline": ret_airline,
                        "flight_number": ret_flight_num,
                        "duration": ret_duration,
                        "airline_logo": "",
                    })

            offer = FlightOffer(
                offer_id=itin_id,
                provider_id=self.provider_id,
                provider_name=self.provider_name,
                provider_type=self.provider_type,
                airline=airline_name,
                flight_number=flight_num,
                departure_airport=dep_code,
                arrival_airport=arr_code,
                departure_time=dep_time,
                arrival_time=arr_time,
                duration=duration,
                stops=stops,
                price=int(lowest_amount),
                currency=DEFAULT_CURRENCY,
                baggage="Cabin: 7 kg included",
                booking_url=booking_link or "https://www.flightapi.io",
                is_best_price=False,
                price_diff=0,
                trip_type=trip_type,
                segments=segments_list,
                layovers=layovers_list,
                is_live=is_live and not is_cached,
                is_estimated=False,
                data_status=data_status,
                source=source_name,
                comparison_offers=vendor_offers,
                return_airline=ret_airline,
                return_flight_number=ret_flight_num,
                return_departure_time=ret_dep_time,
                return_arrival_time=ret_arr_time,
                return_duration=ret_duration,
                return_stops=ret_stops,
                return_departure_airport=ret_dep_code,
                return_arrival_airport=ret_arr_code,
                return_segments=ret_segments,
                return_layovers=ret_layovers,
            )

            # Safe itinerary deduplication within provider response:
            # Group records sharing the exact same schedule (same route, times, flight number, stops)
            # while keeping different flight numbers or departure times strictly separate.
            if offer.itinerary_key in seen_offers:
                existing = seen_offers[offer.itinerary_key]
                if offer.price < existing.price:
                    existing.price = offer.price
                    existing.booking_url = offer.booking_url
                    existing.offer_id = offer.offer_id
                for v in offer.comparison_offers:
                    if not any((e.get("provider") == v.get("provider") or e.get("provider_name") == v.get("provider_name")) and e.get("price") == v.get("price") for e in existing.comparison_offers):
                        existing.comparison_offers.append(v)
            else:
                seen_offers[offer.itinerary_key] = offer
                offers.append(offer)

        self.last_offer_count = len(offers)
        return sorted(offers, key=lambda o: o.price)

    def search(
        self,
        departure: str,
        arrival: str,
        outbound_date: str,
        return_date: Optional[str] = None,
        travel_class: int = 1,
        reference_itineraries: Optional[List[Dict[str, Any]]] = None
    ) -> List[FlightOffer]:
        """
        Execute flight search via FlightAPI.io one-way or round-trip endpoint.
        Routed through API Guardian for caching and quota protection.
        """
        if not self.is_configured():
            self.last_status = "unconfigured"
            self.last_message = "FLIGHTAPI_API_KEY is not configured in environment"
            return []

        if self.is_quota_exhausted():
            self.last_status = "quota_exhausted"
            self.last_message = "FlightAPI quota exhausted. Upstream search suppressed."
            return []

        trip_type = "round_trip" if return_date else "one_way"
        cabin_class = FLIGHTAPI_CABIN_MAP.get(travel_class, "Economy")
        region = self.region
        currency = DEFAULT_CURRENCY

        if trip_type == "round_trip":
            endpoint_name = "roundtrip"
            url = self._build_roundtrip_url(
                departure=departure,
                arrival=arrival,
                outbound_date=outbound_date,
                return_date=return_date,
                adults=1,
                children=0,
                infants=0,
                cabin_class=cabin_class,
                currency=currency,
                region=region
            )
            params = {
                "departure_airport_code": departure.upper(),
                "arrival_airport_code": arrival.upper(),
                "departure_date": outbound_date,
                "arrival_date": return_date,
                "adults": 1,
                "children": 0,
                "infants": 0,
                "cabin_class": cabin_class,
                "currency": currency,
                "region": region,
            }
        else:
            endpoint_name = "onewaytrip"
            url = self._build_oneway_url(
                departure=departure,
                arrival=arrival,
                outbound_date=outbound_date,
                adults=1,
                children=0,
                infants=0,
                cabin_class=cabin_class,
                currency=currency,
                region=region
            )
            params = {
                "departure_airport_code": departure.upper(),
                "arrival_airport_code": arrival.upper(),
                "departure_date": outbound_date,
                "adults": 1,
                "children": 0,
                "infants": 0,
                "cabin_class": cabin_class,
                "currency": currency,
                "region": region,
            }

        cfg = get_guardian_config()
        timeout = cfg.get("flightapi_timeout_seconds", cfg.get("flightapi_timeout", 25))

        def _fetch(_p):
            sanitized_req_url = sanitize_flightapi_url(url)
            try:
                res = requests.get(url, timeout=timeout)
            except Exception as net_err:
                raise requests.exceptions.RequestException(
                    f"FlightAPI connection failure ({sanitized_req_url}): {net_err}"
                )

            # Check status codes and raise explicit errors with status_code
            if res.status_code == 401:
                err = requests.exceptions.HTTPError(
                    "FlightAPI authentication failed: Invalid or expired FLIGHTAPI_API_KEY (HTTP 401)",
                    response=res
                )
                setattr(err, "status_code", 401)
                raise err

            if res.status_code == 403:
                err = requests.exceptions.HTTPError(
                    "FlightAPI forbidden: Account or API permissions disabled (HTTP 403)",
                    response=res
                )
                setattr(err, "status_code", 403)
                raise err

            if res.status_code == 429 or is_quota_exhausted_error(res.status_code, str(res.text)):
                err = requests.exceptions.HTTPError(
                    f"FlightAPI quota exhausted: {res.text or 'HTTP 429'}",
                    response=res
                )
                setattr(err, "status_code", 429)
                raise err

            if res.status_code in (400, 422):
                err = requests.exceptions.HTTPError(
                    f"FlightAPI invalid request parameters (HTTP {res.status_code}): {res.text}",
                    response=res
                )
                setattr(err, "status_code", res.status_code)
                raise err

            if res.status_code >= 400:
                err = requests.exceptions.HTTPError(
                    f"FlightAPI upstream error (HTTP {res.status_code}): {res.text}",
                    response=res
                )
                setattr(err, "status_code", res.status_code)
                raise err

            try:
                return res.json()
            except Exception as json_err:
                raise ValueError(f"FlightAPI returned non-JSON payload: {json_err}")

        guardian_resp = guardian.execute_request(
            provider_id=self.provider_id,
            endpoint=endpoint_name,
            params=params,
            fetch_fn=_fetch,
            caller_type=CALLER_USER_SEARCH
        )

        if guardian_resp.status in ("blocked_budget", "circuit_open"):
            self.last_status = "quota_exhausted" if guardian_resp.status == "circuit_open" else "rate_limited"
            self.last_message = guardian_resp.error or "FlightAPI provider temporarily blocked by API Guardian"
            return []

        if guardian_resp.status == "error" or not guardian_resp.data:
            self.last_status = "error"
            self.last_message = guardian_resp.error or "FlightAPI query failed"
            return []

        self.last_status = "active"
        self.last_message = "FlightAPI search succeeded"

        return self._normalize_response(
            data=guardian_resp.data,
            trip_type=trip_type,
            default_departure=departure,
            default_arrival=arrival,
            is_live=guardian_resp.is_live,
            is_cached=guardian_resp.is_cached
        )

    def search_multi_trip(
        self,
        legs: List[Dict[str, Any]],
        travel_class: int = 1,
        adults: int = 1,
        children: int = 0,
        infants: int = 0,
        caller_type: str = CALLER_USER_SEARCH
    ) -> List[Dict[str, Any]]:
        """
        Execute multi-city flight search via FlightAPI.io /multitrip endpoint.
        Enforces documented route limits: strictly 3 to 5 trips.
        Returns sorted list of SkyTracker multi-city itinerary dictionaries.
        """
        if not self.is_configured():
            raise ProviderUnavailableError("FlightAPI is unconfigured. Requires FLIGHTAPI_API_KEY in environment.")

        if self.is_quota_exhausted():
            raise ProviderQuotaExhaustedError("FlightAPI quota exhausted. Upstream search suppressed.")

        num_legs = len(legs)
        if num_legs < 3 or num_legs > 5:
            raise ValueError(
                f"FlightAPI multi-trip API requires between 3 and 5 route legs as documented "
                f"(received {num_legs}). For 1 or 2 legs, please use one-way or round-trip search."
            )

        cabin_class = FLIGHTAPI_CABIN_MAP.get(travel_class, "Economy")
        region = self.region
        currency = DEFAULT_CURRENCY

        url, params = self._build_multitrip_request(
            legs=legs,
            adults=adults,
            children=children,
            infants=infants,
            cabin_class=cabin_class,
            currency=currency,
            region=region
        )

        cfg = get_guardian_config()
        timeout = cfg.get("flightapi_timeout_seconds", cfg.get("flightapi_timeout", 25))

        def _fetch(_p):
            sanitized_req_url = sanitize_flightapi_url(url)
            try:
                res = requests.get(url, params=_p, timeout=timeout)
            except Exception as net_err:
                raise requests.exceptions.RequestException(
                    f"FlightAPI connection failure ({sanitized_req_url}): {net_err}"
                )

            if res.status_code == 401:
                err = requests.exceptions.HTTPError(
                    "FlightAPI authentication failed: Invalid or expired FLIGHTAPI_API_KEY (HTTP 401)",
                    response=res
                )
                setattr(err, "status_code", 401)
                raise err

            if res.status_code == 403:
                err = requests.exceptions.HTTPError(
                    "FlightAPI forbidden: Account or API permissions disabled (HTTP 403)",
                    response=res
                )
                setattr(err, "status_code", 403)
                raise err

            if res.status_code == 429 or is_quota_exhausted_error(res.status_code, str(res.text)):
                err = requests.exceptions.HTTPError(
                    f"FlightAPI quota exhausted: {res.text or 'HTTP 429'}",
                    response=res
                )
                setattr(err, "status_code", 429)
                raise err

            if res.status_code in (400, 422):
                err = requests.exceptions.HTTPError(
                    f"FlightAPI invalid multi-city parameters (HTTP {res.status_code}): {res.text}",
                    response=res
                )
                setattr(err, "status_code", res.status_code)
                raise err

            if res.status_code >= 400:
                err = requests.exceptions.HTTPError(
                    f"FlightAPI upstream error (HTTP {res.status_code}): {res.text}",
                    response=res
                )
                setattr(err, "status_code", res.status_code)
                raise err

            try:
                return res.json()
            except Exception as json_err:
                raise ValueError(f"FlightAPI returned non-JSON payload: {json_err}")

        # Exclude key from params in guardian
        cache_params = dict(params)
        cache_params.pop("api_key", None)

        guardian_resp = guardian.execute_request(
            provider_id=self.provider_id,
            endpoint="multitrip",
            params=cache_params,
            fetch_fn=_fetch,
            caller_type=caller_type
        )

        if guardian_resp.status in ("blocked_budget", "circuit_open"):
            raise ProviderQuotaExhaustedError(guardian_resp.error or "FlightAPI request blocked by API Guardian.")

        if guardian_resp.status == "error" or not guardian_resp.data:
            raise ProviderUnavailableError(guardian_resp.error or "FlightAPI multi-trip search failed.")

        data = guardian_resp.data
        is_live = guardian_resp.is_live
        is_cached = guardian_resp.is_cached
        data_status = "cached" if is_cached else ("live" if is_live else "estimated")
        source_name = "FlightAPI (Cached)" if is_cached else "FlightAPI"

        raw_itineraries = data.get("itineraries") or []
        legs_map = _build_lookup(data.get("legs"))
        segments_map = _build_lookup(data.get("segments"))
        carriers_map = _build_lookup(data.get("carriers"))
        agents_map = _build_lookup(data.get("agents"))
        places_map = _build_lookup(data.get("places"))

        results: List[Dict[str, Any]] = []

        for itin in raw_itineraries:
            if not isinstance(itin, dict):
                continue

            itin_id = str(itin.get("id") or "")
            pricing_options = itin.get("pricing_options") or []
            if not pricing_options:
                continue

            vendor_offers: List[Dict[str, Any]] = []
            lowest_amount = float("inf")
            booking_link = ""
            primary_vendor = "FlightAPI Partner"

            for opt in pricing_options:
                if not isinstance(opt, dict):
                    continue
                price_field = opt.get("price")
                amount = None
                if isinstance(price_field, dict):
                    amount = price_field.get("amount")
                elif price_field is not None:
                    amount = price_field
                if amount is None:
                    continue
                num_amt = _safe_int(amount)
                if num_amt <= 0:
                    continue

                opt_link = ""
                items = opt.get("items") or []
                if items and isinstance(items[0], dict) and items[0].get("url"):
                    opt_link = items[0]["url"]
                elif opt.get("url"):
                    opt_link = opt["url"]

                opt_vendor = "FlightAPI Partner"
                agent_ids = opt.get("agent_ids") or []
                if agent_ids:
                    agent_obj = agents_map.get(str(agent_ids[0]))
                    if agent_obj and agent_obj.get("name"):
                        opt_vendor = agent_obj["name"]

                vendor_offers.append({
                    "provider": opt_vendor,
                    "provider_name": opt_vendor,
                    "provider_type": "ota",
                    "price": num_amt,
                    "currency": currency,
                    "booking_url": opt_link or "https://www.flightapi.io",
                    "itinerary_id": itin_id,
                })

                if num_amt < lowest_amount:
                    lowest_amount = num_amt
                    booking_link = opt_link
                    primary_vendor = opt_vendor

            if lowest_amount == float("inf"):
                continue

            leg_ids = itin.get("leg_ids") or []
            leg_details = []
            all_segments = []
            total_duration = 0
            flight_numbers = []
            primary_airline = "Airline"

            for idx, l_id in enumerate(leg_ids):
                l_obj = legs_map.get(str(l_id)) or {}
                l_dep = (
                    l_obj.get("departure_airport_code")
                    or (places_map.get(str(l_obj.get("origin_place_id", ""))) or {}).get("code")
                    or (legs[idx]["departure"] if idx < len(legs) else "")
                ).upper()
                l_arr = (
                    l_obj.get("arrival_airport_code")
                    or (places_map.get(str(l_obj.get("destination_place_id", ""))) or {}).get("code")
                    or (legs[idx]["arrival"] if idx < len(legs) else "")
                ).upper()
                l_date = legs[idx]["outbound_date"] if idx < len(legs) else ""
                l_dep_time = _extract_hhmm(l_obj.get("departure"))
                l_arr_time = _extract_hhmm(l_obj.get("arrival"))
                l_dur = _safe_int(l_obj.get("duration"), 120)
                l_stops = _safe_int(l_obj.get("stop_count"), 0)
                total_duration += l_dur

                seg_ids = l_obj.get("segment_ids") or []
                leg_airline = "Airline"
                leg_f_num = ""

                for s_id in seg_ids:
                    seg = segments_map.get(str(s_id)) or {}
                    s_dep = (
                        seg.get("departure_airport_code")
                        or (places_map.get(str(seg.get("origin_place_id", ""))) or {}).get("code")
                        or l_dep
                    )
                    s_arr = (
                        seg.get("arrival_airport_code")
                        or (places_map.get(str(seg.get("destination_place_id", ""))) or {}).get("code")
                        or l_arr
                    )
                    s_dep_t = _extract_hhmm(seg.get("departure"))
                    s_arr_t = _extract_hhmm(seg.get("arrival"))
                    s_dur = _safe_int(seg.get("duration"), 90)

                    c_id = str(seg.get("marketing_carrier_id") or "")
                    c_obj = carriers_map.get(c_id) or {}
                    s_carrier = c_obj.get("name") or leg_airline
                    s_logo = c_obj.get("logo_url") or c_obj.get("logo") or ""

                    f_no = str(seg.get("marketing_flight_number") or seg.get("flight_number") or "").strip()
                    c_code = (c_obj.get("iata") or c_obj.get("code") or "").strip()
                    if f_no and c_code and not f_no.startswith(c_code) and not f_no.startswith(s_carrier):
                        f_no = f"{c_code} {f_no}".strip()

                    if not leg_f_num and f_no:
                        leg_f_num = f_no
                    if leg_airline == "Airline" and s_carrier != "Airline":
                        leg_airline = s_carrier
                        if primary_airline == "Airline":
                            primary_airline = s_carrier

                    seg_dict = {
                        "departure_airport": s_dep,
                        "departure_name": f"{s_dep} Airport",
                        "departure_time": s_dep_t,
                        "arrival_airport": s_arr,
                        "arrival_name": f"{s_arr} Airport",
                        "arrival_time": s_arr_t,
                        "airline": s_carrier,
                        "flight_number": f_no or "Flight",
                        "duration": s_dur,
                        "airline_logo": s_logo,
                        "leg_date": l_date
                    }
                    all_segments.append(seg_dict)

                if not leg_f_num:
                    leg_f_num = f"{leg_airline} Leg {idx + 1}"
                flight_numbers.append(leg_f_num)

                leg_details.append({
                    "leg_index": idx + 1,
                    "departure_airport": l_dep,
                    "arrival_airport": l_arr,
                    "departure_date": l_date,
                    "airline": leg_airline,
                    "flight_number": leg_f_num,
                    "departure_time": l_dep_time,
                    "arrival_time": l_arr_time,
                    "duration": l_dur,
                    "stops": l_stops,
                    "price": int(lowest_amount // max(1, len(leg_ids))),
                    "segments": [s for s in all_segments if s.get("leg_date") == l_date] or all_segments
                })

            combo_flight_number = " / ".join(flight_numbers)
            first_leg = leg_details[0] if leg_details else {}
            last_leg = leg_details[-1] if leg_details else {}

            results.append({
                "trip_type": "multi_city",
                "airline": primary_airline,
                "flight_number": combo_flight_number,
                "itinerary_id": f"mc_{itin_id[:10]}",
                "price": int(lowest_amount),
                "currency": currency,
                "duration": total_duration,
                "stops": sum(l["stops"] for l in leg_details),
                "departure_time": first_leg.get("departure_time", "12:00"),
                "arrival_time": last_leg.get("arrival_time", "12:00"),
                "departure_airport": first_leg.get("departure_airport", ""),
                "arrival_airport": last_leg.get("arrival_airport", ""),
                "segments": all_segments,
                "layovers": [],
                "legs": leg_details,
                "adults": adults,
                "children": children,
                "infants": infants,
                "passengers": adults + children,
                "travel_class": travel_class,
                "is_simulated": False,
                "is_live": is_live and not is_cached,
                "data_status": data_status,
                "source": source_name,
                "booking_url": booking_link or "https://www.flightapi.io",
                "vendor": primary_vendor,
                "comparison_offers": vendor_offers
            })

        return sorted(results, key=lambda f: f["price"])
