from validation import clean_iata
from services.airports import (
    calculate_distance,
    get_nearby_airports,
)


def test_clean_iata():
    assert clean_iata(" del ") == "DEL"


def test_calculate_distance():
    distance = calculate_distance(
        28.6139, 77.2090,
        19.0760, 72.8777
    )

    assert distance > 0

def test_get_nearby_airports():
    airports = get_nearby_airports("DEL", 100)

    assert isinstance(airports, list)