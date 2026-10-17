import os

from .base import (
    BaseFlightProvider,
    FlightOffer,
    ItineraryGroup,
    ProviderStatus,
    ProviderQuotaExhaustedError,
    ProviderUnavailableError,
)
from .serpapi_provider import SerpApiFlightsProvider
from .amadeus_provider import AmadeusFlightProvider
from .flightapi_provider import FlightApiProvider
from .direct_airline_provider import DirectAirlineProvider
from .ota_partner_provider import OtaPartnerProvider
from .expedia_provider import ExpediaFlightProvider
from .aggregator import FlightComparisonAggregator

PROVIDER_REGISTRY = {
    "serpapi": SerpApiFlightsProvider,
    "amadeus": AmadeusFlightProvider,
    "flightapi": FlightApiProvider,
}


def get_primary_provider() -> BaseFlightProvider:
    """
    Return the configured primary flight search provider adapter.
    Configurable via PRIMARY_FLIGHT_PROVIDER (default: 'serpapi').
    Enforces legitimate, explicit provider integration without inventing or silently switching providers.
    """
    provider_name = (os.getenv("PRIMARY_FLIGHT_PROVIDER") or "serpapi").lower().strip()
    provider_cls = PROVIDER_REGISTRY.get(provider_name)
    if not provider_cls:
        raise ValueError(f"Unknown or unconfigured flight provider: '{provider_name}'")
    return provider_cls()


__all__ = [
    "BaseFlightProvider",
    "FlightOffer",
    "ItineraryGroup",
    "ProviderStatus",
    "ProviderQuotaExhaustedError",
    "ProviderUnavailableError",
    "SerpApiFlightsProvider",
    "AmadeusFlightProvider",
    "FlightApiProvider",
    "DirectAirlineProvider",
    "OtaPartnerProvider",
    "ExpediaFlightProvider",
    "FlightComparisonAggregator",
    "PROVIDER_REGISTRY",
    "get_primary_provider",
]
