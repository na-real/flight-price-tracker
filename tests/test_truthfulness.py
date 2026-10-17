import os
import pytest
from app import app
from db import db, init_db, DATABASE_URL
from validation import validate_airport_pair
from services.flight_api import search_flights
from services.providers.base import FlightOffer
from services.providers.direct_airline_provider import DirectAirlineProvider
from services.providers.expedia_provider import ExpediaFlightProvider
from services.providers.ota_partner_provider import OtaPartnerProvider
import checker


@pytest.fixture
def client():
    app.config["TESTING"] = True
    init_db()
    with app.test_client() as client:
        yield client


def test_contact_persistence(client):
    """Verify that legitimate contact messages are inserted into the contact_messages database table."""
    conn = db()
    # Count before
    before = conn.execute("SELECT COUNT(*) AS c FROM contact_messages").fetchone()["c"]

    payload = {
        "name": "Jane Explorer",
        "email": "jane@example.com",
        "subject": "Radar Alert & Price Tracking Support",
        "message": "Testing automated verification of contact inquiry persistence in database.",
        "hp_username": ""
    }
    res = client.post("/api/contact", json=payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "recorded" in data["message"].lower() or "received" in data["message"].lower()

    # Verify insertion in DB
    conn = db()
    after = conn.execute("SELECT COUNT(*) AS c FROM contact_messages").fetchone()["c"]
    assert after == before + 1

    latest = conn.execute(
        "SELECT * FROM contact_messages WHERE email = ? ORDER BY id DESC LIMIT 1",
        ("jane@example.com",)
    ).fetchone()
    assert latest is not None
    assert latest["name"] == "Jane Explorer"
    assert latest["subject"] == "Radar Alert & Price Tracking Support"
    assert "Testing automated verification" in latest["message"]


def test_contact_honeypot_drops_db(client):
    """Verify that bot submissions with filled honeypot return success but are not stored in database."""
    conn = db()
    before = conn.execute("SELECT COUNT(*) AS c FROM contact_messages").fetchone()["c"]

    res = client.post("/api/contact", json={
        "name": "Spam Bot",
        "email": "bot@spamnetwork.com",
        "subject": "General Inquiries & Feedback",
        "message": "Buy cheap watches at our site now!",
        "hp_username": "trapped_bot"
    })
    assert res.status_code == 200
    assert res.get_json()["success"] is True

    conn = db()
    after = conn.execute("SELECT COUNT(*) AS c FROM contact_messages").fetchone()["c"]
    assert after == before


def test_airport_pair_validation():
    """Verify airport pair validation: rejects same airport and resolves aliases."""
    # Same canonical code
    with pytest.raises(ValueError, match="cannot be identical"):
        validate_airport_pair("DEL", "DEL")

    # City alias resolving to same airport
    with pytest.raises(ValueError, match="cannot be identical"):
        validate_airport_pair("BOM", "bombay")

    # Invalid code
    with pytest.raises(ValueError, match="valid 3-letter IATA"):
        validate_airport_pair("XYZINVALID", "BOM")

    # Valid distinct pair
    dep, arr = validate_airport_pair("DEL", "BOM")
    assert dep == "DEL"
    assert arr == "BOM"

    # Valid alias pair
    dep2, arr2 = validate_airport_pair("Delhi", "Mumbai")
    assert dep2 == "DEL"
    assert arr2 == "BOM"


def test_search_rejects_identical_airports(client):
    """Verify search and compare API endpoints return 400 when departure equals arrival."""
    # Search endpoint
    res_search = client.post("/api/search", json={
        "departure": "DEL",
        "arrival": "DEL",
        "outbound_date": "2026-11-20"
    })
    assert res_search.status_code == 400
    err_search = res_search.get_json()
    assert "cannot be identical" in err_search["error"]

    # Compare endpoint
    res_compare = client.post("/api/flight/compare", json={
        "departure": "DEL",
        "arrival": "DEL",
        "outbound_date": "2026-11-20"
    })
    assert res_compare.status_code == 400
    err_compare = res_compare.get_json()
    assert "cannot be identical" in err_compare["error"]


def test_flight_data_status_contracts(monkeypatch):
    """Verify data contracts: simulated fares are flagged honestly as demonstration data."""
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    results = search_flights("DEL", "BOM", "2026-11-15")
    assert len(results) > 0
    for flight in results:
        assert flight.get("is_simulated") is True
        assert flight.get("data_status") == "demonstration"

    ref_flights = [{
        "airline": "Air India",
        "flight_number": "AI 101",
        "price": 5000,
        "departure_airport": "DEL",
        "arrival_airport": "BOM",
        "departure_time": "10:00",
        "arrival_time": "12:00",
        "duration": 120,
        "stops": 0,
        "trip_type": "one_way",
        "segments": [],
        "layovers": []
    }]

    # Test provider offers
    direct = DirectAirlineProvider()
    offers = direct.search("DEL", "BOM", "2026-11-15", reference_itineraries=ref_flights)
    for offer in offers:
        assert offer.is_live is True
        assert offer.data_status == "live"

    expedia = ExpediaFlightProvider()
    e_offers = expedia.search("DEL", "BOM", "2026-11-15", reference_itineraries=ref_flights)
    for offer in e_offers:
        assert offer.is_estimated is True
        assert offer.data_status == "estimated"

    ota = OtaPartnerProvider()
    o_offers = ota.search("DEL", "BOM", "2026-11-15", reference_itineraries=ref_flights)
    for offer in o_offers:
        assert offer.is_estimated is True
        assert offer.data_status == "estimated"


def test_checker_no_false_alert_on_baseline(monkeypatch):
    """Verify that the background checker does NOT send an alert on the first check if price is above target."""
    conn = db()
    test_email = "baseline_check_user@example.com"
    conn.execute(
        """
        INSERT INTO tracked_flights
        (departure, arrival, outbound_date, target_price, lowest_price, target_alert_sent, created_at, email)
        VALUES ('DEL', 'BOM', '2026-12-01', 5000, NULL, 0, '2026-01-01T00:00:00Z', ?)
        """,
        (test_email,)
    )
    conn.commit()
    conn.close()

    emails_sent = []
    def mock_send_email(to_email, subject, body):
        emails_sent.append({"to": to_email, "subject": subject, "body": body})

    monkeypatch.setattr(checker, "send_email", mock_send_email)
    monkeypatch.setenv("SERPAPI_KEY", "mock_key")

    # Mock search_flights returning 7500 (above target 5000, initial baseline)
    def mock_search(dep, arr, out, ret=None):
        return [{
            "price": 7500.0,
            "airline": "Air India",
            "is_simulated": False,
            "data_status": "live"
        }]

    monkeypatch.setattr(checker, "search_flights", mock_search)

    checker.run()

    # No emails should have been sent since this is first baseline observation above target!
    user_alerts = [e for e in emails_sent if e["to"] == test_email]
    assert len(user_alerts) == 0


def test_checker_alerts_on_price_drop(monkeypatch):
    """Verify that the background checker DOES send an alert on a confirmed price drop below previous lowest."""
    conn = db()
    test_email = "drop_check_user@example.com"
    conn.execute(
        """
        INSERT INTO tracked_flights
        (departure, arrival, outbound_date, target_price, lowest_price, target_alert_sent, created_at, email)
        VALUES ('DEL', 'BLR', '2026-12-05', 4000, 9500, 0, '2026-01-01T00:00:00Z', ?)
        """,
        (test_email,)
    )
    conn.commit()
    conn.close()

    emails_sent = []
    def mock_send_email(to_email, subject, body):
        emails_sent.append({"to": to_email, "subject": subject, "body": body})

    monkeypatch.setattr(checker, "send_email", mock_send_email)
    monkeypatch.setenv("SERPAPI_KEY", "mock_key")

    # Price dropped from 9500 to 8000
    def mock_search(dep, arr, out, ret=None):
        return [{
            "price": 8000.0,
            "airline": "IndiGo",
            "is_simulated": False,
            "data_status": "live"
        }]

    monkeypatch.setattr(checker, "search_flights", mock_search)

    checker.run()

    user_alerts = [e for e in emails_sent if e["to"] == test_email]
    assert len(user_alerts) == 1
    assert "Flight price dropped" in user_alerts[0]["subject"] or "Flight Price Alert" in user_alerts[0]["subject"]
    assert "CONFIRMED FARE DROP" in user_alerts[0]["body"]
    assert "8,000" in user_alerts[0]["body"]


def test_rendered_templates_free_of_fictional_claims(client):
    """Audit rendered web pages to ensure complete absence of fabricated contact details and false savings."""
    endpoints = ["/", "/contact", "/terms", "/about", "/compare"]
    fictional_strings = [
        "+44 7700 900077",
        "Terminal 2 Aviation Pavilion",
        "concierge@skytrack.luxury",
        "api@skytrack.luxury",
        "Save up to ₹2,800"
    ]

    for ep in endpoints:
        res = client.get(ep)
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        for bad_str in fictional_strings:
            assert bad_str not in html, f"Found prohibited fictional string '{bad_str}' in endpoint '{ep}'"
