import os
import re
import pytest
from app import app
from db import db, init_db
import checker
from services.email_service import (
    build_price_drop_email,
    send_price_drop_email,
    build_tracking_confirmation_email,
    send_tracking_confirmation_email,
    build_service_interruption_email,
    send_service_interruption_email,
    scrub_email_address,
    calculate_savings,
    format_airport_display
)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    init_db()
    with app.test_client() as client:
        yield client


# ==============================================================================
# 1. TEMPLATE FORM AUDIT: NEUTRAL PLACEHOLDERS & BLANK DEFAULT VALUES
# ==============================================================================

def test_search_forms_have_neutral_placeholders_and_blank_defaults(client):
    """
    Ensure all origin and destination fields are blank by default and display
    clean, neutral placeholders across all relevant pages.
    """
    pages_to_check = ["/", "/flights", "/compare", "/tracking"]

    for page in pages_to_check:
        res = client.get(page)
        assert res.status_code == 200, f"Page {page} failed to render"
        html = res.get_data(as_text=True)

        # Prohibited demo airport placeholders or prefilled values
        prohibited_tokens = [
            'placeholder="London Heathrow (LHR)"',
            'placeholder="Kansai / Tokyo (KIX)"',
            'placeholder="DEL"',
            'placeholder="BOM"',
            'placeholder="LHR"',
            'placeholder="KIX"',
            'value="DEL"',
            'value="BOM"',
            'value="LHR"',
            'value="London Heathrow (LHR)"',
            'value="Kansai / Tokyo (KIX)"'
        ]
        for token in prohibited_tokens:
            assert token not in html, f"Found prohibited demo token '{token}' in page {page}"

        # Clean neutral placeholders must be present
        if page in ["/", "/flights", "/compare"]:
            assert 'placeholder="Enter origin city or airport"' in html, f"Missing origin placeholder in {page}"
            assert 'placeholder="Enter destination city or airport"' in html, f"Missing destination placeholder in {page}"

        if page == "/tracking":
            assert 'placeholder="Enter origin city or airport"' in html
            assert 'placeholder="Enter destination city or airport"' in html
            assert 'placeholder="Select departure date"' in html

        # Verify departure/return date neutral placeholders where applicable
        if page in ["/", "/flights"]:
            assert 'placeholder="Select departure date"' in html
            assert 'placeholder="Select return date"' in html


def test_no_hardcoded_demo_flights_in_rendered_dashboard(client):
    """Verify that dashboard does not display hardcoded sample flight links."""
    res = client.get("/dashboard")
    # Redirects or renders depending on auth; let's check with an authenticated session
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["user_email"] = "auditor@example.com"
        sess["user_name"] = "Auditor"

    res = client.get("/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    assert "DEL → Tokyo" not in html
    assert "LHR → Naples" not in html
    assert "JFK → Reykjavik" not in html
    assert "Real-Time Flight Search" in html
    assert "/flights" in html


# ==============================================================================
# 2. PRICE-DROP EMAIL NOTIFICATION TESTS
# ==============================================================================

def test_price_drop_email_content_and_savings_calculation():
    """
    Test price drop email subject, calculated savings, percentage drop,
    route formatting, verified carrier details, and disclaimer.
    """
    subject, text_body, html_body = build_price_drop_email(
        to_email="traveler@example.com",
        departure_iata="DEL",
        arrival_iata="MAA",
        outbound_date="2026-11-20",
        previous_price=10000.0,
        current_price=8500.0,
        airline="Air India",
        flight_number="AI-542",
        checked_at="2026-10-16 12:00 UTC",
        flight_url="https://skytracker.luxury/flights/123",
        customer_name="Jane Doe"
    )

    # Subject specification
    assert subject == "SkyTracker: Flight price dropped — DEL → MAA"

    # Greeting with customer name
    assert "Hello Jane Doe," in text_body
    assert "Jane Doe" in html_body

    # Route formatting with airport names and IATA codes
    assert "New Delhi (DEL) → Chennai (MAA)" in text_body
    assert "New Delhi (DEL)" in html_body
    assert "Chennai (MAA)" in html_body

    # Calculated exact savings and percentage decrease: 10000 - 8500 = 1500 (15.0%)
    assert "₹1,500" in text_body
    assert "15.0%" in text_body
    assert "₹10,000" in text_body
    assert "₹8,500" in text_body

    # Verified provider carrier and flight details
    assert "Air India" in text_body
    assert "AI-542" in text_body

    # Timestamp and link
    assert "2026-10-16 12:00 UTC" in text_body
    assert "https://skytracker.luxury/flights/123" in text_body
    assert "https://skytracker.luxury/flights/123" in html_body

    # Mandatory disclaimer
    disclaimer = "Flight prices and seat availability can change at any time. This notification is not a booking confirmation."
    assert disclaimer in text_body
    assert disclaimer in html_body


def test_price_drop_email_handles_missing_name_and_missing_carrier():
    """
    Verify safe fallback when customer name and optional flight details are absent.
    """
    subject, text_body, html_body = build_price_drop_email(
        to_email="anonymous@example.com",
        departure_iata="DEL",
        arrival_iata="BOM",
        outbound_date="2026-12-01",
        previous_price=6000.0,
        current_price=4500.0,
        airline=None,
        flight_number=None,
        checked_at=None,
        flight_url=None,
        customer_name=None
    )

    # Neutral fallback greeting
    assert "Hello Traveler," in text_body
    assert "Traveler" in html_body

    # Savings: 6000 - 4500 = 1500 (25.0%)
    assert "₹1,500 (25.0%)" in text_body

    # No dummy airline or "None" in body
    assert "None" not in text_body
    assert "Airline:" not in text_body


def test_price_drop_email_html_escaping_and_security():
    """Verify that XSS injection in customer name or route is properly escaped in HTML emails."""
    malicious_name = "<script>alert('xss')</script>"
    _, _, html_body = build_price_drop_email(
        to_email="target@example.com",
        departure_iata="DEL",
        arrival_iata="BOM",
        outbound_date="2026-11-15",
        previous_price=5000.0,
        current_price=4000.0,
        customer_name=malicious_name
    )

    assert "<script>" not in html_body
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in html_body or "&lt;script&gt;" in html_body


# ==============================================================================
# 3. NEW TRACKING CONFIRMATION EMAIL TESTS
# ==============================================================================

def test_tracking_confirmation_email_content():
    """Test tracking confirmation email generation."""
    subject, text_body, html_body = build_tracking_confirmation_email(
        to_email="patron@example.com",
        departure_iata="DEL",
        arrival_iata="BLR",
        outbound_date="2026-11-25",
        target_price=5500.0,
        return_date="2026-12-02",
        passenger_summary="1 Adult, Economy",
        tracking_id=42,
        delete_token="secure_token_abc"
    )

    assert subject == "SkyTracker: Your flight price alert is active"
    assert "New Delhi (DEL) → Bengaluru (BLR)" in text_body
    assert "2026-11-25" in text_body
    assert "2026-12-02" in text_body
    assert "₹5,500" in text_body
    assert "1 Adult, Economy" in text_body
    assert "delete_token=secure_token_abc" in text_body
    assert "delete_token=secure_token_abc" in html_body
    assert "tracking is subject to airline provider availability" in text_body.lower()


def test_api_track_dispatches_confirmation_email_non_blocking(client, monkeypatch):
    """
    Verify that creating a tracked flight via /api/track dispatches a confirmation email
    and does not fail if email dispatch raises an exception.
    """
    dispatched = []

    def mock_send_tracking_confirmation(to_email, **kwargs):
        dispatched.append({"to": to_email, "kwargs": kwargs})

    monkeypatch.setattr("services.email_service.send_tracking_confirmation_email", mock_send_tracking_confirmation)

    payload = {
        "departure": "DEL",
        "arrival": "MAA",
        "outbound_date": "2026-12-10",
        "target_price": 5000,
        "email": "notify_me@example.com"
    }

    res = client.post("/api/track", json=payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["message"] == "Flight tracking started!"
    assert len(dispatched) == 1
    assert dispatched[0]["to"] == "notify_me@example.com"
    assert dispatched[0]["kwargs"]["departure_iata"] == "DEL"
    assert dispatched[0]["kwargs"]["arrival_iata"] == "MAA"

    # Now verify that even if send_tracking_confirmation_email raises an error, /api/track succeeds
    def mock_failing_send(*args, **kwargs):
        raise ConnectionError("Simulated SMTP timeout")

    monkeypatch.setattr("services.email_service.send_tracking_confirmation_email", mock_failing_send)

    payload2 = {
        "departure": "BOM",
        "arrival": "GOI",
        "outbound_date": "2026-12-15",
        "target_price": 3500,
        "email": "notify_me_resilient@example.com"
    }
    res2 = client.post("/api/track", json=payload2)
    assert res2.status_code == 200
    data2 = res2.get_json()
    assert data2["message"] == "Flight tracking started!"
    assert "id" in data2


# ==============================================================================
# 4. SERVICE INTERRUPTION / UNVERIFIED CHECK EMAIL TESTS
# ==============================================================================

def test_service_interruption_email_content():
    """Test service interruption email content and parameters."""
    subject, text_body, html_body = build_service_interruption_email(
        to_email="patron@example.com",
        departure_iata="DEL",
        arrival_iata="BOM",
        outbound_date="2026-11-30",
        reason="SerpAPI quota exhausted",
        last_checked_at="2026-10-15 14:00 UTC",
        customer_name="Robert"
    )

    assert subject == "SkyTracker: Flight price check update — DEL → BOM"
    assert "Hello Robert," in text_body
    assert "SerpAPI quota exhausted" in text_body
    assert "2026-10-15 14:00 UTC" in text_body
    assert "Our automated radar continues monitoring" in text_body


def test_checker_deduplicates_service_interruption_notifications(monkeypatch):
    """
    Verify that background checker notifies user on service interruption,
    but deduplicates subsequent checks for the same unresolved issue.
    """
    conn = db()
    test_email = "interruption_user@example.com"
    conn.execute("DELETE FROM tracked_flights WHERE email = ?", (test_email,))
    conn.commit()
    cur = conn.execute(
        """
        INSERT INTO tracked_flights
        (departure, arrival, outbound_date, target_price, lowest_price, target_alert_sent, created_at, email, last_interrupted_at)
        VALUES ('DEL', 'HYD', '2026-12-20', 4500, NULL, 0, '2026-01-01T00:00:00Z', ?, NULL)
        """,
        (test_email,)
    )
    conn.commit()
    flight_id = cur.lastrowid
    conn.close()

    emails_sent = []

    def mock_send_email(to_email, subject, body, html_body=None):
        emails_sent.append({"to": to_email, "subject": subject, "body": body})

    monkeypatch.setattr(checker, "send_email", mock_send_email)
    monkeypatch.setenv("SERPAPI_KEY", "mock_key")

    # Mock search_flights raising an error or returning empty/error
    def mock_failing_search(dep, arr, out, ret=None):
        raise RuntimeError("Provider connection failed")

    monkeypatch.setattr(checker, "search_flights", mock_failing_search)

    # First check run: should detect interruption and send 1 notification
    checker.run()

    user_interruption_emails = [e for e in emails_sent if e["to"] == test_email and "price check update" in e["subject"].lower()]
    assert len(user_interruption_emails) == 1

    # Verify that database records last_interrupted_at
    conn = db()
    row = conn.execute("SELECT last_interrupted_at FROM tracked_flights WHERE id = ?", (flight_id,)).fetchone()
    conn.close()
    assert row["last_interrupted_at"] is not None

    # Second check run: provider still failing, should NOT send duplicate email
    checker.run()

    user_interruption_emails_after = [e for e in emails_sent if e["to"] == test_email and "price check update" in e["subject"].lower()]
    assert len(user_interruption_emails_after) == 1  # Deduplicated!


# ==============================================================================
# 5. TRUTHFULNESS: FAILED / SIMULATED RESPONSES NEVER TRIGGER PRICE-DROPS
# ==============================================================================

def test_simulated_and_cached_flights_never_trigger_false_price_drop(monkeypatch):
    """
    Verify that cached, simulated, or benchmark provider results NEVER trigger
    price-drop notifications.
    """
    conn = db()
    test_email = "truth_test_user@example.com"
    conn.execute(
        """
        INSERT INTO tracked_flights
        (departure, arrival, outbound_date, target_price, lowest_price, target_alert_sent, created_at, email)
        VALUES ('DEL', 'CCU', '2026-12-25', 5000, 8000, 0, '2026-01-01T00:00:00Z', ?)
        """,
        (test_email,)
    )
    conn.commit()
    conn.close()

    emails_sent = []

    def mock_send_email(to_email, subject, body, html_body=None):
        emails_sent.append({"to": to_email, "subject": subject, "body": body})

    monkeypatch.setattr(checker, "send_email", mock_send_email)
    monkeypatch.setenv("SERPAPI_KEY", "mock_key")

    # Mock search_flights returning a lower price (4000) but marked as simulated!
    def mock_simulated_search(dep, arr, out, ret=None):
        return [{
            "price": 4000.0,
            "airline": "Simulated Air",
            "is_simulated": True,
            "data_status": "demonstration"
        }]

    monkeypatch.setattr(checker, "search_flights", mock_simulated_search)

    checker.run()

    # Must NOT have sent any price drop email for simulated data!
    drop_alerts = [e for e in emails_sent if e["to"] == test_email and "dropped" in e["subject"].lower()]
    assert len(drop_alerts) == 0


def test_rfc5322_header_injection_scrubbing():
    """Verify that email header injection attacks (newline characters) are neutralized."""
    with pytest.raises(ValueError):
        scrub_email_address("victim@example.com\r\nBcc: hacker@example.com")

    with pytest.raises(ValueError):
        scrub_email_address("invalid-email-no-domain")

    valid = scrub_email_address("  patron.travel@skytracker.luxury  ")
    assert valid == "patron.travel@skytracker.luxury"
