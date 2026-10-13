"""
Flight Price Comparison Aggregator.
Orchestrates multi-provider search, normalizes fares, eliminates duplicates,
groups equivalent itineraries, tags best prices, and generates status summaries.
"""

from typing import List, Dict, Any, Optional
from collections import defaultdict
import hashlib

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
from .expedia_provider import ExpediaFlightProvider


# Currency exchange approximations to target currency (INR)
CURRENCY_RATES = {
    "INR": 1.0,
    "USD": 86.5,
    "EUR": 93.0,
    "GBP": 110.0,
    "AED": 23.5,
    "SGD": 65.0,
}


class FlightComparisonAggregator:
    """Central engine that queries providers, groups itineraries, and picks best prices."""

    def __init__(self, providers: Optional[List[BaseFlightProvider]] = None):
        if providers is not None:
            self.providers = providers
        else:
            self.providers = [
                SerpApiFlightsProvider(),
                AmadeusFlightProvider(),
                DirectAirlineProvider(),
                OtaPartnerProvider(),
                ExpediaFlightProvider(),
            ]

    def register_provider(self, provider: BaseFlightProvider):
        """Add a provider adapter to the aggregator."""
        self.providers.append(provider)

    def get_providers_status(self) -> List[Dict[str, Any]]:
        """Return operational and configuration status for all registered providers."""
        return [p.get_status().to_dict() for p in self.providers]

    def normalize_price(self, price: Any, currency: str = "INR") -> int:
        """
        Normalize price value to integer INR, converting foreign currencies if needed.
        """
        try:
            numeric_price = float(price)
        except (TypeError, ValueError):
            return 0

        curr = (currency or "INR").upper().strip()
        rate = CURRENCY_RATES.get(curr, 1.0)
        inr_price = numeric_price * rate
        return max(0, int(round(inr_price)))

    def deduplicate_offers(self, offers: List[FlightOffer]) -> List[FlightOffer]:
        """
        Remove redundant duplicate offers from the same provider with the same itinerary and fare.
        """
        seen = set()
        unique_offers = []
        for offer in offers:
            key = f"{offer.provider_id}:{offer.itinerary_key}:{offer.price}"
            if key not in seen:
                seen.add(key)
                unique_offers.append(offer)
        return unique_offers

    def group_itineraries(self, offers: List[FlightOffer]) -> List[ItineraryGroup]:
        """
        Group equivalent itineraries across providers so users can compare
        prices for the exact same flight schedule.
        Highlights the lowest comparable total price with `is_best_price = True`.
        """
        groups_dict: Dict[str, List[FlightOffer]] = defaultdict(list)

        for offer in offers:
            groups_dict[offer.itinerary_key].append(offer)

        grouped_itineraries: List[ItineraryGroup] = []

        for itinerary_key, group_offers in groups_dict.items():
            if not group_offers:
                continue

            # Sort group offers ascending by price
            group_offers.sort(key=lambda o: o.price)

            lowest_price = group_offers[0].price
            highest_price = group_offers[-1].price
            price_spread = highest_price - lowest_price

            # Mark best price on the lowest offer(s) and compute price differences
            for idx, offer in enumerate(group_offers):
                if offer.price == lowest_price:
                    offer.is_best_price = True
                    offer.price_diff = 0
                else:
                    offer.is_best_price = False
                    offer.price_diff = offer.price - lowest_price

            representative = group_offers[0]
            group_id = hashlib.md5(itinerary_key.encode("utf-8")).hexdigest()[:10]

            itinerary_group = ItineraryGroup(
                group_id=group_id,
                itinerary_key=itinerary_key,
                airline=representative.airline,
                flight_number=representative.flight_number,
                departure_airport=representative.departure_airport,
                arrival_airport=representative.arrival_airport,
                departure_time=representative.departure_time,
                arrival_time=representative.arrival_time,
                duration=representative.duration,
                stops=representative.stops,
                trip_type=representative.trip_type,
                segments=representative.segments,
                layovers=representative.layovers,
                offers=[o.to_dict() for o in group_offers],
                lowest_price=lowest_price,
                highest_price=highest_price,
                price_spread=price_spread,
                best_offer=group_offers[0].to_dict(),
                provider_count=len(set(o.provider_id for o in group_offers))
            )
            grouped_itineraries.append(itinerary_group)

        # Sort groups by their lowest price
        grouped_itineraries.sort(key=lambda g: g.lowest_price)
        return grouped_itineraries

    def search_and_compare(
        self,
        departure: str,
        arrival: str,
        outbound_date: str,
        return_date: Optional[str] = None,
        travel_class: int = 1,
        reference_itineraries: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Execute full multi-provider flight search and comparison analysis.
        """
        all_raw_offers: List[FlightOffer] = []

        # 1. Query all registered providers
        for provider in self.providers:
            try:
                provider_offers = provider.search(
                    departure=departure,
                    arrival=arrival,
                    outbound_date=outbound_date,
                    return_date=return_date,
                    travel_class=travel_class,
                    reference_itineraries=reference_itineraries
                )
                if provider_offers:
                    # Normalize prices and currency
                    for offer in provider_offers:
                        if offer.currency != "INR":
                            offer.price = self.normalize_price(offer.price, offer.currency)
                            offer.currency = "INR"
                    all_raw_offers.extend(provider_offers)
            except Exception as e:
                print(f"Provider {provider.provider_id} encountered an error: {e}")

        # 2. Deduplicate
        unique_offers = self.deduplicate_offers(all_raw_offers)

        # 3. Group equivalent itineraries and identify best prices
        grouped_itineraries = self.group_itineraries(unique_offers)

        # 4. Compute overall statistics
        lowest_overall = min((g.lowest_price for g in grouped_itineraries), default=0)
        highest_overall = max((g.highest_price for g in grouped_itineraries), default=0)
        max_savings = max((g.price_spread for g in grouped_itineraries), default=0)

        providers_summary = self.get_providers_status()
        active_count = sum(1 for p in providers_summary if p["status"] == "active")
        total_queried = len(providers_summary)

        return {
            "grouped_itineraries": [g.to_dict() for g in grouped_itineraries],
            "all_offers": [o.to_dict() for o in unique_offers],
            "providers_summary": providers_summary,
            "stats": {
                "total_offers": len(unique_offers),
                "total_itineraries": len(grouped_itineraries),
                "lowest_price": lowest_overall,
                "highest_price": highest_overall,
                "max_savings": max_savings,
                "active_providers_count": active_count,
                "total_providers_count": total_queried,
            }
        }
