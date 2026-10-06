from services.flight_api import (
    clean_time,
    extract_airport,
    build_segments,
    build_layovers,
)


def test_clean_time():
    assert clean_time("2026-11-15 17:30") == "17:30"


def test_extract_airport():
    airport = extract_airport({
        "id": "DEL",
        "name": "Delhi Airport",
        "time": "2026-11-15 10:00",
    })

    assert airport["id"] == "DEL"
    assert airport["name"] == "Delhi Airport"
    assert airport["time"] == "2026-11-15 10:00"


def test_build_segments():
    segments = build_segments([
        {
            "departure_airport": {
                "id": "DEL",
                "name": "Delhi Airport",
                "time": "2026-11-15 10:00",
            },
            "arrival_airport": {
                "id": "BOM",
                "name": "Mumbai Airport",
                "time": "2026-11-15 12:00",
            },
            "airline": "Test Airlines",
            "flight_number": "TA101",
            "duration": 120,
        }
    ])

    assert len(segments) == 1
    assert segments[0]["departure_airport"] == "DEL"
    assert segments[0]["arrival_airport"] == "BOM"
    assert segments[0]["airline"] == "Test Airlines"
    assert segments[0]["duration"] == 120


def test_build_layovers():
    segments = [
        {
            "arrival_airport": "BOM",
            "arrival_name": "Mumbai Airport",
            "arrival_time": "12:00",
        },
        {
            "departure_airport": "BOM",
            "departure_name": "Mumbai Airport",
            "departure_time": "14:00",
        },
    ]

    layovers = build_layovers({}, segments)

    assert len(layovers) == 1
    assert layovers[0]["id"] == "BOM"
    assert layovers[0]["duration"] == 120