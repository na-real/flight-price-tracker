import math
import csv
import os
import sys

csv.field_size_limit(sys.maxsize)


# ============================================================
# GLOBAL AIRPORT DATABASE
# ============================================================

AIRPORTS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "data",
    "airports.csv"
)

airports = []


def load_airports():
    """
    Load airports from data/airports.csv.
    Only airports with a valid IATA code are loaded.
    """

    global airports

    if not os.path.exists(AIRPORTS_FILE):
        print("❌ airports.csv not found!")
        return

    airports.clear()

    with open(
        AIRPORTS_FILE,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:

            iata = (
                row.get("iata_code") or ""
            ).strip()

            # Only keep airports with an IATA code
            if not iata:
                continue

            try:
                latitude = float(
                    row.get("latitude_deg") or 0
                )
            except (TypeError, ValueError):
                latitude = 0

            try:
                longitude = float(
                    row.get("longitude_deg") or 0
                )
            except (TypeError, ValueError):
                longitude = 0

            airports.append({
                "name": (
                    row.get("name") or ""
                ).strip(),

                "city": (
                    row.get("municipality") or ""
                ).strip(),

                "country": (
                    row.get("iso_country") or ""
                ).strip(),

                "iata": iata.upper(),

                "type": (
                    row.get("type") or ""
                ).strip(),

                "latitude": latitude,

                "longitude": longitude
            })

    print(
        f"✈️ Loaded {len(airports)} airports"
    )


def _public_airport(airport):
    return {
        "name": airport["name"],
        "city": airport["city"],
        "country": airport["country"],
        "iata": airport["iata"],
        "type": airport["type"]
    }


# ============================================================
# DISTANCE CALCULATION
# ============================================================

def calculate_distance(
    lat1,
    lon1,
    lat2,
    lon2
):
    """
    Calculate distance between two coordinates
    using the Haversine formula.

    Returns distance in kilometers.
    """

    earth_radius = 6371

    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)

    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        +
        math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return earth_radius * c


# ============================================================
# NEARBY AIRPORT SEARCH
# ============================================================

def get_nearby_airports(
    iata_code,
    radius_km=100
):
    """
    Return the selected airport plus
    the closest nearby airport within
    the specified radius.

    Example:

    DEL -> [DEL, nearby_airport]
    """

    iata_code = (
        str(iata_code or "")
        .strip()
        .upper()
    )

    selected = None

    for airport in airports:

        if airport["iata"] == iata_code:

            selected = airport
            break

    if not selected:
        return [iata_code]

    selected_lat = selected["latitude"]
    selected_lon = selected["longitude"]

    nearby = []

    for airport in airports:

        if airport["iata"] == selected["iata"]:
            continue

        if (
            not airport["latitude"]
            or not airport["longitude"]
        ):
            continue

        distance = calculate_distance(
            selected_lat,
            selected_lon,
            airport["latitude"],
            airport["longitude"]
        )

        if distance <= radius_km:

            nearby.append({
                "iata": airport["iata"],
                "distance": distance
            })

    nearby.sort(
        key=lambda x: x["distance"]
    )

    # Always include the selected airport
    result = [
        selected["iata"]
    ]

    # Add only the closest nearby airport
    if nearby:

        result.append(
            nearby[0]["iata"]
        )

    return result


# ============================================================
# AIRPORT SEARCH / LOOKUP
# ============================================================

def search_airports(q=None, iata=None):
    """
    Exact IATA lookup or text search.

    Matches the previous /api/airports behavior:
    - iata: exact match, 0 or 1 result
    - q: substring of name, city, or IATA; max 10 results
    - q shorter than 2 characters: empty list
    """

    query = str(q or "").strip().lower()
    iata_query = str(iata or "").strip().upper()

    # --------------------------------------------------------
    # EXACT IATA LOOKUP
    #
    # Example:
    # /api/airports?iata=DEL
    # --------------------------------------------------------

    if iata_query:

        for airport in airports:

            if airport["iata"] == iata_query:

                return [
                    _public_airport(airport)
                ]

        return []

    # --------------------------------------------------------
    # NORMAL SEARCH
    #
    # Example:
    # /api/airports?q=dehradun
    # --------------------------------------------------------

    if len(query) < 2:

        return []

    matches = []

    for airport in airports:

        name = (
            airport["name"]
            .lower()
        )

        city = (
            airport["city"]
            .lower()
        )

        iata_code = (
            airport["iata"]
            .lower()
        )

        if (
            query in name
            or query in city
            or query in iata_code
        ):

            matches.append(
                _public_airport(airport)
            )

        # Keep autocomplete results small
        if len(matches) >= 10:
            break

    return matches


load_airports()
