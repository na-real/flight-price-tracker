# pyrefly: ignore [missing-import]
import pytest

from validation import (
    get_missing_fields,
    validate_target_price,
    clean_iata,
    validate_date_sequence,
    validate_passenger_counts,
    validate_multi_city_legs,
)


def test_get_missing_fields():
    data = {
        "departure": "DEL",
        "arrival": "",
        "outbound_date": "2026-11-15",
    }

    required = [
        "departure",
        "arrival",
        "outbound_date",
    ]

    result = get_missing_fields(data, required)

    assert result == ["arrival"]


def test_validate_target_price():
    result = validate_target_price("5000")

    assert result == 5000.0


def test_validate_target_price_rejects_zero():
    try:
        validate_target_price("0")
        assert False
    except ValueError as error:
        assert str(error) == "Target price must be greater than zero."


def test_validate_target_price_rejects_text():
    try:
        validate_target_price("abc")
        assert False
    except ValueError as error:
        assert str(error) == "Target price must be a valid number."


def test_clean_iata():
    assert clean_iata(" del ") == "DEL"


def test_validate_date_sequence():
    outbound, ret = validate_date_sequence("2026-11-15", "2026-11-20")
    assert str(outbound) == "2026-11-15"
    assert str(ret) == "2026-11-20"


def test_validate_date_sequence_rejects_inverted():
    with pytest.raises(ValueError, match="Return date cannot be before departure"):
        validate_date_sequence("2026-11-20", "2026-11-15")


def test_validate_passenger_counts():
    a, c, i, tot = validate_passenger_counts(2, 1, 1)
    assert (a, c, i, tot) == (2, 1, 1, 4)


def test_validate_passenger_counts_infants_exceed_adults():
    with pytest.raises(ValueError, match="infants cannot exceed"):
        validate_passenger_counts(1, 0, 2)


def test_validate_multi_city_legs():
    legs = [
        {"departure": "DEL", "arrival": "BOM", "outbound_date": "2026-11-15"},
        {"departure": "BOM", "arrival": "BLR", "outbound_date": "2026-11-18"},
    ]
    cleaned = validate_multi_city_legs(legs)
    assert len(cleaned) == 2
    assert cleaned[0]["departure"] == "DEL"
    assert cleaned[1]["arrival"] == "BLR"


def test_validate_multi_city_legs_rejects_dates_out_of_order():
    legs = [
        {"departure": "DEL", "arrival": "BOM", "outbound_date": "2026-11-20"},
        {"departure": "BOM", "arrival": "BLR", "outbound_date": "2026-11-15"},
    ]
    with pytest.raises(ValueError, match="cannot be before"):
        validate_multi_city_legs(legs)