# pyrefly: ignore [missing-import]
import pytest

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True

    with app.test_client() as client:
        yield client


def test_airports_route(client):
    response = client.get("/api/airports?q=delhi")

    assert response.status_code == 200

    data = response.get_json()

    assert isinstance(data, list)
    assert len(data) > 0


def test_search_route(client, monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)

    response = client.post(
        "/api/search",
        json={
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": "2026-11-15",
        },
    )

    assert response.status_code == 200

    data = response.get_json()

    assert isinstance(data, dict)
    assert "results" in data
    assert "search_airports" in data

    results = data["results"]

    assert isinstance(results, list)
    assert len(results) > 0

    assert "price" in results[0]
    assert "airline" in results[0]


def test_track_route(client, monkeypatch, tmp_path):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)

    import db

    test_database = tmp_path / "test_flights.db"

    monkeypatch.setattr(db, "DB_PATH", str(test_database))

    db.init_db()

    response = client.post(
        "/api/track",
        json={
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": "2026-11-15",
            "target_price": "5000",
            "email": "test@example.com",
        },
    )

    assert response.status_code == 200

    data = response.get_json()

    assert "message" in data


def test_robots_txt(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "text/plain" in response.content_type
    content = response.get_data(as_text=True)
    assert "User-agent: *" in content
    assert "Disallow: /api/" in content
    assert "Sitemap:" in content


def test_sitemap_xml(client):
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "xml" in response.content_type
    content = response.get_data(as_text=True)
    assert "<urlset" in content
    assert "<loc>" in content
    assert "/privacy" in content


def test_privacy_page(client):
    response = client.get("/privacy")
    assert response.status_code == 200
    content = response.get_data(as_text=True)
    assert "Privacy Policy" in content
    assert "SkyTrack" in content


def test_custom_404_handler(client):
    response = client.get("/this-route-does-not-exist-404")
    assert response.status_code == 404
    content = response.get_data(as_text=True)
    assert "404" in content
    assert "Return to flight search" in content or "SkyTrack" in content


def test_security_headers(client):
    response = client.get("/")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "strict-origin-when-cross-origin" in response.headers.get("Referrer-Policy", "")
    assert "geolocation=()" in response.headers.get("Permissions-Policy", "")


def test_radar_status(client):
    response = client.get("/api/radar/status")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, dict)
    assert "status" in data
    assert data["status"] in ["Active", "Paused", "Error", "Unknown"]
    assert "status_detail" in data
    assert "tracked_count" in data


def test_multipage_routes_render(client):
    """Verify that all newly created pages return HTTP 200 and render brand navigation."""
    pages = [
        ("/", "SkyTracker"),
        ("/flights", "Explore Global Horizons"),
        ("/compare", "Flight Comparison Intelligence"),
        ("/tracking", "Radar Price Tracking"),
        ("/about", "Philosophy of Quiet Flight Intelligence"),
        ("/contact", "Direct Inquiries & Concierge"),
        ("/terms", "Terms of Service"),
        ("/login", "Authenticate Access"),
        ("/signup", "Establish Account"),
    ]
    for url, keyword in pages:
        res = client.get(url)
        assert res.status_code == 200, f"Page {url} failed with status {res.status_code}"
        content = res.get_data(as_text=True)
        assert keyword in content, f"Keyword '{keyword}' not found in {url}"
        assert "SKYTRACKER" in content


def test_contact_api_validation(client):
    """Verify server-side validation and honeypot in /api/contact."""
    # Honeypot triggered
    res_hp = client.post("/api/contact", json={
        "name": "Bot User",
        "email": "bot@spam.com",
        "message": "Spam message here",
        "hp_username": "im_a_bot"
    })
    assert res_hp.status_code == 200
    assert res_hp.get_json()["success"] is True

    # Invalid email
    res_bad_email = client.post("/api/contact", json={
        "name": "Legit User",
        "email": "invalid-email",
        "message": "Hello this is a valid message length."
    })
    assert res_bad_email.status_code == 400

    # Valid inquiry
    res_ok = client.post("/api/contact", json={
        "name": "Traveler Sovereign",
        "email": "sovereign@skytrack.luxury",
        "subject": "Flight Intelligence & Arbitrage",
        "message": "Requesting corporate flight intelligence telemetry feed."
    })
    assert res_ok.status_code == 200
    data = res_ok.get_json()
    assert data["success"] is True


def test_auth_and_protected_dashboard(client, tmp_path, monkeypatch):
    """Verify login, session protection on /dashboard, and logout."""
    import db
    test_db_path = tmp_path / "test_auth_routes.db"
    monkeypatch.setattr(db, "DB_PATH", str(test_db_path))
    db.init_db()

    # Unauthenticated dashboard redirects to login
    res_unauth = client.get("/dashboard")
    assert res_unauth.status_code == 302
    assert "/login" in res_unauth.headers.get("Location", "")

    # First register user via /signup
    res_signup = client.post("/signup", json={
        "name": "Patron Sovereign",
        "email": "patron@skytrack.luxury",
        "password": "secret_keyword_pass",
        "confirm_password": "secret_keyword_pass"
    })
    assert res_signup.status_code == 200
    assert res_signup.get_json()["success"] is True

    # Clear session to verify subsequent login
    client.post("/logout")

    # Valid login creates session
    res_login = client.post("/login", json={
        "email": "patron@skytrack.luxury",
        "password": "secret_keyword_pass"
    })
    assert res_login.status_code == 200
    assert res_login.get_json()["success"] is True

    # Authenticated user can view dashboard
    res_dash = client.get("/dashboard")
    assert res_dash.status_code == 200
    content = res_dash.get_data(as_text=True)
    assert "Personal Flight Command" in content
    assert "patron@skytrack.luxury" in content

    # Logout via POST clears session
    res_logout = client.post("/logout")
    assert res_logout.status_code == 302
    res_after = client.get("/dashboard")
    assert res_after.status_code == 302
