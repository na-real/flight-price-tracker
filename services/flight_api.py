import os



import requests







from dotenv import load_dotenv











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
    return {
        "airline": airline_name,
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
        "booking_token": itinerary.get("booking_token", "")
    }

def _serpapi_request(params):
    response = requests.get(SERPAPI_URL, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise Exception(data["error"])
    return data
def _get_cheapest_one_way_price(
    departure,
    arrival,
    travel_date,
    travel_class=1
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

    data = _serpapi_request(params)

    prices = []

    for itinerary in _collect_itineraries(data):
        flight = _normalize_itinerary(itinerary)

        if flight and flight.get("price") is not None:
            try:
                prices.append(int(flight["price"]))
            except (TypeError, ValueError):
                pass

    return min(prices) if prices else None


def search_flights(departure, arrival, outbound_date, return_date=None, travel_class=1):
    api_key = os.getenv("SERPAPI_KEY")

    # Demo mode
    if not api_key:
        outbound = {
            "airline": "Demo Air", "price": 48250, "currency": "INR",
            "duration": 160, "stops": 0, "departure_time": "09:00",
            "arrival_time": "11:40", "departure_airport": departure,
            "arrival_airport": arrival,
            "segments": [{
                "departure_airport": departure, "departure_name": departure,
                "departure_time": "09:00", "arrival_airport": arrival,
                "arrival_name": arrival, "arrival_time": "11:40",
                "airline": "Demo Air", "flight_number": "DA101",
                "duration": 160, "airline_logo": ""
            }], "layovers": []
        }
        if not return_date:
            return [outbound]

        return_leg = {
            **outbound,
            "departure_airport": arrival,
            "arrival_airport": departure,
            "departure_time": "14:00",
            "arrival_time": "16:40",
            "segments": [{
                "departure_airport": arrival,
                "departure_name": arrival,
                "departure_time": "14:00",
                "arrival_airport": departure,
                "arrival_name": departure,
                "arrival_time": "16:40",
                "airline": "Demo Air",
                "flight_number": "DA102",
                "duration": 160,
                "airline_logo": ""
            }],
            "layovers": []
        }

        return [{
            **outbound,
            "trip_type": "round_trip",
            "outbound": outbound,
            "return": return_leg,
            "return_airline": return_leg["airline"],
            "return_duration": 160,
            "return_stops": 0,
            "return_departure_time": "14:00",
            "return_arrival_time": "16:40",
            "return_departure_airport": arrival,
            "return_arrival_airport": departure,
            "return_segments": return_leg["segments"],
            "return_layovers": [],
            "price": 48250
        }]

    params = {
           "engine": "google_flights", "api_key": api_key,
           "departure_id": departure.upper(), "arrival_id": arrival.upper(),
           "outbound_date": outbound_date, "travel_class": travel_class,
           "currency": "INR", "hl": "en", "gl": "in", "show_hidden": "true"
    }
    if return_date:
        params["return_date"] = return_date
        params["type"] = "1"
    else:
        params["type"] = "2"

    data = _serpapi_request(params)
    itineraries = _collect_itineraries(data)

    if not return_date:
        results = []

        for itinerary in itineraries:
            flight = _normalize_itinerary(itinerary)

            if flight:
                results.append(flight)
        return sorted(results, key=lambda f: f["price"])


    # Round trip: initial response contains outbound options.
    # Each outbound option has a departure_token.
    # Use that token to retrieve the corresponding return options.

    # ---------------------------------------------------------
    # REFERENCE ONE-WAY FARES
    # ---------------------------------------------------------

    reference_outbound_price = None
    reference_return_price = None
    reference_separate_total = None


    try:
        reference_outbound_price = _get_cheapest_one_way_price(
            departure,
            arrival,
            outbound_date,
            travel_class
        )
    except Exception as e:
        print("Outbound reference fare error:", e)

    try:
        reference_return_price = _get_cheapest_one_way_price(
            arrival,
            departure,
            return_date,
            travel_class
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

    outbound_limit = max(
        1,
        int(os.getenv("ROUND_TRIP_OUTBOUND_LIMIT", "5"))
    )

    return_limit = max(
        1,
        int(os.getenv("ROUND_TRIP_RETURN_LIMIT", "5"))
    )

    results = []

    for outbound_raw in itineraries[:outbound_limit]:
        outbound = _normalize_itinerary(outbound_raw)
        token = outbound_raw.get("departure_token")
        if not outbound or not token:
            continue

        return_params = {
            "engine": "google_flights", "api_key": api_key,
            "departure_id": departure.upper(), "arrival_id": arrival.upper(),
            "outbound_date": outbound_date, "return_date": return_date,
            "type": "1", "departure_token": token,
            "travel_class": travel_class, "currency": "INR", "hl": "en", "gl": "in"
        }
        return_data = _serpapi_request(return_params)

        for return_raw in _collect_itineraries(return_data)[:return_limit]:
            return_leg = _normalize_itinerary(return_raw)
            if not return_leg:
                continue
            total_price = return_raw.get("price", outbound["price"])
            results.append({
                "trip_type": "round_trip",
                "airline": outbound["airline"], "price": int(total_price), "currency": "INR",
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
                "departure_token": token, "booking_token": return_raw.get("booking_token", ""),"reference_outbound_price": reference_outbound_price,
                "reference_return_price": reference_return_price,
                "reference_separate_total": reference_separate_total,
            })

    return sorted(results, key=lambda f: f["price"])
