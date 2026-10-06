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
    return str(value).strip().upper()