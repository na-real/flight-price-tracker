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
            )
        if not self.offer_id:
            raw_id = f"{self.provider_id}_{self.itinerary_key}_{self.price}"
            self.offer_id = hashlib.md5(raw_id.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def build_itinerary_key(departure: str, arrival: str, dep_time: str,
                            arr_time: str, flight_no: str, stops: int) -> str:
        """
        Creates a deterministic signature for an itinerary so equivalent flights
        across different providers group together.
        """
        dep = str(departure or "").strip().upper()
        arr = str(arrival or "").strip().upper()
        d_time = str(dep_time or "").strip()
        a_time = str(arr_time or "").strip()
        f_no = str(flight_no or "").strip().upper()
        return f"{dep}_{arr}_{d_time}_{a_time}_{f_no}_{stops}"

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
