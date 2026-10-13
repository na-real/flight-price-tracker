def get_missing_fields(data, required_fields):
    """Return required fields that are missing or empty."""
    return [
        field
        for field in required_fields
        if not data.get(field)
    ]


def validate_target_price(value):
    """Convert target price to a positive float."""
    try:
        target_price = float(value)
    except (TypeError, ValueError):
        raise ValueError("Target price must be a valid number.")

    if target_price <= 0:
        raise ValueError("Target price must be greater than zero.")

    return target_price


def clean_iata(value):
    """Clean an airport IATA code."""
    return str(value or "").strip().upper()


def validate_date_iso(date_str, field_name="Date", allow_past=False):
    """Validate that date_str is in YYYY-MM-DD format and not in the past."""
    from datetime import datetime, date
    if not date_str or not isinstance(date_str, str):
        raise ValueError(f"{field_name} is required.")
    cleaned = date_str.strip()
    try:
        dt = datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid {field_name} format: {date_str}. Expected YYYY-MM-DD.")

    if not allow_past and dt < date.today():
        raise ValueError(f"{field_name} cannot be in the past.")

    return dt


def validate_date_sequence(outbound_date_str, return_date_str=None, allow_past=False):
    """
    Validate that outbound date is valid and return date (if given)
    is on or after outbound date.
    """
    outbound_dt = validate_date_iso(outbound_date_str, "Departure date", allow_past=allow_past)
    if return_date_str:
        return_dt = validate_date_iso(return_date_str, "Return date", allow_past=allow_past)
        if return_dt < outbound_dt:
            raise ValueError("Return date cannot be before departure date.")
        return outbound_dt, return_dt
    return outbound_dt, None


def validate_passenger_counts(adults=1, children=0, infants=0):
    """
    Validate passenger numbers.
    Rules:
    - At least 1 adult
    - Children >= 0, Infants >= 0
    - Infants <= Adults (each infant must be accompanied by an adult)
    - Total passengers <= 9
    """
    try:
        a = int(adults if adults is not None else 1)
        c = int(children if children is not None else 0)
        i = int(infants if infants is not None else 0)
    except (TypeError, ValueError):
        raise ValueError("Passenger counts must be valid integers.")

    if a < 1:
        raise ValueError("At least 1 adult passenger is required.")
    if c < 0:
        raise ValueError("Children count cannot be negative.")
    if i < 0:
        raise ValueError("Infants count cannot be negative.")
    if i > a:
        raise ValueError("Number of infants cannot exceed number of adult passengers.")
    total = a + c + i
    if total > 9:
        raise ValueError("Total number of passengers cannot exceed 9.")
    return a, c, i, total


def validate_multi_city_legs(legs, allow_past=False):
    """
    Validate multi-city itinerary legs.
    Requirements:
    - Must be a list of 2 to 5 legs.
    - Each leg must specify departure, arrival, and outbound_date.
    - Origin and destination must not be identical within the same leg.
    - Departure dates across legs must be non-decreasing (chronological).
    - Dates cannot be in the past (unless allow_past=True).
    """
    if not isinstance(legs, list):
        raise ValueError("Multi-city legs must be provided as a list.")
    if len(legs) < 2:
        raise ValueError("Multi-city search requires at least 2 flight legs.")
    if len(legs) > 5:
        raise ValueError("Multi-city search allows a maximum of 5 flight legs.")

    cleaned_legs = []
    prev_date = None

    for index, leg in enumerate(legs):
        leg_num = index + 1
        if not isinstance(leg, dict):
            raise ValueError(f"Flight leg {leg_num} must be an object.")

        dep = clean_iata(leg.get("departure"))
        arr = clean_iata(leg.get("arrival"))
        date_str = str(leg.get("outbound_date") or "").strip()

        if not dep or len(dep) != 3:
            raise ValueError(f"Flight leg {leg_num} must have a valid 3-letter departure airport code.")
        if not arr or len(arr) != 3:
            raise ValueError(f"Flight leg {leg_num} must have a valid 3-letter arrival airport code.")
        if dep == arr:
            raise ValueError(f"Flight leg {leg_num} origin and destination cannot be identical ({dep}).")

        dt = validate_date_iso(date_str, f"Flight leg {leg_num} departure date", allow_past=allow_past)
        if prev_date and dt < prev_date:
            raise ValueError(
                f"Flight leg {leg_num} date ({date_str}) cannot be before leg {index} date ({prev_date.isoformat()})."
            )
        prev_date = dt

        cleaned_legs.append({
            "departure": dep,
            "arrival": arr,
            "outbound_date": date_str
        })

    return cleaned_legs