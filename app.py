from db import db, init_db, DATABASE_URL
from validation import (
    get_missing_fields,
    validate_target_price,
    clean_iata,
    validate_email,
    validate_iata_code,
    resolve_airport_code,
    validate_date_sequence,
    validate_passenger_counts,
    validate_multi_city_legs,
)
from security import (
    rate_limit,
    apply_security_headers,
    generate_delete_token,
    verify_delete_token,
    check_bot_honeypot,
    SECRET_KEY,
)
import math
import hashlib
import csv
import os
import sqlite3
import sys
from urllib.parse import urlsplit
from datetime import datetime, timezone

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    Response,
    send_from_directory,
    session,
    redirect,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

from services.flight_api import (
    search_flights,
    search_multi_city_flights,
    get_flight_aggregator,
    get_providers_status
)
from services.airports import (
    search_airports,
    get_nearby_airports,
    calculate_distance,
    load_airports,
    airports,
    CITY_ALIASES
)
from services.providers.base import FlightOffer
csv.field_size_limit(sys.maxsize)

load_dotenv()


app = Flask(__name__)
# Restrict ProxyFix to required hops only; disable x_host and x_prefix to prevent Host header poisoning
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_prefix=0)
# Secret key is enforced by security._load_secret_key():
# - Mandatory and validated in production (fails startup if missing or insecure; no ephemeral secrets in prod)
# - Ephemeral fallback key is strictly restricted to local development / testing
app.config["SECRET_KEY"] = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB max request size
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (os.getenv("FLASK_ENV") == "production" or bool(os.getenv("RENDER")))


# ============================================================
# PRODUCTION SECURITY HEADERS & CORS
# ============================================================
@app.after_request
def add_security_headers(response):
    """Add defense-in-depth HTTP security headers and CORS controls to all responses."""
    return apply_security_headers(response)


# ============================================================
# GLOBAL AIRPORT DATABASE
# ============================================================
# AIRPORT SERVICES
# ============================================================
# Core airport database, haversine distance calculations, and nearby
# hub discovery are centrally managed in services.airports.


# ============================================================
# DATABASE CONNECTION
# ============================================================



# ============================================================
# DATABASE INITIALIZATION
# ============================================================



# ============================================================
# GLOBAL TEMPLATE CONTEXT
# ============================================================
@app.context_processor
def inject_global_template_context():
    return {
        "current_year": datetime.now(timezone.utc).year,
        "logged_in_user": session.get("user_email"),
        "user_name": session.get("user_name"),
    }


# ============================================================
# MULTIPAGE WEB ROUTES
# ============================================================

@app.get("/")
def index():
    return render_template("index.html", active_page="home")


@app.get("/flights")
def flights_page():
    return render_template("flights.html", active_page="flights")


@app.get("/compare")
def compare_page():
    return render_template("compare.html", active_page="compare")


@app.get("/tracking")
def tracking_page():
    return render_template("tracking.html", active_page="tracking")


@app.get("/about")
def about_page():
    return render_template("about.html", active_page="about")


@app.get("/contact")
def contact_page():
    return render_template("contact.html", active_page="contact")


@app.post("/api/contact")
@rate_limit(limit=10, window=60)
def api_contact():
    data = request.get_json(silent=True) or {}
    if check_bot_honeypot(data.get("hp_username", "")):
        return jsonify({"success": True, "message": "Inquiry submitted."}), 200

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    subject = (data.get("subject") or "General Inquiry").strip()
    message = (data.get("message") or "").strip()

    if not name or len(name) < 2 or len(name) > 100:
        return jsonify({"error": "Please provide a valid name (2-100 characters)."}), 400

    try:
        validate_email(email)
    except ValueError:
        return jsonify({"error": "Please provide a valid email address."}), 400

    if not message or len(message) < 10 or len(message) > 4000:
        return jsonify({"error": "Please provide a detailed inquiry message (10-4000 characters)."}), 400

    return jsonify({
        "success": True,
        "message": "Your transmission has been logged with the SkyTracker Concierge. We will reply within 24 hours."
    }), 200


def is_safe_redirect_url(target):
    """Ensure redirect URL is a relative local destination to prevent Open Redirects (CWE-601)."""
    if not target or not isinstance(target, str):
        return False
    target = target.strip()
    if not target.startswith("/") or target.startswith("//") or target.startswith("/\\"):
        return False
    parsed = urlsplit(target)
    return parsed.scheme in ("", "http", "https") and not parsed.netloc


@app.route("/login", methods=["GET", "POST"])
@rate_limit(limit=10, window=60, methods=("POST",))
def login_page():
    if request.method == "POST":
        data = request.get_json(silent=True) if request.is_json else request.form
        raw_email = (data.get("email") or "").strip()
        password = data.get("password") or ""
        raw_next = request.args.get("next") or (data.get("next") if isinstance(data, dict) else "") or ""
        next_url = raw_next if is_safe_redirect_url(raw_next) else url_for("dashboard_page")

        try:
            email = validate_email(raw_email).lower()
        except ValueError:
            if request.is_json:
                return jsonify({"error": "Please enter a valid email address."}), 400
            return render_template("login.html", error="Please enter a valid email address.", active_page="login"), 400

        if not password:
            if request.is_json:
                return jsonify({"error": "Password is required."}), 400
            return render_template("login.html", error="Password is required.", active_page="login"), 400

        conn = db()
        try:
            if DATABASE_URL:
                user = conn.execute(
                    "SELECT id, email, name, password_hash FROM users WHERE email = %s",
                    (email,)
                ).fetchone()
            else:
                user = conn.execute(
                    "SELECT id, email, name, password_hash FROM users WHERE email = ?",
                    (email,)
                ).fetchone()
        finally:
            conn.close()

        if not user or not check_password_hash(user["password_hash"], password):
            if request.is_json:
                return jsonify({"error": "Invalid email or security keyword."}), 401
            return render_template("login.html", error="Invalid email or security keyword.", active_page="login"), 401

        # Clear any pre-existing session state to mitigate session fixation & cross-contamination
        session.clear()
        session["user_email"] = user["email"]
        session["user_name"] = user["name"]

        if request.is_json:
            return jsonify({"success": True, "redirect": next_url}), 200
        return redirect(next_url)

    if session.get("user_email"):
        return redirect(url_for("dashboard_page"))

    return render_template("login.html", active_page="login")


@app.route("/signup", methods=["GET", "POST"])
@rate_limit(limit=5, window=60, methods=("POST",))
def signup_page():
    if request.method == "POST":
        data = request.get_json(silent=True) if request.is_json else request.form
        name = (data.get("name") or "").strip()
        raw_email = (data.get("email") or "").strip()
        password = data.get("password") or ""
        confirm_password = data.get("confirm_password") or ""

        if not name or len(name) < 2:
            if request.is_json:
                return jsonify({"error": "Please provide your full name."}), 400
            return render_template("signup.html", error="Please provide your full name.", active_page="signup"), 400

        try:
            email = validate_email(raw_email).lower()
        except ValueError:
            if request.is_json:
                return jsonify({"error": "Please provide a valid email address."}), 400
            return render_template("signup.html", error="Please provide a valid email address.", active_page="signup"), 400

        if not password or len(password) < 8:
            if request.is_json:
                return jsonify({"error": "Password must be at least 8 characters long."}), 400
            return render_template("signup.html", error="Password must be at least 8 characters long.", active_page="signup"), 400

        if password != confirm_password:
            if request.is_json:
                return jsonify({"error": "Passwords do not match."}), 400
            return render_template("signup.html", error="Passwords do not match.", active_page="signup"), 400

        conn = db()
        try:
            if DATABASE_URL:
                existing = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
            else:
                existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()

            if existing:
                if request.is_json:
                    return jsonify({"error": "An account with this email address already exists."}), 400
                return render_template("signup.html", error="An account with this email address already exists.", active_page="signup"), 400

            password_hash = generate_password_hash(password)
            created_at = datetime.now(timezone.utc).isoformat()

            if DATABASE_URL:
                conn.execute(
                    "INSERT INTO users (email, name, password_hash, created_at) VALUES (%s, %s, %s, %s)",
                    (email, name, password_hash, created_at)
                )
            else:
                conn.execute(
                    "INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (email, name, password_hash, created_at)
                )
            conn.commit()
        finally:
            conn.close()

        # Clear session before setting newly registered user identity
        session.clear()
        session["user_email"] = email
        session["user_name"] = name

        if request.is_json:
            return jsonify({"success": True, "redirect": url_for("dashboard_page")}), 200
        return redirect(url_for("dashboard_page"))

    if session.get("user_email"):
        return redirect(url_for("dashboard_page"))

    return render_template("signup.html", active_page="signup")


@app.post("/logout")
def logout_page():
    session.clear()
    if request.is_json:
        return jsonify({"success": True, "redirect": url_for("index")}), 200
    return redirect(url_for("index"))



@app.get("/dashboard")
def dashboard_page():
    if not session.get("user_email"):
        return redirect(url_for("login_page", next=request.path))
    return render_template("dashboard.html", active_page="dashboard")


@app.get("/terms")
def terms_page():
    return render_template("terms.html", active_page="terms")


# ============================================================
# AIRPORT SEARCH API
# ============================================================

@app.get("/api/airports")
@rate_limit(limit=100, window=60)
def airport_search():
    query = request.args.get("q", "").strip()[:50]
    iata_query = request.args.get("iata", "").strip()[:3].upper()
    return jsonify(search_airports(q=query, iata=iata_query))


# ============================================================
# FLIGHT SEARCH API
# ============================================================

@app.post("/api/search")
@rate_limit(limit=40, window=60)
def api_search():

    data = request.get_json() or {}
    try:
        check_bot_honeypot(data)
    except ValueError as bot_err:
        return jsonify({"error": str(bot_err)}), 400

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

        raw_class = data.get("travel_class", 1)
        if isinstance(raw_class, str):
            class_map = {
                "1": 1, "2": 2, "3": 3, "4": 4,
                "economy": 1, "premium_economy": 2, "business": 3, "first": 4
            }
            travel_class = class_map.get(raw_class.lower(), 1)
        else:
            try:
                travel_class = int(raw_class)
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

    try:
        departure = resolve_airport_code(data["departure"], "Departure airport")
        arrival = resolve_airport_code(data["arrival"], "Arrival airport")
    except ValueError as iata_err:
        return jsonify({"error": str(iata_err)}), 400

    if departure == arrival:
        return jsonify({"error": "Departure and arrival airports cannot be identical."}), 400

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
    raw_class = data.get("travel_class", 1)
    if isinstance(raw_class, str):
        class_map = {
            "1": 1, "2": 2, "3": 3, "4": 4,
            "economy": 1, "premium_economy": 2, "business": 3, "first": 4
        }
        travel_class = class_map.get(raw_class.lower(), 1)
    else:
        try:
            travel_class = int(raw_class)
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
        app.logger.error("Flight search error: %s", e)
        return jsonify({
            "error": "An unexpected error occurred while searching for flights. Please try again."
        }), 500


# ============================================================
# EXACT FLIGHT COMPARISON API
# ============================================================

@app.post("/api/flight/compare")
@rate_limit(limit=40, window=60)
def api_flight_compare():
    data = request.get_json() or {}
    try:
        check_bot_honeypot(data)
    except ValueError as bot_err:
        return jsonify({"error": str(bot_err)}), 400

    flight = data.get("flight")

    if flight:
        departure = str(data.get("departure") or flight.get("departure_airport") or "").strip().upper()
        arrival = str(data.get("arrival") or flight.get("arrival_airport") or "").strip().upper()
        outbound_date = str(data.get("outbound_date") or data.get("departure_date") or "").strip()
        return_date = data.get("return_date")
        reference_itineraries = [flight]
    else:
        # Route-based comparison for standalone /compare page
        raw_dep = data.get("departure")
        raw_arr = data.get("arrival")
        raw_outbound = data.get("outbound_date") or data.get("departure_date")
        if not raw_dep or not raw_arr or not raw_outbound:
            return jsonify({"error": "Departure, arrival, and outbound date are required."}), 400

        try:
            departure = resolve_airport_code(raw_dep, "Departure airport")
            arrival = resolve_airport_code(raw_arr, "Arrival airport")
        except ValueError as iata_err:
            return jsonify({"error": str(iata_err)}), 400

        if departure == arrival:
            return jsonify({"error": "Departure and arrival airports cannot be identical."}), 400

        outbound_date = str(raw_outbound).strip()
        return_date = data.get("return_date")
        try:
            validate_date_sequence(outbound_date, return_date)
        except ValueError as d_err:
            return jsonify({"error": str(d_err)}), 400

        # Query authentic base flights to serve as reference itineraries
        base_flights = search_flights(
            departure=departure,
            arrival=arrival,
            outbound_date=outbound_date,
            return_date=return_date
        )
        reference_itineraries = base_flights if base_flights else None

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
            reference_itineraries=reference_itineraries
        )

        all_offers = comparison_data.get("all_offers", [])
        # Ensure offers contain both 'provider' and 'provider_name' for UI template compatibility
        for offer in all_offers:
            if "provider" not in offer:
                offer["provider"] = offer.get("provider_name", "Carrier")

        response_payload = {
            "flight_summary": flight or {
                "departure_airport": departure,
                "arrival_airport": arrival,
                "outbound_date": outbound_date,
                "return_date": return_date,
            },
            "offers": all_offers,
            "comparison_offers": all_offers,
            "grouped_itineraries": comparison_data.get("grouped_itineraries", []),
            "providers_summary": comparison_data.get("providers_summary", []),
            "stats": comparison_data.get("stats", {}),
            "comparison": comparison_data
        }
        return jsonify(response_payload)
    except Exception as e:
        app.logger.error("Flight comparison endpoint error: %s", e)
        return jsonify({"error": "An error occurred while comparing flights across providers."}), 500



# ============================================================
# PROVIDERS METADATA API
# ============================================================
@app.get("/api/providers")
def api_providers():
    """Return status and configuration details for all flight data providers."""
    try:
        raw_status = get_providers_status()
        providers_list = []
        for p in raw_status:
            item = dict(p) if isinstance(p, dict) else (p.to_dict() if hasattr(p, "to_dict") else vars(p))
            item["name"] = item.get("provider_name") or item.get("provider_id", "Provider")
            providers_list.append(item)
        return jsonify({
            "providers": providers_list
        })
    except Exception as e:
        app.logger.error("Providers metadata endpoint error: %s", e)
        return jsonify({"error": "Unable to retrieve flight data providers status."}), 500



# ============================================================
# TRACK FLIGHT PRICE
# ============================================================
@app.post("/api/track")
@rate_limit(limit=15, window=60)
def track():

    data = request.get_json() or {}

    # Bot protection
    try:
        check_bot_honeypot(data)
    except ValueError as bot_err:
        return jsonify({"error": str(bot_err)}), 400

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
    # VALIDATE EMAIL & AIRPORTS
    # --------------------------------------------------------

    try:
        email = validate_email(data.get("email"))
    except ValueError as err:
        return jsonify({"error": str(err)}), 400

    try:
        departure = validate_iata_code(data.get("departure"), "Departure airport")
        arrival = validate_iata_code(data.get("arrival"), "Arrival airport")
    except ValueError as err:
        return jsonify({"error": str(err)}), 400

    if departure == arrival:
        return jsonify({"error": "Departure and arrival airports cannot be identical."}), 400

    outbound_date = (
        str(data["outbound_date"])
        .strip()
    )

    return_date = (
        data.get("return_date")
        or None
    )

    try:
        validate_date_sequence(outbound_date, return_date)
    except ValueError as d_err:
        return jsonify({"error": str(d_err)}), 400

    # --------------------------------------------------------
    # SAVE TRACKING REQUEST
    # --------------------------------------------------------

    created_at = datetime.now(timezone.utc).isoformat()
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
                    created_at
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
                    created_at
                )
            )

            tracking_id = cur.lastrowid

        conn.commit()

    finally:
        conn.close()

    delete_token = generate_delete_token(tracking_id, created_at)

    return jsonify({
        "message": "Flight tracking started!",
        "id": tracking_id,
        "delete_token": delete_token
    })# ============================================================
# GET TRACKED FLIGHTS
# ============================================================

@app.get("/api/tracked")
def get_tracked_flights():
    user_email = session.get("user_email")
    if not user_email:
        # Prevent unauthenticated users from snooping on other users' private tracking data
        return jsonify([])

    conn = db()

    try:
        if DATABASE_URL:
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
                WHERE LOWER(email) = LOWER(%s)
                ORDER BY created_at DESC
                """,
                (user_email,)
            ).fetchall()
        else:
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
                WHERE LOWER(email) = LOWER(?)
                ORDER BY created_at DESC
                """,
                (user_email,)
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
        app.logger.error("Price radar status failed: %s", exc)
        return jsonify({
            "status": "Error",
            "status_detail": "Price radar connection failed",
            "error": "Service temporarily unavailable",
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
@rate_limit(limit=60, window=60)
def history(tracked_id):

    conn = db()

    try:
        # Verify flight exists and fetch ownership metadata
        if DATABASE_URL:
            flight = conn.execute(
                "SELECT id, email, created_at FROM tracked_flights WHERE id = %s",
                (tracked_id,)
            ).fetchone()
        else:
            flight = conn.execute(
                "SELECT id, email, created_at FROM tracked_flights WHERE id = ?",
                (tracked_id,)
            ).fetchone()

        if not flight:
            return jsonify({"error": "Tracked flight not found."}), 404

        # Enforce tenancy / authorization:
        # 1. Authenticated user matching the normalized email
        user_email = session.get("user_email")
        is_owner = bool(user_email and flight["email"].strip().lower() == user_email.strip().lower())

        # 2. Or unauthenticated/token-authorized access via record-specific HMAC token in request header
        raw_token = request.headers.get("X-Delete-Token") or request.headers.get("X-Auth-Token")
        token = raw_token.strip() if raw_token and isinstance(raw_token, str) else ""
        is_token_valid = bool(token and verify_delete_token(tracked_id, flight["created_at"], token))

        if not is_owner and not is_token_valid:
            return jsonify({"error": "Unauthorized access to this flight history."}), 403

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
# DELETE TRACKED FLIGHT (IDOR & AUTHORIZATION HARDENING)
# ============================================================
@app.delete("/api/tracked/<int:tracked_id>")
@rate_limit(limit=30, window=60)
def delete_tracked_flight(tracked_id):

    conn = db()

    try:
        if DATABASE_URL:
            row = conn.execute(
                "SELECT id, created_at FROM tracked_flights WHERE id = %s",
                (tracked_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, created_at FROM tracked_flights WHERE id = ?",
                (tracked_id,)
            ).fetchone()

        if not row:
            return jsonify({
                "error": "Tracked flight not found."
            }), 404

        # Validate authorization token - MUST be provided via X-Delete-Token header only
        raw_token = request.headers.get("X-Delete-Token")
        token = raw_token.strip() if raw_token and isinstance(raw_token, str) else ""

        if not token:
            return jsonify({
                "error": "Authorization token required to delete this flight."
            }), 403

        if not verify_delete_token(tracked_id, row["created_at"], token):
            return jsonify({
                "error": "Invalid authorization token for this flight."
            }), 403

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
        "message": "Tracked flight removed.",
        "id": tracked_id
    })


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
    routes = [
        ("/", "daily", "1.0"),
        ("/flights", "daily", "0.9"),
        ("/compare", "daily", "0.8"),
        ("/tracking", "daily", "0.8"),
        ("/about", "monthly", "0.7"),
        ("/contact", "monthly", "0.6"),
        ("/privacy", "monthly", "0.5"),
        ("/terms", "monthly", "0.5"),
    ]
    urls_xml = "".join([
        f"  <url>\n    <loc>https://skytrack-flights.onrender.com{path}</loc>\n    <changefreq>{freq}</changefreq>\n    <priority>{prio}</priority>\n  </url>\n"
        for path, freq, prio in routes
    ])
    xml_content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'{urls_xml}'
        '</urlset>'
    )
    return Response(xml_content, mimetype="application/xml")


# ============================================================
# PRIVACY POLICY ROUTE
# ============================================================
@app.get("/privacy")
def privacy():
    """Serve truthful platform privacy policy."""
    return render_template("privacy.html", active_page="privacy")


# ============================================================
# FAVICON ROUTE
# ============================================================
@app.get("/favicon.ico")
def favicon():
    """Serve brand favicon."""
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon"
    )


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
