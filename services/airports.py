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
        print("airports.csv not found!")
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

                "longitude": longitude,

                "keywords": (
                    row.get("keywords") or ""
                ).strip()
            })

    print(
        f"Loaded {len(airports)} airports"
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
# CANONICAL CITY ALIASES
# ============================================================

CITY_ALIASES = {
    "bombay": "BOM",
    "mumbai": "BOM",
    "madras": "MAA",
    "chennai": "MAA",
    "calcutta": "CCU",
    "kolkata": "CCU",
    "bangalore": "BLR",
    "bengaluru": "BLR",
    "cochin": "COK",
    "kochi": "COK",
    "delhi": "DEL",
    "new delhi": "DEL",
    "peking": "PEK",
    "beijing": "PEK",
    "saigon": "SGN",
    "ho chi minh": "SGN",
    "canton": "CAN",
    "guangzhou": "CAN"
}


# ============================================================
# AIRPORT SEARCH / LOOKUP
# ============================================================

def search_airports(q=None, iata=None):
    """
    Exact IATA lookup or ranked text search with alias and keyword support.

    - iata: exact match, 0 or 1 result
    - q: ranked search across IATA, city aliases, city name, keywords, and airport name
    - q shorter than 2 characters: empty list
    """

    query = str(q or "").strip().lower()
    iata_query = str(iata or "").strip().upper()

    # --------------------------------------------------------
    # EXACT IATA LOOKUP
    # --------------------------------------------------------
    if iata_query:
        for airport in airports:
            if airport["iata"] == iata_query:
                return [
                    _public_airport(airport)
                ]
        return []

    # --------------------------------------------------------
    # RANKED SEARCH WITH ALIASES & KEYWORDS
    # --------------------------------------------------------
    if len(query) < 2:
        return []

    scored = []
    for airport in airports:
        score = 0
        iata_lower = airport["iata"].lower()
        city_lower = airport["city"].lower()
        name_lower = airport["name"].lower()
        kw_lower = airport.get("keywords", "").lower()
        kw_list = [k.strip() for k in kw_lower.split(",") if k.strip()]

        # 1. Exact or prefix IATA code match
        if query == iata_lower:
            score += 1000
        elif iata_lower.startswith(query):
            score += 500

        # 2. City Alias match (e.g. Bombay -> BOM, Madras -> MAA)
        if query in CITY_ALIASES and CITY_ALIASES[query] == airport["iata"]:
            score += 800
        elif any(alias.startswith(query) and code == airport["iata"] for alias, code in CITY_ALIASES.items()):
            score += 400

        # 3. City name match
        if city_lower == query:
            score += 400
        elif city_lower.startswith(query):
            score += 300
        elif query in city_lower:
            score += 150

        # 4. Keywords match (from CSV)
        if any(k == query for k in kw_list):
            score += 350
        elif any(k.startswith(query) for k in kw_list):
            score += 250
        elif query in kw_lower:
            score += 100

        # 5. Airport name match
        if name_lower.startswith(query):
            score += 200
        elif query in name_lower:
            score += 100

        if score > 0:
            if airport["type"] == "large_airport":
                score += 80
            elif airport["type"] == "medium_airport":
                score += 40
            elif airport["type"] == "small_airport":
                score += 10

            scored.append((score, airport))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [_public_airport(x[1]) for x in scored[:10]]


load_airports()
