import json
import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key-for-appearance-tests-32bytes!!"
    with app.test_client() as client:
        yield client


def test_appearance_settings_page_route(client):
    """Verify that /settings/appearance renders successfully with all required sections."""
    response = client.get("/settings/appearance")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Settings" in html
    assert "Visual Theme" in html
    assert "Backdrop Effect" in html
    assert "Custom Colour" in html
    assert "Restore Default Appearance" in html
    assert "SkyTracker Standalone Experience" in html
    assert 'data-theme="signature"' in html
    assert 'data-theme="dark"' in html
    assert 'data-theme="monochrome"' in html
    assert 'data-theme="high-contrast"' in html
    assert 'data-theme="midnight"' in html
    assert 'data-theme="custom"' in html


def test_settings_redirect_route(client):
    """Verify that /settings redirects cleanly to /settings/appearance."""
    response = client.get("/settings", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings/appearance")


def test_manifest_webmanifest_route(client):
    """Verify that /manifest.webmanifest serves a valid PWA manifest."""
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert "application/manifest+json" in response.headers.get("Content-Type", "") or "json" in response.headers.get("Content-Type", "")
    data = json.loads(response.get_data(as_text=True))
    assert "SkyTracker" in data["name"]
    assert data["short_name"] == "SkyTracker"
    assert data["display"] == "standalone"
    assert len(data["icons"]) > 0


def test_manifest_json_alias_route(client):
    """Verify /manifest.json alias works identically."""
    response = client.get("/manifest.json")
    assert response.status_code == 200
    data = json.loads(response.get_data(as_text=True))
    assert data["short_name"] == "SkyTracker"


def test_service_worker_route(client):
    """Verify /sw.js serves Service Worker with Service-Worker-Allowed root scope header."""
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.headers.get("Content-Type", "")
    assert response.headers.get("Service-Worker-Allowed") == "/"
    content = response.get_data(as_text=True)
    assert "CACHE_NAME" in content
    assert "addEventListener" in content


def test_appearance_preferences_api_get_default(client):
    """Verify /api/appearance/preferences returns default preferences when none saved."""
    response = client.get("/api/appearance/preferences")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["preferences"]["theme"] in ["signature", "dark", "monochrome", "high-contrast", "midnight", "custom"]
    assert data["preferences"]["backdropEffect"] == "default"


def test_appearance_preferences_api_post_valid(client):
    """Verify saving appearance preferences persists correctly in session."""
    payload = {
        "theme": "midnight",
        "backdropEffect": "ferrofluid",
        "accentColor": "#00A8FF",
        "reducedMotion": True
    }
    response = client.post("/api/appearance/preferences", json=payload)
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["preferences"]["theme"] == "midnight"
    assert data["preferences"]["backdropEffect"] == "ferrofluid"
    assert data["preferences"]["accentColor"] == "#00A8FF"
    assert data["preferences"]["reducedMotion"] is True

    # Check that subsequent GET returns the persisted values
    get_res = client.get("/api/appearance/preferences")
    assert get_res.status_code == 200
    get_data = get_res.get_json()
    assert get_data["preferences"]["theme"] == "midnight"
    assert get_data["preferences"]["backdropEffect"] == "ferrofluid"


def test_appearance_preferences_api_post_sanitization(client):
    """Verify malicious or invalid inputs are safely sanitized."""
    payload = {
        "theme": "invalid-theme-injection",
        "backdropEffect": "malicious<script>",
        "accentColor": "not-a-color-at-all",
        "reducedMotion": "not-a-bool"
    }
    response = client.post("/api/appearance/preferences", json=payload)
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["preferences"]["theme"] == "signature"
    assert data["preferences"]["backdropEffect"] == "default"
    assert data["preferences"]["accentColor"] == "#DFC99E"
    assert data["preferences"]["reducedMotion"] is False


def test_css_themes_present_in_stylesheet(client):
    """Verify all 6 themes exist in the stylesheet."""
    response = client.get("/static/style.css")
    assert response.status_code == 200
    css = response.get_data(as_text=True)
    assert '[data-theme="signature"]' in css
    assert '[data-theme="dark"]' in css
    assert '[data-theme="monochrome"]' in css
    assert '[data-theme="high-contrast"]' in css
    assert '[data-theme="midnight"]' in css
    assert '[data-theme="custom"]' in css
    assert '.mobile-bottom-nav' in css
    assert '.dynamic-backdrop-canvas' in css
