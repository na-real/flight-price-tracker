from db import db, init_db
from validation import get_missing_fields, validate_target_price, clean_iata
import math
import csv
import os
import sqlite3
import sys
from datetime import datetime

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

from services.flight_api import search_flights
csv.field_size_limit(sys.maxsize)

load_dotenv()


app = Flask(__name__)

DB_PATH = os.getenv("DB_PATH", "flights.db")


# ============================================================
# GLOBAL AIRPORT DATABASE
# ============================================================

AIRPORTS_FILE = os.path.join(
    os.path.dirname(__file__),
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


load_airports()


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
# DATABASE CONNECTION
# ============================================================

def db():
    """
    Create a SQLite database connection.
    """

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    return conn

# ============================================================
# DATABASE INITIALIZATION
# ============================================================



# ============================================================
# HOME PAGE
# ============================================================

@app.get("/")
def index():

    return render_template(
        "index.html"
    )


# ============================================================
# AIRPORT SEARCH API
# ============================================================

@app.get("/api/airports")
def airport_search():

    query = (
        request.args
        .get("q", "")
        .strip()
        .lower()
    )

    iata_query = (
        request.args
        .get("iata", "")
        .strip()
        .upper()
    )

    # --------------------------------------------------------
    # EXACT IATA LOOKUP
    #
    # Example:
    # /api/airports?iata=DEL
    # --------------------------------------------------------

    if iata_query:

        for airport in airports:

            if airport["iata"] == iata_query:

                return jsonify([
                    {
                        "name":
                            airport["name"],

                        "city":
                            airport["city"],

                        "country":
                            airport["country"],

                        "iata":
                            airport["iata"],

                        "type":
                            airport["type"]
                    }
                ])

        return jsonify([])

    # --------------------------------------------------------
    # NORMAL SEARCH
    #
    # Example:
    # /api/airports?q=dehradun
    # --------------------------------------------------------

    if len(query) < 2:

        return jsonify([])

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

        iata = (
            airport["iata"]
            .lower()
        )

        if (
            query in name
            or query in city
            or query in iata
        ):

            matches.append({
                "name":
                    airport["name"],

                "city":
                    airport["city"],

                "country":
                    airport["country"],

                "iata":
                    airport["iata"],

                "type":
                    airport["type"]
            })

        # Keep autocomplete results small
        if len(matches) >= 10:
            break

    return jsonify(matches)


# ============================================================
# FLIGHT SEARCH API
# ============================================================

@app.post("/api/search")
def api_search():

    data = request.get_json() or {}

    required = [
        "departure",
        "arrival",
        "outbound_date"
    ]

    if any(
        not data.get(key)
        for key in required
    ):

        return jsonify({
            "error":
                "Departure, arrival and outbound date are required."
        }), 400

    departure = (
        str(data["departure"])
        .strip()
        .upper()
    )

    arrival = (
        str(data["arrival"])
        .strip()
        .upper()
    )

    return_date = data.get(
        "return_date"
    )

    # --------------------------------------------------------
    # NEARBY AIRPORT OPTIONS
    # --------------------------------------------------------

    departure_ids = [
        departure
    ]

    arrival_ids = [
        arrival
    ]

    if data.get("nearby_departure"):

        departure_ids = get_nearby_airports(
            departure,
            100
        )

    if data.get("nearby_arrival"):

        arrival_ids = get_nearby_airports(
            arrival,
            100
        )

    # --------------------------------------------------------
    # CONVERT AIRPORT LISTS TO SERPAPI FORMAT
    #
    # Example:
    #
    # DEL
    #
    # or:
    #
    # DEL,GAU
    # --------------------------------------------------------

    departure_search = ",".join(
        departure_ids
    )

    arrival_search = ",".join(
        arrival_ids
    )

    # --------------------------------------------------------
    # SEARCH FLIGHTS
    # --------------------------------------------------------

    try:

        travel_class = int(
            data.get(
                "travel_class",
                1
            )
        )

    except (
        TypeError,
        ValueError
    ):

        travel_class = 1

    try:

        results = search_flights(

            departure=departure_search,

            arrival=arrival_search,

            outbound_date=
                data["outbound_date"],

            return_date=
                return_date,

            travel_class=
                travel_class
        )

        return jsonify({

            "results":
                results,

            "search_airports": {

                "departure":
                    departure_ids,

                "arrival":
                    arrival_ids
            }

        })

    except Exception as e:

        print(
            "❌ Flight search error:",
            str(e)
        )

        return jsonify({
            "error": str(e)
        }), 500


# ============================================================
# TRACK FLIGHT PRICE
# ============================================================

@app.post("/api/track")
def track():

    data = request.get_json() or {}

    # --------------------------------------------------------
    # REQUIRED FIELDS
    # --------------------------------------------------------

    required_fields = [
        "departure",
        "arrival",
        "outbound_date",
        "target_price",
        "email"
    ]

    missing_fields = get_missing_fields(
        data,
        required_fields
    )

    if missing_fields:
        return jsonify({
            "error": "Please fill all tracking fields.",
            "missing": missing_fields
        }), 400

    # --------------------------------------------------------
    # VALIDATE TARGET PRICE
    # --------------------------------------------------------
    try:
        target_price = validate_target_price(
            data["target_price"]
        )
    except ValueError as error:
        return jsonify({
            "error": str(error)
        }), 400

    if target_price <= 0:

        return jsonify({
            "error":
                "Target price must be greater than zero."
        }), 400

    # --------------------------------------------------------
    # CLEAN INPUTS
    # --------------------------------------------------------
    departure = clean_iata(data["departure"])
    arrival = clean_iata(data["arrival"])

    outbound_date = (
        str(data["outbound_date"])
        .strip()
    )

    return_date = (
        data.get("return_date")
        or None
    )

    email = (
        str(data["email"])
        .strip()
    )

    # --------------------------------------------------------
    # SAVE TRACKING REQUEST
    # --------------------------------------------------------

    conn = db()

    try:

        cur = conn.execute(
            """
            INSERT INTO tracked_flights
            (
                departure,
                arrival,
                outbound_date,
                return_date,
                target_price,
                email,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                departure,
                arrival,
                outbound_date,
                return_date,
                target_price,
                email,
                datetime.utcnow().isoformat()
            )
        )

        conn.commit()

        tracking_id = cur.lastrowid

    finally:

        conn.close()

    return jsonify({

        "message":
            "Flight tracking started!",

        "id":
            tracking_id

    })
# ============================================================
# GET TRACKED FLIGHTS
# ============================================================

@app.get("/api/tracked")
def get_tracked_flights():

    conn = db()

    try:
        rows = conn.execute(
            """
            SELECT
                id,
                departure,
                arrival,
                outbound_date,
                return_date,
                target_price,
                email,
                lowest_price,
                last_price,
                created_at
            FROM tracked_flights
            ORDER BY created_at DESC
            """
        ).fetchall()

    finally:
        conn.close()

    return jsonify([
        dict(row)
        for row in rows
    ])

# ============================================================
# PRICE HISTORY
# ============================================================

@app.get("/api/history/<int:tracked_id>")
def history(tracked_id):

    conn = db()

    rows = conn.execute(
        """
        SELECT
            price,
            checked_at
        FROM price_history
        WHERE tracked_id = ?
        ORDER BY checked_at
        """,
        (tracked_id,)
    ).fetchall()

    conn.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":

    init_db()

    app.run(
        debug=True
    )