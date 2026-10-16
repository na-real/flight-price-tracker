import os
import hashlib
import requests
from dotenv import load_dotenv

from services.api_guardian import (
    guardian,
    PROVIDER_SERPAPI,
    CALLER_USER_SEARCH,
    CALLER_BACKGROUND_CHECK,
    get_guardian_config,
)











# =========================================================



# LOAD ENVIRONMENT VARIABLES



# =========================================================







load_dotenv()







SERPAPI_URL = "https://serpapi.com/search.json"











# =========================================================



# HELPER: CLEAN TIME



# =========================================================







def clean_time(time_value):



    """



    Convert a SerpApi datetime such as:







        2026-10-10 17:15







    into:







        17:15



    """







    if not time_value:



        return ""







    time_value = str(time_value)







    if " " in time_value:



        return time_value.split(" ")[-1]







    return time_value











# =========================================================



# HELPER: FORMAT AIRPORT



# =========================================================







def extract_airport(airport):



    """



    Extract useful airport information from a SerpApi



    airport object.



    """







    airport = airport or {}







    return {



        "id": airport.get("id", ""),



        "name": airport.get("name", ""),



        "time": airport.get("time", "")



    }











# =========================================================



# HELPER: BUILD SEGMENTS



# =========================================================







def build_segments(raw_segments):



    """



    Preserve every individual flight segment.







    Example:







        DEL → LKO



        LKO → MAA



    """







    segments = []







    for segment in raw_segments:







        departure_airport = extract_airport(



            segment.get("departure_airport")



        )







        arrival_airport = extract_airport(



            segment.get("arrival_airport")



        )







        segments.append({







            # -----------------------------------------



            # AIRPORTS



            # -----------------------------------------







            "departure_airport":



                departure_airport.get("id", ""),







            "departure_name":



                departure_airport.get("name", ""),







            "departure_time":



                clean_time(



                    departure_airport.get("time", "")



                ),







            "arrival_airport":



                arrival_airport.get("id", ""),







            "arrival_name":



                arrival_airport.get("name", ""),







            "arrival_time":



                clean_time(



                    arrival_airport.get("time", "")



                ),







            # -----------------------------------------



            # FLIGHT DETAILS



            # -----------------------------------------







            "airline":



                segment.get(



                    "airline",



                    "Unknown Airline"



                ),







            "flight_number":



                segment.get(



                    "flight_number",



                    ""



                ),







            "duration":



                int(



                    segment.get(



                        "duration",



                        0



                    ) or 0



                ),







            "airline_logo":



                segment.get(



                    "airline_logo",



                    ""



                )



        })







    return segments











# =========================================================



# HELPER: BUILD LAYOVERS



# =========================================================







def build_layovers(itinerary, segments):



    """



    Preserve detailed layover information.







    SerpApi can provide layovers directly through the



    itinerary's 'layovers' array.







    If that information is unavailable, we calculate



    the layover duration from adjacent segments.



    """







    raw_layovers = itinerary.get(



        "layovers",



        []



    )







    layovers = []











    # =====================================================



    # USE SERPAPI LAYOVER DATA WHEN AVAILABLE



    # =====================================================







    for i, layover in enumerate(raw_layovers):







        layover_id = layover.get(



            "id",



            ""



        )







        layover_name = layover.get(



            "name",



            ""



        )







        layover_duration = layover.get(



            "duration"



        )







        overnight = layover.get(



            "overnight",



            False



        )











        # -------------------------------------------------



        # If duration isn't supplied, calculate it



        # from the surrounding segments.



        # -------------------------------------------------







        if layover_duration is None and i < len(segments) - 1:







            first_arrival = (



                segments[i]["arrival_time"]



            )







            next_departure = (



                segments[i + 1]["departure_time"]



            )







            # We cannot reliably calculate across dates



            # from time-only strings, so leave it as 0.



            layover_duration = 0











        layovers.append({







            "id":



                layover_id,







            "name":



                layover_name,







            "duration":



                int(



                    layover_duration or 0



                ),







            "overnight":



                bool(overnight)







        })











    # =====================================================



    # FALLBACK



    # =====================================================







    # Some responses may not provide the itinerary-level



    # layovers array.



    #



    # In that case, create layovers from the segments.







    if not layovers and len(segments) > 1:







        for i in range(



            len(segments) - 1



        ):







            current_segment = segments[i]







            next_segment = segments[i + 1]











            layover_id = (



                current_segment[



                    "arrival_airport"



                ]



            )







            layover_name = (



                current_segment[



                    "arrival_name"



                ]



            )











            # -------------------------------------------------



            # Try to calculate duration when timestamps



            # are available.



            # -------------------------------------------------







            duration_minutes = 0







            try:







                from datetime import datetime







                arrival_time = datetime.strptime(



                    current_segment[



                        "arrival_time"



                    ],



                    "%H:%M"



                )







                departure_time = datetime.strptime(



                    next_segment[



                        "departure_time"



                    ],



                    "%H:%M"



                )











                duration_minutes = int(



                    (



                        departure_time



                        - arrival_time



                    ).total_seconds()



                    / 60



                )











                # Handle overnight connection



                if duration_minutes < 0:







                    duration_minutes += 24 * 60











            except Exception:







                duration_minutes = 0











            layovers.append({







                "id":



                    layover_id,







                "name":



                    layover_name,







                "duration":



                    duration_minutes,







                "overnight":



                    duration_minutes >= 12 * 60







            })











    return layovers











# =========================================================



# SEARCH FLIGHTS



# =========================================================
# SEARCH FLIGHTS
# =========================================================

def _collect_itineraries(data):
    itineraries = []
    itineraries.extend(data.get("best_flights", []) or [])
    itineraries.extend(data.get("other_flights", []) or [])
    return itineraries

def _normalize_itinerary(itinerary):
    raw_segments = itinerary.get("flights", []) or []
    segments = build_segments(raw_segments)
    if not segments:
        return None
    stops = max(len(segments) - 1, 0)
    airlines = []
    for segment in segments:
        airline = segment.get("airline")
        if airline and airline not in airlines:
            airlines.append(airline)
    airline_name = " + ".join(airlines) if airlines else "Unknown Airline"
    first_segment = segments[0]
    last_segment = segments[-1]
    price = itinerary.get("price")
    if price is None:
        return None
    duration = itinerary.get("total_duration")
    if duration is None:
        duration = sum(segment.get("duration", 0) or 0 for segment in segments)
    flight_number = first_segment.get("flight_number", "")
    itin_raw = f"{first_segment.get('departure_airport')}_{last_segment.get('arrival_airport')}_{airline_name}_{flight_number}_{first_segment.get('departure_time')}_{last_segment.get('arrival_time')}_{stops}"
    itinerary_id = hashlib.md5(itin_raw.encode("utf-8")).hexdigest()[:12]
    return {
        "airline": airline_name,
        "flight_number": flight_number,
        "itinerary_id": itinerary_id,
        "price": int(price),
        "currency": "INR",
        "duration": int(duration or 0),
        "stops": stops,
        "departure_time": first_segment.get("departure_time", ""),
        "arrival_time": last_segment.get("arrival_time", ""),
        "departure_airport": first_segment.get("departure_airport", ""),
        "arrival_airport": last_segment.get("arrival_airport", ""),
        "segments": segments,
        "layovers": build_layovers(itinerary, segments),
        "departure_token": itinerary.get("departure_token", ""),
        "booking_token": itinerary.get("booking_token", ""),
        "is_simulated": False,
        "is_live": True,
        "data_status": "live",
        "source": "Google Flights via SerpAPI"
    }

def _raw_serpapi_fetch(params):
    cfg = get_guardian_config()
    timeout = cfg.get("serpapi_timeout", 25)
    response = requests.get(SERPAPI_URL, params=params, timeout=timeout)
    data = response.json() if response.content else {}

    if response.status_code == 429 or "search limit reached" in str(data.get("error", "")).lower():
        err = Exception(data.get("error") or "SerpAPI account search limit reached (HTTP 429)")
        setattr(err, "status_code", 429)
        setattr(err, "response", response)
        raise err

    if response.status_code >= 400:
        err = Exception(data.get("error") or f"SerpAPI request error (HTTP {response.status_code})")
        setattr(err, "status_code", response.status_code)
        setattr(err, "response", response)
        raise err

    if data.get("error"):
        err = Exception(data["error"])
        setattr(err, "status_code", 400)
        raise err

    return data


def _serpapi_request(params, caller_type=CALLER_USER_SEARCH):
    resp = guardian.execute_request(
        provider_id=PROVIDER_SERPAPI,
        endpoint="search.json",
        params=params,
        fetch_fn=_raw_serpapi_fetch,
        caller_type=caller_type
    )
    if resp.status not in ("fresh", "cached") or not resp.data:
        raise Exception(resp.error or "SerpAPI request blocked by Quota Guardian or unavailable")
    return resp.data


def _get_cheapest_one_way_price(
    departure,
    arrival,
    travel_date,
    travel_class=1,
    caller_type=CALLER_USER_SEARCH
):
    """
    Get the cheapest standalone one-way fare for a route/date.
    This is a REFERENCE fare only.
    It is not assumed to be one half of a round-trip ticket.
    """
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        return None

    params = {
        "engine": "google_flights",
        "api_key": api_key,
        "departure_id": departure.upper(),
        "arrival_id": arrival.upper(),
        "outbound_date": travel_date,
        "type": "2",
        "travel_class": travel_class,
        "currency": "INR",
        "hl": "en",
        "gl": "in",
        "show_hidden": "true"
    }

    try:
        data = _serpapi_request(params, caller_type=caller_type)
    except Exception as e:
        print(f"Reference one-way fare query notice: {e}")
        return None

    prices = []
    for itinerary in _collect_itineraries(data):
        flight = _normalize_itinerary(itinerary)
        if flight and flight.get("price") is not None:
            try:
                prices.append(int(flight["price"]))
            except (TypeError, ValueError):
                pass

    return min(prices) if prices else None


def search_flights(
    departure,
    arrival,
    outbound_date,
    return_date=None,
    travel_class=1,
    caller_type=CALLER_USER_SEARCH,
    force_refresh=False
):
    dep = str(departure or "").strip().upper()
    arr = str(arrival or "").strip().upper()
    out_date = str(outbound_date or "").strip()
    ret_date = str(return_date).strip() if return_date else None
    t_class = int(travel_class or 1)

    # 1. High-Level Route Cache Check (Guarantees zero upstream calls for recent repeated searches)
    route_cache_key = guardian.build_cache_key(
        PROVIDER_SERPAPI,
        "search_flights",
        {"dep": dep, "arr": arr, "outbound": out_date, "return": ret_date, "class": t_class}
    )

    if not force_refresh:
        cached_resp = guardian.get_cached(route_cache_key)
        if cached_resp and cached_resp.data:
            # Mark cached flights truthfully
            cached_results = []
            for item in cached_resp.data:
                f_copy = dict(item)
                f_copy["is_cached"] = True
                f_copy["cached_at"] = cached_resp.cached_at
                f_copy["age_seconds"] = cached_resp.age_seconds
                f_copy["data_status"] = "cached"
                f_copy["is_live"] = False
                f_copy["source"] = f"{f_copy.get('source', 'Google Flights via SerpAPI')} (Cached)"
                cached_results.append(f_copy)
            return cached_results

    api_key = os.getenv("SERPAPI_KEY")

    def _get_demo_flights():
        sample_airlines = [
            {"name": "IndiGo", "flight_no": "6E 214", "price": 4850, "duration": 130, "dep": "06:15", "arr": "08:25", "stops": 0},
            {"name": "Air India", "flight_no": "AI 804", "price": 5420, "duration": 125, "dep": "09:30", "arr": "11:35", "stops": 0},
            {"name": "Vistara", "flight_no": "UK 945", "price": 6190, "duration": 135, "dep": "14:00", "arr": "16:15", "stops": 0},
            {"name": "Akasa Air", "flight_no": "QP 112", "price": 4650, "duration": 210, "dep": "17:45", "arr": "21:15", "stops": 1},
            {"name": "SpiceJet", "flight_no": "SG 123", "price": 5100, "duration": 130, "dep": "19:10", "arr": "21:20", "stops": 0},
        ]
        demo_list = []
        for item in sample_airlines:
            itin_raw = f"{dep}_{arr}_{item['name']}_{item['flight_no']}_{item['dep']}_{item['arr']}_{item['stops']}"
            itinerary_id = hashlib.md5(itin_raw.encode("utf-8")).hexdigest()[:12]
            out_leg = {
                "airline": item["name"],
                "flight_number": item["flight_no"],
                "itinerary_id": itinerary_id,
                "price": item["price"],
                "currency": "INR",
                "duration": item["duration"],
                "stops": item["stops"],
                "departure_time": item["dep"],
                "arrival_time": item["arr"],
                "departure_airport": dep,
                "arrival_airport": arr,
                "segments": [{
                    "departure_airport": dep, "departure_name": f"{dep} Airport",
                    "departure_time": item["dep"], "arrival_airport": arr, "arrival_name": f"{arr} Airport",
                    "arrival_time": item["arr"], "airline": item["name"], "flight_number": item["flight_no"],
                    "duration": item["duration"], "airline_logo": ""
                }],
                "layovers": [] if item["stops"] == 0 else [{
                    "id": "NAG", "name": "Nagpur Airport", "duration": 45, "overnight": False
                }],
                "is_simulated": True,
                "is_live": False,
                "is_cached": False,
                "data_status": "demonstration",
                "source": "Demonstration Data (Live SerpAPI Unconfigured)"
            }
            if not ret_date:
                demo_list.append(out_leg)
            else:
                ret_itin_raw = f"{arr}_{dep}_{item['name']}_{item['flight_no']}R_15:30_17:45_{item['stops']}"
                ret_itinerary_id = hashlib.md5(ret_itin_raw.encode("utf-8")).hexdigest()[:12]
                ret_leg = {
                    **out_leg,
                    "flight_number": item["flight_no"] + "R",
                    "itinerary_id": ret_itinerary_id,
                    "departure_airport": arr, "arrival_airport": dep,
                    "departure_time": "15:30", "arrival_time": "17:45",
                    "segments": [{
                        "departure_airport": arr, "departure_name": f"{arr} Airport",
                        "departure_time": "15:30", "arrival_airport": dep, "arrival_name": f"{dep} Airport",
                        "arrival_time": "17:45", "airline": item["name"], "flight_number": item["flight_no"] + "R",
                        "duration": item["duration"], "airline_logo": ""
                    }],
                }
                roundtrip_id = hashlib.md5(f"{itinerary_id}_{ret_itinerary_id}".encode("utf-8")).hexdigest()[:12]
                demo_list.append({
                    **out_leg,
                    "itinerary_id": roundtrip_id,
                    "trip_type": "round_trip",
                    "outbound": out_leg, "return": ret_leg,
                    "return_airline": ret_leg["airline"], "return_duration": ret_leg["duration"],
                    "return_stops": ret_leg["stops"], "return_departure_time": ret_leg["departure_time"],
                    "return_arrival_time": ret_leg["arrival_time"], "return_departure_airport": ret_leg["departure_airport"],
                    "return_arrival_airport": ret_leg["arrival_airport"], "return_segments": ret_leg["segments"],
                    "return_layovers": [], "price": item["price"] * 2 - 400,
                    "reference_outbound_price": item["price"], "reference_return_price": item["price"],
                    "reference_separate_total": item["price"] * 2,
                    "is_simulated": True,
                    "is_live": False,
                    "is_cached": False,
                    "data_status": "demonstration",
                    "source": "Demonstration Data (Live SerpAPI Unconfigured)"
                })
        return sorted(demo_list, key=lambda f: f["price"])

    # Demo mode if no key provided
    if not api_key:
        return _get_demo_flights()

    params = {
        "engine": "google_flights", "api_key": api_key,
        "departure_id": dep, "arrival_id": arr,
        "outbound_date": out_date, "travel_class": t_class,
        "currency": "INR", "hl": "en", "gl": "in", "show_hidden": "true"
    }
    if ret_date:
        params["return_date"] = ret_date
        params["type"] = "1"
    else:
        params["type"] = "2"

    try:
        data = _serpapi_request(params, caller_type=caller_type)
    except Exception as err:
        print(f"SerpAPI quota notice: {err}. Using simulated flight intelligence.")
        return _get_demo_flights()

    itineraries = _collect_itineraries(data)

    if not ret_date:
        results = []
        for itinerary in itineraries:
            flight = _normalize_itinerary(itinerary)
            if flight:
                results.append(flight)
        sorted_results = sorted(results, key=lambda f: f["price"])
        # Cache results in Quota Guardian
        cfg = get_guardian_config()
        ttl = cfg["cache_ttl_checker"] if caller_type == CALLER_BACKGROUND_CHECK else cfg["cache_ttl_search"]
        guardian.set_cached(route_cache_key, PROVIDER_SERPAPI, "search_flights", sorted_results, ttl_seconds=ttl, is_live=True)
        return sorted_results

    # Round trip: initial response contains outbound options.
    reference_outbound_price = None
    reference_return_price = None
    reference_separate_total = None

    try:
        reference_outbound_price = _get_cheapest_one_way_price(
            dep, arr, out_date, t_class, caller_type=caller_type
        )
    except Exception as e:
        print("Outbound reference fare error:", e)

    try:
        reference_return_price = _get_cheapest_one_way_price(
            arr, dep, ret_date, t_class, caller_type=caller_type
        )
    except Exception as e:
        print("Return reference fare error:", e)

    if (
        reference_outbound_price is not None
        and reference_return_price is not None
    ):
        reference_separate_total = (
            reference_outbound_price
            + reference_return_price
        )

    outbound_limit = max(1, int(os.getenv("ROUND_TRIP_OUTBOUND_LIMIT", "5")))
    return_limit = max(1, int(os.getenv("ROUND_TRIP_RETURN_LIMIT", "5")))

    results = []

    for outbound_raw in itineraries[:outbound_limit]:
        outbound = _normalize_itinerary(outbound_raw)
        token = outbound_raw.get("departure_token")
        if not outbound or not token:
            continue

        return_params = {
            "engine": "google_flights", "api_key": api_key,
            "departure_id": dep, "arrival_id": arr,
            "outbound_date": out_date, "return_date": ret_date,
            "type": "1", "departure_token": token,
            "travel_class": t_class, "currency": "INR", "hl": "en", "gl": "in"
        }
        try:
            return_data = _serpapi_request(return_params, caller_type=caller_type)
        except Exception as e:
            print("Return leg search notice:", e)
            continue

        for return_raw in _collect_itineraries(return_data)[:return_limit]:
            return_leg = _normalize_itinerary(return_raw)
            if not return_leg:
                continue
            total_price = return_raw.get("price", outbound["price"])
            roundtrip_id = hashlib.md5(f"{outbound.get('itinerary_id')}_{return_leg.get('itinerary_id')}".encode("utf-8")).hexdigest()[:12]
            results.append({
                "trip_type": "round_trip",
                "airline": outbound["airline"],
                "flight_number": outbound.get("flight_number", ""),
                "itinerary_id": roundtrip_id,
                "price": int(total_price),
                "currency": "INR",
                "duration": outbound["duration"], "stops": outbound["stops"],
                "departure_time": outbound["departure_time"], "arrival_time": outbound["arrival_time"],
                "departure_airport": outbound["departure_airport"], "arrival_airport": outbound["arrival_airport"],
                "segments": outbound["segments"], "layovers": outbound["layovers"],
                "outbound": outbound, "return": return_leg,
                "return_airline": return_leg["airline"], "return_duration": return_leg["duration"],
                "return_stops": return_leg["stops"],
                "return_departure_time": return_leg["departure_time"],
                "return_arrival_time": return_leg["arrival_time"],
                "return_departure_airport": return_leg["departure_airport"],
                "return_arrival_airport": return_leg["arrival_airport"],
                "return_segments": return_leg["segments"], "return_layovers": return_leg["layovers"],
                "departure_token": token,
                "booking_token": return_raw.get("booking_token", ""),
                "reference_outbound_price": reference_outbound_price,
                "reference_return_price": reference_return_price,
                "reference_separate_total": reference_separate_total,
                "is_simulated": False,
                "is_live": True,
                "is_cached": False,
                "data_status": "live",
                "source": "Google Flights via SerpAPI"
            })

    sorted_results = sorted(results, key=lambda f: f["price"])
    # Cache round-trip results in Quota Guardian
    cfg = get_guardian_config()
    ttl = cfg["cache_ttl_checker"] if caller_type == CALLER_BACKGROUND_CHECK else cfg["cache_ttl_search"]
    guardian.set_cached(route_cache_key, PROVIDER_SERPAPI, "search_flights", sorted_results, ttl_seconds=ttl, is_live=True)
    return sorted_results


# =========================================================
# MULTI-PROVIDER COMPARISON INTEGRATION
# =========================================================

def get_flight_aggregator():
    from services.providers.aggregator import FlightComparisonAggregator
    return FlightComparisonAggregator()


def get_providers_status():
    return get_flight_aggregator().get_providers_status()


def search_multi_city_flights(
    legs,
    travel_class=1,
    adults=1,
    children=0,
    infants=0
):
    """
    Search and construct multi-city flight itineraries across multiple legs.
    Each leg dictionary contains:
      - departure: IATA
      - arrival: IATA
      - outbound_date: YYYY-MM-DD
    Returns a sorted list of combined multi-city itineraries.
    """
    if not legs or len(legs) < 2:
        return []

    # Demo / Intelligence generation templates
    template_carriers = [
        {"airline": "IndiGo", "prefix": "6E", "base_price": 4200, "base_dur": 120, "stops": 0, "dep_hour": 7},
        {"airline": "Air India", "prefix": "AI", "base_price": 4900, "base_dur": 135, "stops": 0, "dep_hour": 9},
        {"airline": "Vistara", "prefix": "UK", "base_price": 5600, "base_dur": 130, "stops": 0, "dep_hour": 14},
        {"airline": "Akasa Air", "prefix": "QP", "base_price": 3950, "base_dur": 180, "stops": 1, "dep_hour": 16},
        {"airline": "SpiceJet", "prefix": "SG", "base_price": 4450, "base_dur": 140, "stops": 0, "dep_hour": 19},
    ]

    class_multipliers = {1: 1.0, 2: 1.6, 3: 2.8, 4: 4.2}
    class_mult = class_multipliers.get(travel_class, 1.0)
    passenger_count = max(1, (adults or 1) + (children or 0))

    combined_itineraries = []

    for c_idx, carrier in enumerate(template_carriers):
        leg_details = []
        all_segments = []
        total_price = 0
        total_duration = 0
        flight_numbers = []

        for leg_idx, leg in enumerate(legs):
            dep = leg["departure"].upper()
            arr = leg["arrival"].upper()
            date = leg["outbound_date"]

            f_num = f"{carrier['prefix']} {100 * (leg_idx + 1) + c_idx * 15 + 23}"
            flight_numbers.append(f_num)

            dep_h = (carrier["dep_hour"] + leg_idx * 3) % 24
            dep_time = f"{dep_h:02d}:30"
            dur_mins = carrier["base_dur"] + (leg_idx * 15)
            arr_h = (dep_h + dur_mins // 60) % 24
            arr_m = (30 + dur_mins % 60) % 60
            arr_time = f"{arr_h:02d}:{arr_m:02d}"

            leg_price = int((carrier["base_price"] + (leg_idx * 600)) * class_mult * passenger_count)
            total_price += leg_price
            total_duration += dur_mins

            seg = {
                "departure_airport": dep,
                "departure_name": f"{dep} Airport",
                "departure_time": dep_time,
                "arrival_airport": arr,
                "arrival_name": f"{arr} Airport",
                "arrival_time": arr_time,
                "airline": carrier["airline"],
                "flight_number": f_num,
                "duration": dur_mins,
                "airline_logo": "",
                "leg_date": date
            }
            all_segments.append(seg)

            leg_details.append({
                "leg_index": leg_idx + 1,
                "departure_airport": dep,
                "arrival_airport": arr,
                "departure_date": date,
                "airline": carrier["airline"],
                "flight_number": f_num,
                "departure_time": dep_time,
                "arrival_time": arr_time,
                "duration": dur_mins,
                "stops": carrier["stops"],
                "price": leg_price,
                "segments": [seg]
            })

        first_leg = leg_details[0]
        last_leg = leg_details[-1]
        combo_flight_number = " / ".join(flight_numbers)

        raw_itin_id = f"mc_{'_'.join(l['departure_airport'] + '_' + l['arrival_airport'] + '_' + l['departure_date'] for l in leg_details)}_{carrier['airline']}_{combo_flight_number}"
        itinerary_id = hashlib.md5(raw_itin_id.encode("utf-8")).hexdigest()[:12]

        combined_itineraries.append({
            "trip_type": "multi_city",
            "airline": carrier["airline"],
            "flight_number": combo_flight_number,
            "itinerary_id": itinerary_id,
            "price": total_price,
            "currency": "INR",
            "duration": total_duration,
            "stops": sum(l["stops"] for l in leg_details),
            "departure_time": first_leg["departure_time"],
            "arrival_time": last_leg["arrival_time"],
            "departure_airport": first_leg["departure_airport"],
            "arrival_airport": last_leg["arrival_airport"],
            "segments": all_segments,
            "layovers": [],
            "legs": leg_details,
            "adults": adults or 1,
            "children": children or 0,
            "infants": infants or 0,
            "passengers": passenger_count,
            "travel_class": travel_class,
            "is_simulated": True,
            "is_live": False,
            "data_status": "demonstration",
            "source": "Demonstration Data (Multi-City Route Intelligence)"
        })

    return sorted(combined_itineraries, key=lambda f: f["price"])


def search_flights_with_comparison(
    departure,
    arrival,
    outbound_date,
    return_date=None,
    travel_class=1
):
    """
    Search flights and perform comprehensive multi-website price comparison.
    Returns (results, comparison_data).
    """
    results = search_flights(
        departure=departure,
        arrival=arrival,
        outbound_date=outbound_date,
        return_date=return_date,
        travel_class=travel_class
    )

    aggregator = get_flight_aggregator()
    comparison = aggregator.search_and_compare(
        departure=departure,
        arrival=arrival,
        outbound_date=outbound_date,
        return_date=return_date,
        travel_class=travel_class,
        reference_itineraries=results
    )

    return results, comparison


