"""
Base abstractions and data models for flight price comparison providers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
import hashlib


@dataclass
class FlightOffer:
    """Represents a specific price offer from a flight provider or booking platform."""
    offer_id: str
    provider_id: str
    provider_name: str
    provider_type: str  # "airline_direct", "ota", "metasearch", "gds"
    airline: str
    flight_number: str
    departure_airport: str
    arrival_airport: str
    departure_time: str
    arrival_time: str
    duration: int  # minutes
    stops: int
    price: int  # Normalized integer price in target currency (INR)
    currency: str = "INR"
    baggage: str = "Cabin: 7 kg included"
    booking_url: str = ""
    is_best_price: bool = False
    price_diff: int = 0
    trip_type: str = "one_way"
    segments: List[Dict[str, Any]] = field(default_factory=list)
    layovers: List[Dict[str, Any]] = field(default_factory=list)
    itinerary_key: str = ""
    is_live: bool = False
    is_estimated: bool = True
    data_status: str = "estimated"  # "live", "estimated", "demonstration"
    source: str = ""
    return_airline: Optional[str] = None
    return_flight_number: Optional[str] = None
    return_departure_time: Optional[str] = None
    return_arrival_time: Optional[str] = None
    return_duration: Optional[int] = None
    return_stops: Optional[int] = None
    return_departure_airport: Optional[str] = None
    return_arrival_airport: Optional[str] = None
    return_segments: List[Dict[str, Any]] = field(default_factory=list)
    return_layovers: List[Dict[str, Any]] = field(default_factory=list)
    comparison_offers: List[Dict[str, Any]] = field(default_factory=list)
    number_of_bookable_seats: Optional[int] = None

    def __post_init__(self):
        # Generate itinerary_key if not explicitly provided
        if not self.itinerary_key:
            self.itinerary_key = self.build_itinerary_key(
                departure=self.departure_airport,
                arrival=self.arrival_airport,
                dep_time=self.departure_time,
                arr_time=self.arrival_time,
                flight_no=self.flight_number or self.airline,
                stops=self.stops,
                return_dep_time=self.return_departure_time,
                return_arr_time=self.return_arrival_time,
                return_flight_no=self.return_flight_number,
            )
        if not self.offer_id:
            raw_id = f"{self.provider_id}_{self.itinerary_key}_{self.price}"
            self.offer_id = hashlib.md5(raw_id.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def build_itinerary_key(departure: str, arrival: str, dep_time: str,
                            arr_time: str, flight_no: str, stops: int,
                            return_dep_time: Optional[str] = None,
                            return_arr_time: Optional[str] = None,
                            return_flight_no: Optional[str] = None) -> str:
        """
        Creates a deterministic signature for an itinerary so equivalent flights
        across different providers group together.
        Preserves distinct schedules: different flight numbers or departure times yield distinct keys.
        """
        dep = str(departure or "").strip().upper()
        arr = str(arrival or "").strip().upper()
        d_time = str(dep_time or "").strip()
        a_time = str(arr_time or "").strip()
        f_no = str(flight_no or "").strip().upper()
        base_key = f"{dep}_{arr}_{d_time}_{a_time}_{f_no}_{stops}"
        if return_dep_time or return_arr_time or return_flight_no:
            ret_d = str(return_dep_time or "").strip()
            ret_a = str(return_arr_time or "").strip()
            ret_f = str(return_flight_no or "").strip().upper()
            base_key += f"_ret_{ret_d}_{ret_a}_{ret_f}"
        return base_key

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ItineraryGroup:
    """
    Groups equivalent flight itineraries across providers so users can compare
    prices for the exact same flight schedule.
    """
    group_id: str
    itinerary_key: str
    airline: str
    flight_number: str
    departure_airport: str
    arrival_airport: str
    departure_time: str
    arrival_time: str
    duration: int
    stops: int
    trip_type: str
    segments: List[Dict[str, Any]]
    layovers: List[Dict[str, Any]]
    offers: List[Dict[str, Any]] = field(default_factory=list)
    lowest_price: int = 0
    highest_price: int = 0
    price_spread: int = 0
    best_offer: Optional[Dict[str, Any]] = None
    provider_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderStatus:
    """Operational status and metadata for a flight price provider."""
    provider_id: str
    provider_name: str
    provider_type: str
    status: str  # "active", "rate_limited", "unconfigured", "error"
    is_live: bool
    message: str
    offers_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ProviderQuotaExhaustedError(Exception):
    """Raised when an external flight data provider has exhausted its API quota."""
    pass


class ProviderUnavailableError(Exception):
    """Raised when an external flight data provider is unavailable or blocked."""
    pass


class BaseFlightProvider(ABC):
    """Abstract base class for all flight data sources and provider adapters."""

    def __init__(self, provider_id: str, provider_name: str, provider_type: str):
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.provider_type = provider_type

    @abstractmethod
    def is_configured(self) -> bool:
        """Check whether the necessary API keys or credentials are set."""
        pass

    @abstractmethod
    def get_status(self) -> ProviderStatus:
        """Return the current operational status of the provider."""
        pass

    def is_quota_exhausted(self) -> bool:
        """Check whether the provider is currently in a quota-exhausted state."""
        return False

    def get_quota_state(self) -> Dict[str, Any]:
        """Return provider-specific quota and circuit state information."""
        return {
            "provider_id": self.provider_id,
            "is_quota_exhausted": self.is_quota_exhausted(),
            "status": self.get_status().status
        }

    @abstractmethod
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
        Execute flight search or extract offers for the requested route and date.
        """
        pass
