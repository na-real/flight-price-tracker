from validation import (
    get_missing_fields,
    validate_target_price,
    clean_iata,
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