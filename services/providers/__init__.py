"""
Flight Provider Package
Modular architecture for multi-website flight price comparisons.
"""

from .base import (
    BaseFlightProvider,
    FlightOffer,
    ItineraryGroup,
    ProviderStatus,
)
from .serpapi_provider import SerpApiFlightsProvider
from .amadeus_provider import AmadeusFlightProvider
from .direct_airline_provider import DirectAirlineProvider
from .ota_partner_provider import OtaPartnerProvider
from .aggregator import FlightComparisonAggregator

__all__ = [
    "BaseFlightProvider",
    "FlightOffer",
    "ItineraryGroup",
    "ProviderStatus",
    "SerpApiFlightsProvider",
    "AmadeusFlightProvider",
    "DirectAirlineProvider",
    "OtaPartnerProvider",
    "FlightComparisonAggregator",
]
