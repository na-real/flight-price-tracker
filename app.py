from db import db, init_db, DATABASE_URL
from validation import (
    get_missing_fields,
    validate_target_price,
    clean_iata,
    validate_date_sequence,
    validate_passenger_counts,
    validate_multi_city_legs,
)
import math
import hashlib
import csv
import os
import sqlite3
import sys
from datetime import datetime, timezone

from flask import Flask, render_template, request, jsonify, Response
from dotenv import load_dotenv

from services.flight_api import (
    search_flights,
    search_multi_city_flights,
    get_flight_aggregator,
    get_providers_status
)
from services.providers.base import FlightOffer
csv.field_size_limit(sys.maxsize)

load_dotenv()


app = Flask(__name__)


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

                "longitude": longitude
            })

    print(
        f"Loaded {len(airports)} airports"
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
    raw_trip_type = data.get("trip_type")
    if raw_trip_type:
        trip_type = raw_trip_type
    else:
        trip_type = "round_trip" if data.get("return_date") else "one_way"

    # --------------------------------------------------------
    # MULTI-CITY FLIGHT SEARCH
    # --------------------------------------------------------
    if trip_type == "multi_city" or data.get("legs"):
        try:
            adults, children, infants, total_passengers = validate_passenger_counts(
                data.get("adults", 1),
                data.get("children", 0),
                data.get("infants", 0)
            )
        except ValueError as p_err:
            return jsonify({"error": str(p_err)}), 400

        try:
            cleaned_legs = validate_multi_city_legs(data.get("legs", []))
        except ValueError as leg_err:
            return jsonify({"error": str(leg_err)}), 400

        try:
            travel_class = int(data.get("travel_class", 1))
        except (TypeError, ValueError):
            travel_class = 1

        all_results = search_multi_city_flights(
            legs=cleaned_legs,
            travel_class=travel_class,
            adults=adults,
            children=children,
            infants=infants
        )

        comparison_data = None
        try:
            aggregator = get_flight_aggregator()
            comparison_data = aggregator.search_and_compare(
                departure=cleaned_legs[0]["departure"],
                arrival=cleaned_legs[-1]["arrival"],
                outbound_date=cleaned_legs[0]["outbound_date"],
                travel_class=travel_class,
                reference_itineraries=all_results
            )
            if comparison_data and comparison_data.get("grouped_itineraries"):
                groups_by_key = {g["itinerary_key"]: g for g in comparison_data["grouped_itineraries"]}
                for f in all_results:
                    if not f.get("flight_number") and f.get("segments"):
                        f["flight_number"] = f["segments"][0].get("flight_number", "")
                    if not f.get("itinerary_id"):
                        itin_raw = f"{f.get('departure_airport')}_{f.get('arrival_airport')}_{f.get('airline')}_{f.get('flight_number')}_{f.get('departure_time')}_{f.get('arrival_time')}_{f.get('stops')}"
                        f["itinerary_id"] = hashlib.md5(itin_raw.encode("utf-8")).hexdigest()[:12]
                    f_key = FlightOffer.build_itinerary_key(
                        f.get("departure_airport", cleaned_legs[0]["departure"]),
                        f.get("arrival_airport", cleaned_legs[-1]["arrival"]),
                        f.get("departure_time", ""),
                        f.get("arrival_time", ""),
                        f.get("flight_number") or f.get("airline", ""),
                        f.get("stops", 0)
                    )
                    f["itinerary_key"] = f_key
                    matched_grp = groups_by_key.get(f_key)
                    if matched_grp:
                        f["comparison_offers"] = matched_grp.get("offers", [])
                        f["lowest_provider_price"] = matched_grp.get("lowest_price")
                        f["highest_provider_price"] = matched_grp.get("highest_price")
                        f["price_spread"] = matched_grp.get("price_spread", 0)
                        f["best_provider_offer"] = matched_grp.get("best_offer")
        except Exception as comp_err:
            print("Multi-city comparison aggregation error:", comp_err)

        return jsonify({
            "results": all_results,
            "trip_type": "multi_city",
            "legs": cleaned_legs,
            "passengers": {
                "adults": adults,
                "children": children,
                "infants": infants,
                "total": total_passengers
            },
            "search_airports": {
                "departure": [cleaned_legs[0]["departure"]],
                "arrival": [cleaned_legs[-1]["arrival"]]
            },
            "airport_comparisons": [],
            "cheapest_airport": None,
            "nearby_savings": {"amount": None, "percent": None},
            "comparison": comparison_data
        })

    # --------------------------------------------------------
    # ONE-WAY / ROUND-TRIP SEARCH VALIDATION
    # --------------------------------------------------------
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
    if trip_type == "one_way":
        return_date = None
    elif trip_type == "round_trip" and not return_date:
        return jsonify({"error": "Return date is required for round-trip search."}), 400

    # Date sequence validation
    try:
        validate_date_sequence(data["outbound_date"], return_date)
    except ValueError as d_err:
        return jsonify({"error": str(d_err)}), 400

    # Passenger validation
    try:
        adults, children, infants, total_passengers = validate_passenger_counts(
            data.get("adults", 1),
            data.get("children", 0),
            data.get("infants", 0)
        )
    except ValueError as p_err:
        return jsonify({"error": str(p_err)}), 400

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

        # --------------------------------------------------------
    # SEARCH EACH AIRPORT SEPARATELY
    # --------------------------------------------------------
    try:
        travel_class = int(
            data.get(
                "travel_class",
                1
            )
        )
    except (TypeError, ValueError):
        travel_class = 1
    try:
        airport_comparisons = []
        all_results = []

        for arrival_airport in arrival_ids:
            airport_results = search_flights(
                departure=departure_search,
                arrival=arrival_airport,
                outbound_date=data["outbound_date"],
                return_date=return_date,
                travel_class=travel_class
            )

            airport_results = airport_results or []

            for flight in airport_results:
                flight["search_arrival_airport"] = arrival_airport

            all_results.extend(airport_results)

            cheapest_price = None

            if airport_results:
                cheapest_price = min(
                    flight["price"]
                    for flight in airport_results
                    if flight.get("price") is not None
                )

            airport_comparisons.append({
                "airport": arrival_airport,
                "cheapest_price": cheapest_price,
                "result_count": len(airport_results)
            })

        # ----------------------------------------------------
        # FIND CHEAPEST AIRPORT
        # ----------------------------------------------------

        valid_comparisons = [
            item
            for item in airport_comparisons
            if item["cheapest_price"] is not None
        ]

        cheapest_airport = None

        if valid_comparisons:
            cheapest_airport = min(
                valid_comparisons,
                key=lambda item: item["cheapest_price"]
            )

        # ----------------------------------------------------
        # CALCULATE NEARBY SAVINGS
        # ----------------------------------------------------

        original_airport_price = next(
            (
                item["cheapest_price"]
                for item in airport_comparisons
                if item["airport"] == arrival
                and item["cheapest_price"] is not None
            ),
            None
        )

        nearby_saving = None
        nearby_saving_percent = None

        if (
            original_airport_price is not None
            and cheapest_airport is not None
            and cheapest_airport["airport"] != arrival
            and cheapest_airport["cheapest_price"] < original_airport_price
        ):
            nearby_saving = (
                original_airport_price
                - cheapest_airport["cheapest_price"]
            )

            nearby_saving_percent = round(
                (nearby_saving / original_airport_price) * 100,
                1
            )

        # ----------------------------------------------------
        # MULTI-WEBSITE PRICE COMPARISON
        # ----------------------------------------------------
        comparison_data = None
        try:
            aggregator = get_flight_aggregator()
            comparison_data = aggregator.search_and_compare(
                departure=departure,
                arrival=arrival,
                outbound_date=data["outbound_date"],
                return_date=return_date,
                travel_class=travel_class,
                reference_itineraries=all_results
            )
            if comparison_data and comparison_data.get("grouped_itineraries"):
                groups_by_key = {g["itinerary_key"]: g for g in comparison_data["grouped_itineraries"]}
                for f in all_results:
                    if not f.get("flight_number") and f.get("segments"):
                        f["flight_number"] = f["segments"][0].get("flight_number", "")
                    if not f.get("itinerary_id"):
                        itin_raw = f"{f.get('departure_airport')}_{f.get('arrival_airport')}_{f.get('airline')}_{f.get('flight_number')}_{f.get('departure_time')}_{f.get('arrival_time')}_{f.get('stops')}"
                        f["itinerary_id"] = hashlib.md5(itin_raw.encode("utf-8")).hexdigest()[:12]
                    f_key = FlightOffer.build_itinerary_key(
                        f.get("departure_airport", departure),
                        f.get("arrival_airport", arrival),
                        f.get("departure_time", ""),
                        f.get("arrival_time", ""),
                        f.get("flight_number") or f.get("airline", ""),
                        f.get("stops", 0)
                    )
                    f["itinerary_key"] = f_key
                    matched_grp = groups_by_key.get(f_key)
                    if matched_grp:
                        f["comparison_offers"] = matched_grp.get("offers", [])
                        f["lowest_provider_price"] = matched_grp.get("lowest_price")
                        f["highest_provider_price"] = matched_grp.get("highest_price")
                        f["price_spread"] = matched_grp.get("price_spread", 0)
                        f["best_provider_offer"] = matched_grp.get("best_offer")
        except Exception as comp_err:
            print("Comparison aggregation error:", comp_err)

        return jsonify({
            "results": all_results,

            "trip_type": trip_type,

            "search_airports": {
                "departure": departure_ids,
                "arrival": arrival_ids
            },

            "passengers": {
                "adults": adults,
                "children": children,
                "infants": infants,
                "total": total_passengers
            },

            "airport_comparisons": airport_comparisons,

            "cheapest_airport": cheapest_airport,

            "nearby_savings": {
                "amount": nearby_saving,
                "percent": nearby_saving_percent
            },

            "comparison": comparison_data
        })

    except Exception as e:
        print(
            "Flight search error:",
            str(e)
        )

        return jsonify({
            "error": str(e)
        }), 500


# ============================================================
# EXACT FLIGHT COMPARISON API
# ============================================================

@app.post("/api/flight/compare")
def api_flight_compare():
    data = request.get_json() or {}
    flight = data.get("flight")
    if not flight:
        return jsonify({"error": "Flight details are required."}), 400

    departure = str(data.get("departure") or flight.get("departure_airport") or "").strip().upper()
    arrival = str(data.get("arrival") or flight.get("arrival_airport") or "").strip().upper()
    outbound_date = str(data.get("outbound_date") or "").strip()
    return_date = data.get("return_date")
    try:
        travel_class = int(data.get("travel_class", 1))
    except (TypeError, ValueError):
        travel_class = 1

    try:
        aggregator = get_flight_aggregator()
        comparison_data = aggregator.search_and_compare(
            departure=departure,
            arrival=arrival,
            outbound_date=outbound_date,
            return_date=return_date,
            travel_class=travel_class,
            reference_itineraries=[flight]
        )
        response_payload = {
            "flight_summary": flight,
            "comparison_offers": comparison_data.get("all_offers", []),
            "grouped_itineraries": comparison_data.get("grouped_itineraries", []),
            "providers_summary": comparison_data.get("providers_summary", []),
            "stats": comparison_data.get("stats", {}),
            "comparison": comparison_data
        }
        return jsonify(response_payload)
    except Exception as e:
        print("Flight comparison endpoint error:", str(e))
        return jsonify({"error": str(e)}), 500


# ============================================================
# PROVIDERS METADATA API
# ============================================================
@app.get("/api/providers")
def api_providers():
    """Return status and configuration details for all flight data providers."""
    try:
        return jsonify({
            "providers": get_providers_status()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500



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
            "error": "Target price must be greater than zero."
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

        if DATABASE_URL:
            # PostgreSQL
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
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    departure,
                    arrival,
                    outbound_date,
                    return_date,
                    target_price,
                    email,
                    datetime.now(timezone.utc).isoformat()
                )
            )

            tracking_id = cur.fetchone()["id"]

        else:
            # SQLite
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
                    datetime.now(timezone.utc).isoformat()
                )
            )

            tracking_id = cur.lastrowid

        conn.commit()

    finally:
        conn.close()

    return jsonify({
        "message": "Flight tracking started!",
        "id": tracking_id
    })# ============================================================
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
# RADAR / BACKGROUND MONITORING STATUS
# ============================================================
@app.get("/api/radar/status")
def radar_status():
    """Return genuine operational status of background flight price checks."""
    conn = None
    try:
        conn = db()
        count_row = conn.execute("SELECT COUNT(*) AS total FROM tracked_flights").fetchone()
        total_tracked = count_row["total"] if count_row else 0

        last_check_row = conn.execute("SELECT MAX(checked_at) AS last_check FROM price_history").fetchone()
        last_checked = last_check_row["last_check"] if last_check_row else None

        status = "Active"
        if total_tracked == 0:
            status_detail = "Waiting for saved routes"
        elif last_checked:
            status_detail = "Automated background checks"
        else:
            status_detail = "Scheduled checks active"

        return jsonify({
            "status": status,
            "status_detail": status_detail,
            "tracked_count": total_tracked,
            "last_checked": last_checked
        })
    except Exception as exc:
        return jsonify({
            "status": "Error",
            "status_detail": "Price radar connection failed",
            "error": str(exc),
            "tracked_count": 0,
            "last_checked": None
        }), 500
    finally:
        if conn:
            conn.close()


# ============================================================
# PRICE HISTORY
# ============================================================
@app.get("/api/history/<int:tracked_id>")
def history(tracked_id):

    conn = db()

    try:

        if DATABASE_URL:
            # PostgreSQL
            rows = conn.execute(
                """
                SELECT
                    price,
                    checked_at
                FROM price_history
                WHERE tracked_id = %s
                ORDER BY checked_at
                """,
                (tracked_id,)
            ).fetchall()

        else:
            # SQLite
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

    finally:
        conn.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


# ============================================================
# DELETE TRACKED FLIGHT
# ============================================================
@app.delete("/api/tracked/<int:tracked_id>")
def delete_tracked_flight(tracked_id):

    conn = db()

    try:
        if DATABASE_URL:
            conn.execute(
                "DELETE FROM price_history WHERE tracked_id = %s",
                (tracked_id,)
            )
            conn.execute(
                "DELETE FROM tracked_flights WHERE id = %s",
                (tracked_id,)
            )
        else:
            conn.execute(
                "DELETE FROM price_history WHERE tracked_id = ?",
                (tracked_id,)
            )
            conn.execute(
                "DELETE FROM tracked_flights WHERE id = ?",
                (tracked_id,)
            )
        conn.commit()

    finally:
        conn.close()

    return jsonify({
        "message": "Tracked flight removed."
    })


# ============================================================
# SECURITY HEADERS
# ============================================================
@app.after_request
def add_security_headers(response):
    """Enforce production security headers on all responses."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    return response


# ============================================================
# TECHNICAL SEO & ROBOTS / SITEMAP
# ============================================================
@app.get("/robots.txt")
def robots_txt():
    """Serve search engine crawler directives."""
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "\n"
        "Sitemap: https://skytrack-flights.onrender.com/sitemap.xml\n"
    )
    return Response(content, mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml():
    """Serve search engine XML sitemap for legitimate indexed routes."""
    xml_content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '  <url>\n'
        '    <loc>https://skytrack-flights.onrender.com/</loc>\n'
        '    <changefreq>daily</changefreq>\n'
        '    <priority>1.0</priority>\n'
        '  </url>\n'
        '  <url>\n'
        '    <loc>https://skytrack-flights.onrender.com/privacy</loc>\n'
        '    <changefreq>monthly</changefreq>\n'
        '    <priority>0.5</priority>\n'
        '  </url>\n'
        '</urlset>'
    )
    return Response(xml_content, mimetype="application/xml")


# ============================================================
# PRIVACY POLICY ROUTE
# ============================================================
@app.get("/privacy")
def privacy():
    """Serve truthful platform privacy policy."""
    return render_template("privacy.html")


# ============================================================
# CUSTOM ERROR HANDLERS (404 & 500)
# ============================================================
@app.errorhandler(404)
def page_not_found(error):
    """Serve custom 404 page for web requests and JSON error for APIs."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "Resource not found"}), 404
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(error):
    """Serve graceful 500 page without leaking stack traces or credentials."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "An internal error occurred"}), 500
    return render_template("404.html"), 500


# ============================================================
# START APPLICATION
# ============================================================
# Initialize the database when the application starts.
init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )