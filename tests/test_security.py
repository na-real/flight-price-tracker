import pytest
import sqlite3
import urllib.parse
from app import app
from security import (
    limiter,
    generate_delete_token,
    verify_delete_token,
    check_bot_honeypot,
    apply_security_headers,
    SECRET_KEY,
)
from validation import validate_email, validate_iata_code
import db


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# =====================================================================
# 1. INPUT SANITIZATION & INJECTION DEFENSE
# =====================================================================

def test_validate_email_prevents_header_injection():
    """Ensure newline characters for SMTP header injection are blocked."""
    with pytest.raises(ValueError, match="newline characters detected"):
        validate_email("user@example.com\r\nBcc:victim@target.com")

    with pytest.raises(ValueError, match="newline characters detected"):
        validate_email("user@example.com\nBcc:victim@target.com")


def test_validate_email_enforces_format():
    """Ensure malformed or non-email strings are rejected."""
    with pytest.raises(ValueError, match="valid email address"):
        validate_email("not-an-email")

    with pytest.raises(ValueError, match="valid email address"):
        validate_email("missing-domain@")

    with pytest.raises(ValueError, match="cannot exceed 254 characters"):
        validate_email("a" * 250 + "@example.com")

    # Valid email succeeds
    assert validate_email("legit.traveler@example.com") == "legit.traveler@example.com"


def test_validate_iata_code():
    """Ensure airport codes must be strictly 3 alphabetical letters."""
    assert validate_iata_code("del") == "DEL"
    assert validate_iata_code("  jfk  ") == "JFK"

    with pytest.raises(ValueError, match="valid 3-letter IATA"):
        validate_iata_code("TOOLONG")

    with pytest.raises(ValueError, match="valid 3-letter IATA"):
        validate_iata_code("12")

    with pytest.raises(ValueError, match="valid 3-letter IATA"):
        validate_iata_code("$$$")


def test_search_rejects_identical_endpoints(client):
    """Ensure origin and destination cannot be identical in flight search."""
    response = client.post(
        "/api/search",
        json={
            "departure": "DEL",
            "arrival": "DEL",
            "outbound_date": "2026-11-20",
        },
    )
    assert response.status_code == 400
    data = response.get_json()
    assert "cannot be identical" in data["error"]


def test_track_rejects_identical_endpoints(client):
    """Ensure origin and destination cannot be identical in flight tracking."""
    response = client.post(
        "/api/track",
        json={
            "departure": "BOM",
            "arrival": "BOM",
            "outbound_date": "2026-11-20",
            "target_price": 5000,
            "email": "pilot@example.com",
        },
    )
    assert response.status_code == 400
    data = response.get_json()
    assert "cannot be identical" in data["error"]


def test_track_rejects_malformed_email(client):
    """Ensure invalid email rejected on /api/track."""
    response = client.post(
        "/api/track",
        json={
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": "2026-11-20",
            "target_price": 5000,
            "email": "injection\r\nBcc:victim@test.com",
        },
    )
    assert response.status_code == 400
    data = response.get_json()
    assert "newline characters" in data["error"]


# =====================================================================
# 2. ANTI-BOT HONEYPOT DEFENSE
# =====================================================================

def test_honeypot_detection():
    """Ensure hidden bot traps detect automated form submissions."""
    with pytest.raises(ValueError, match="Automated submission detected"):
        check_bot_honeypot({"website": "http://spambot.xyz"})

    with pytest.raises(ValueError, match="Automated submission detected"):
        check_bot_honeypot({"phone_trap": "123456789"})

    # Legitimate user payload passes
    check_bot_honeypot({"departure": "DEL", "arrival": "LHR"})


def test_track_rejects_bot_with_honeypot(client):
    """Ensure automated bot filling honeypot is blocked on /api/track."""
    response = client.post(
        "/api/track",
        json={
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": "2026-11-20",
            "target_price": 5000,
            "email": "spammer@bot.com",
            "website": "http://malicious-link.biz",
        },
    )
    assert response.status_code == 400
    data = response.get_json()
    assert "Automated submission" in data["error"]


# =====================================================================
# 3. IDOR MITIGATION & OBJECT AUTHORIZATION
# =====================================================================

def test_delete_non_existent_tracked_flight_returns_404(client, tmp_path, monkeypatch):
    """Deleting a non-existent flight ID must return 404, not 200."""
    test_database = tmp_path / "test_idor.db"
    monkeypatch.setattr(db, "DB_PATH", str(test_database))
    db.init_db()

    response = client.delete("/api/tracked/999999")
    assert response.status_code == 404
    data = response.get_json()
    assert "not found" in data["error"]


def test_history_non_existent_tracked_flight_returns_404(client, tmp_path, monkeypatch):
    """Requesting history for non-existent flight ID must return 404."""
    test_database = tmp_path / "test_history.db"
    monkeypatch.setattr(db, "DB_PATH", str(test_database))
    db.init_db()

    response = client.get("/api/history/999999")
    assert response.status_code == 404
    data = response.get_json()
    assert "not found" in data["error"]


def test_deletion_token_verification(tmp_path, monkeypatch):
    """Verify cryptographic HMAC deletion tokens."""
    token = generate_delete_token(42, "2026-10-15T00:00:00Z")
    assert isinstance(token, str)
    assert len(token) == 64

    assert verify_delete_token(42, "2026-10-15T00:00:00Z", token) is True
    assert verify_delete_token(42, "2026-10-15T00:00:00Z", "wrongtoken") is False
    assert verify_delete_token(99, "2026-10-15T00:00:00Z", token) is False


def test_authorized_deletion_flow(client, tmp_path, monkeypatch):
    """Create a tracked flight, receive token, delete with token."""
    test_database = tmp_path / "test_auth_delete.db"
    monkeypatch.setattr(db, "DB_PATH", str(test_database))
    db.init_db()

    # Create flight
    create_res = client.post(
        "/api/track",
        json={
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": "2026-11-20",
            "target_price": 4500,
            "email": "owner@example.com",
        },
    )
    assert create_res.status_code == 200
    create_data = create_res.get_json()
    tracked_id = create_data["id"]
    delete_token = create_data["delete_token"]
    assert len(delete_token) == 64  # Full 256-bit HMAC-SHA256 hex digest

    # Attempt deletion WITHOUT ANY token -> MUST return 403 Forbidden
    no_token_res = client.delete(f"/api/tracked/{tracked_id}")
    assert no_token_res.status_code == 403
    assert "token required" in no_token_res.get_json()["error"].lower()

    # Attempt deletion with EMPTY token -> MUST return 403 Forbidden
    empty_token_res = client.delete(
        f"/api/tracked/{tracked_id}",
        headers={"X-Delete-Token": "   "}
    )
    assert empty_token_res.status_code == 403

    # Attempt deletion with invalid token -> 403
    forbidden_res = client.delete(
        f"/api/tracked/{tracked_id}",
        headers={"X-Delete-Token": "invalid_forged_token"}
    )
    assert forbidden_res.status_code == 403
    assert "invalid" in forbidden_res.get_json()["error"].lower()

    # Delete with valid token -> 200
    success_res = client.delete(
        f"/api/tracked/{tracked_id}",
        headers={"X-Delete-Token": delete_token}
    )
    assert success_res.status_code == 200

    # Ensure record was actually purged from database
    purged_res = client.get(f"/api/history/{tracked_id}")
    assert purged_res.status_code == 404


def test_deletion_via_query_param_token_rejected(client, tmp_path, monkeypatch):
    """Ensure delete token is rejected via query parameters and requires X-Delete-Token header."""
    test_database = tmp_path / "test_query_token_delete.db"
    monkeypatch.setattr(db, "DB_PATH", str(test_database))
    db.init_db()

    create_res = client.post(
        "/api/track",
        json={
            "departure": "LHR",
            "arrival": "HND",
            "outbound_date": "2026-11-20",
            "target_price": 55000,
            "email": "traveler@example.com",
        },
    )
    assert create_res.status_code == 200
    data = create_res.get_json()
    tracked_id = data["id"]
    delete_token = data["delete_token"]
    assert len(delete_token) == 64

    # Deleting via query parameter MUST be rejected with 403 Forbidden
    query_res = client.delete(f"/api/tracked/{tracked_id}?token={delete_token}")
    assert query_res.status_code == 403
    assert "token required" in query_res.get_json()["error"].lower()

    # Deleting with valid X-Delete-Token header succeeds
    header_res = client.delete(
        f"/api/tracked/{tracked_id}",
        headers={"X-Delete-Token": delete_token}
    )
    assert header_res.status_code == 200


def test_cross_record_delete_token_replay_rejected(client, tmp_path, monkeypatch):
    """Ensure a deletion token generated for Flight A cannot delete Flight B."""
    test_database = tmp_path / "test_cross_record.db"
    monkeypatch.setattr(db, "DB_PATH", str(test_database))
    db.init_db()

    # Create Flight A
    res_a = client.post("/api/track", json={
        "departure": "DEL", "arrival": "BOM",
        "outbound_date": "2026-11-20", "target_price": 5000,
        "email": "user_a@example.com"
    })
    assert res_a.status_code == 200
    data_a = res_a.get_json()
    token_a = data_a["delete_token"]

    # Create Flight B
    res_b = client.post("/api/track", json={
        "departure": "JFK", "arrival": "LHR",
        "outbound_date": "2026-12-01", "target_price": 40000,
        "email": "user_b@example.com"
    })
    assert res_b.status_code == 200
    data_b = res_b.get_json()
    id_b = data_b["id"]
    token_b = data_b["delete_token"]

    # Replay Token A against Flight B -> MUST be rejected with 403
    replay_res = client.delete(
        f"/api/tracked/{id_b}",
        headers={"X-Delete-Token": token_a}
    )
    assert replay_res.status_code == 403
    assert "invalid" in replay_res.get_json()["error"].lower()

    # Flight B must still exist and its history is protected
    check_b_unauth = client.get(f"/api/history/{id_b}")
    assert check_b_unauth.status_code == 403

    # Flight B history is accessible with its valid token
    check_b = client.get(
        f"/api/history/{id_b}",
        headers={"X-Delete-Token": token_b}
    )
    assert check_b.status_code == 200

    # Flight B can be deleted with its own token
    delete_b = client.delete(
        f"/api/tracked/{id_b}",
        headers={"X-Delete-Token": token_b}
    )
    assert delete_b.status_code == 200


def test_login_open_redirect_protection(client, tmp_path, monkeypatch):
    """Ensure /login rejects external or protocol-relative redirect targets (CWE-601)."""
    test_database = tmp_path / "test_redirect.db"
    monkeypatch.setattr(db, "DB_PATH", str(test_database))
    db.init_db()

    # Register test user
    client.post("/signup", json={
        "name": "Sovereign Tester",
        "email": "tester@skytrack.luxury",
        "password": "valid_secure_pass123",
        "confirm_password": "valid_secure_pass123"
    })
    client.post("/logout")

    malicious_targets = [
        "https://evil.com/phish",
        "http://attacker.com",
        "//malicious.com",
        "/\\evil.com",
        "javascript:alert(1)",
    ]

    for target in malicious_targets:
        res = client.post(
            f"/login?next={target}",
            json={
                "email": "tester@skytrack.luxury",
                "password": "valid_secure_pass123"
            }
        )
        assert res.status_code == 200
        assert res.get_json()["redirect"] == "/dashboard"

    # Legitimate local relative redirect must succeed
    safe_target = "/flights?departure=DEL&arrival=BOM"
    res_safe = client.post(
        f"/login?next={urllib.parse.quote(safe_target)}",
        json={
            "email": "tester@skytrack.luxury",
            "password": "valid_secure_pass123"
        }
    )
    assert res_safe.status_code == 200
    assert res_safe.get_json()["redirect"] == safe_target


def test_user_authentication_password_hashing_and_isolation(client, tmp_path, monkeypatch):
    """Ensure user accounts use persistent password hashing and isolate private tracked data."""
    test_database = tmp_path / "test_auth_isolation.db"
    monkeypatch.setattr(db, "DB_PATH", str(test_database))
    db.init_db()

    # 1. Signup Alice
    res_signup = client.post("/signup", json={
        "name": "Alice Sovereign",
        "email": "alice@skytrack.luxury",
        "password": "alice_secret_pass",
        "confirm_password": "alice_secret_pass"
    })
    assert res_signup.status_code == 200

    # Verify password hash in database is NOT plaintext
    conn = sqlite3.connect(test_database)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT email, name, password_hash FROM users WHERE email = ?", ("alice@skytrack.luxury",)).fetchone()
    conn.close()
    assert row is not None
    assert row["name"] == "Alice Sovereign"
    assert "alice_secret_pass" not in row["password_hash"]
    assert row["password_hash"].startswith("pbkdf2:") or row["password_hash"].startswith("scrypt:")

    # 2. Duplicate signup rejected
    res_dup = client.post("/signup", json={
        "name": "Alice Clone",
        "email": "alice@skytrack.luxury",
        "password": "another_pass1234",
        "confirm_password": "another_pass1234"
    })
    assert res_dup.status_code == 400
    assert "already exists" in res_dup.get_json()["error"].lower()

    # 3. Login with invalid password rejected
    client.post("/logout")
    res_bad_login = client.post("/login", json={
        "email": "alice@skytrack.luxury",
        "password": "wrong_password"
    })
    assert res_bad_login.status_code == 401

    # 4. Login with correct password succeeds
    res_ok_login = client.post("/login", json={
        "email": "alice@skytrack.luxury",
        "password": "alice_secret_pass"
    })
    assert res_ok_login.status_code == 200

    # 5. Alice tracks a flight
    track_res = client.post("/api/track", json={
        "departure": "DEL", "arrival": "BOM",
        "outbound_date": "2026-11-20", "target_price": 5000,
        "email": "alice@skytrack.luxury"
    })
    assert track_res.status_code == 200

    # 6. Alice sees her flight on /api/tracked
    alice_feed = client.get("/api/tracked")
    assert alice_feed.status_code == 200
    alice_flights = alice_feed.get_json()
    assert len(alice_flights) == 1
    assert alice_flights[0]["departure"] == "DEL"

    # 7. Unauthenticated user sees NO flights on /api/tracked (privacy protection)
    client.post("/logout")
    anon_feed = client.get("/api/tracked")
    assert anon_feed.status_code == 200
    assert anon_feed.get_json() == []

    # 8. Bob logs in -> Bob sees NO flights on /api/tracked (tenant isolation)
    client.post("/signup", json={
        "name": "Bob Sovereign",
        "email": "bob@skytrack.luxury",
        "password": "bob_secure_pass",
        "confirm_password": "bob_secure_pass"
    })
    bob_feed = client.get("/api/tracked")
    assert bob_feed.status_code == 200
    assert bob_feed.get_json() == []


def test_production_secret_key_enforcement(monkeypatch):
    """Ensure missing SECRET_KEY in production raises RuntimeError."""
    from security import _load_secret_key

    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SECRET_KEY environment variable must be set in production"):
        _load_secret_key()


def test_production_secret_key_insecure_placeholder(monkeypatch):
    """Ensure placeholder SECRET_KEY in production raises RuntimeError."""
    from security import _load_secret_key

    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "replace_with_64_character_cryptographically_secure_random_hex_key")

    with pytest.raises(RuntimeError, match="SECRET_KEY environment variable is insecure"):
        _load_secret_key()


def test_production_secret_key_too_short(monkeypatch):
    """Ensure too-short SECRET_KEY in production raises RuntimeError."""
    from security import _load_secret_key

    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "insecure_short_key_123")

    with pytest.raises(RuntimeError, match="SECRET_KEY environment variable is insecure"):
        _load_secret_key()


def test_production_secret_key_valid(monkeypatch):
    """Ensure strong SECRET_KEY in production succeeds."""
    from security import _load_secret_key

    valid_key = "a" * 64
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", valid_key)

    assert _load_secret_key() == valid_key


def test_development_secret_key_fallback(monkeypatch):
    """Ensure missing SECRET_KEY in development generates fallback key without raising."""
    from security import _load_secret_key

    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    key = _load_secret_key()
    assert key.startswith("skytracker-dev-entropy-")


# =====================================================================
# 4. DEFENSE-IN-DEPTH SECURITY HEADERS
# =====================================================================

def test_full_security_headers_present(client):
    """Verify complete CSP, Frame protection, Referrer, and MIME headers."""
    response = client.get("/")
    headers = response.headers

    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "strict-origin-when-cross-origin" in headers.get("Referrer-Policy", "")
    assert "geolocation=()" in headers.get("Permissions-Policy", "")

    # Content-Security-Policy
    csp = headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp


# =====================================================================
# 5. RATE LIMITING ENGINE
# =====================================================================

def test_sliding_window_rate_limiter():
    """Verify sliding window accurately limits and resets requests."""
    limiter.clear()
    key = "test_ip_123"

    # Allow 3 requests in a 10s window
    allowed1, rem1, _ = limiter.is_allowed(key, max_requests=3, window_seconds=10)
    assert allowed1 is True
    assert rem1 == 2

    allowed2, rem2, _ = limiter.is_allowed(key, max_requests=3, window_seconds=10)
    assert allowed2 is True
    assert rem2 == 1

    allowed3, rem3, _ = limiter.is_allowed(key, max_requests=3, window_seconds=10)
    assert allowed3 is True
    assert rem3 == 0

    # 4th request must be rejected
    allowed4, rem4, retry_after = limiter.is_allowed(key, max_requests=3, window_seconds=10)
    assert allowed4 is False
    assert rem4 == 0
    assert retry_after > 0


# =====================================================================
# 6. FIX 1 REGRESSION: PRICE HISTORY ACCESS CONTROL & IDOR PROTECTION
# =====================================================================

def test_price_history_authorization_matrix(client, tmp_path, monkeypatch):
    """
    FIX 1: Verify /api/history/<id> requires either:
    1. An authenticated session matching the flight owner's email, OR
    2. A valid record-specific HMAC token in X-Delete-Token / X-Auth-Token header.
    Unauthenticated requests without a token or with a mismatched user/token return 403.
    """
    test_database = tmp_path / "test_history_auth.db"
    monkeypatch.setattr(db, "DB_PATH", str(test_database))
    db.init_db()

    # 1. Track a flight under Charlie's email
    res_track = client.post("/api/track", json={
        "departure": "DEL",
        "arrival": "MAA",
        "outbound_date": "2026-11-20",
        "target_price": 6000,
        "email": "charlie@skytrack.luxury"
    })
    assert res_track.status_code == 200
    track_data = res_track.get_json()
    flight_id = track_data["id"]
    token = track_data["delete_token"]

    # Insert sample price history record
    conn = sqlite3.connect(test_database)
    conn.execute(
        "INSERT INTO price_history (tracked_id, price, checked_at) VALUES (?, ?, ?)",
        (flight_id, 5800.0, "2026-10-15T10:00:00Z")
    )
    conn.commit()
    conn.close()

    # Test A: Anonymous access without token -> MUST return 403 Forbidden
    res_anon_no_token = client.get(f"/api/history/{flight_id}")
    assert res_anon_no_token.status_code == 403
    assert "unauthorized" in res_anon_no_token.get_json()["error"].lower()

    # Test B: Anonymous access with invalid/forged token -> MUST return 403 Forbidden
    res_anon_bad_token = client.get(
        f"/api/history/{flight_id}",
        headers={"X-Delete-Token": "bad_forged_token_12345"}
    )
    assert res_anon_bad_token.status_code == 403

    # Test C: Anonymous access with valid X-Delete-Token header -> MUST return 200 OK
    res_anon_valid_del_token = client.get(
        f"/api/history/{flight_id}",
        headers={"X-Delete-Token": token}
    )
    assert res_anon_valid_del_token.status_code == 200
    history_items = res_anon_valid_del_token.get_json()
    assert len(history_items) == 1
    assert history_items[0]["price"] == 5800.0

    # Test D: Anonymous access with valid X-Auth-Token header -> MUST return 200 OK
    res_anon_valid_auth_token = client.get(
        f"/api/history/{flight_id}",
        headers={"X-Auth-Token": token}
    )
    assert res_anon_valid_auth_token.status_code == 200

    # Test E: User Dave logs in (non-owner) -> Accessing Charlie's flight history without token returns 403
    client.post("/signup", json={
        "name": "Dave Traveler",
        "email": "dave@skytrack.luxury",
        "password": "dave_password123",
        "confirm_password": "dave_password123"
    })
    res_dave = client.get(f"/api/history/{flight_id}")
    assert res_dave.status_code == 403

    # Test F: User Charlie logs in (owner) -> Accessing own flight history without token succeeds
    client.post("/logout")
    client.post("/signup", json={
        "name": "Charlie Sovereign",
        "email": "charlie@skytrack.luxury",
        "password": "charlie_password123",
        "confirm_password": "charlie_password123"
    })
    res_charlie = client.get(f"/api/history/{flight_id}")
    assert res_charlie.status_code == 200
    assert len(res_charlie.get_json()) == 1

    # Test G: Querying non-existent flight returns 404
    res_404 = client.get("/api/history/999999")
    assert res_404.status_code == 404


# =====================================================================
# 7. FIX 2 REGRESSION: AUTHENTICATION RATE LIMITING
# =====================================================================

def test_login_and_signup_rate_limiting(client, tmp_path, monkeypatch):
    """
    FIX 2: Verify repeated POST requests to /login and /signup trigger 429 Too Many Requests,
    while GET requests that view the forms remain unblocked.
    """
    test_database = tmp_path / "test_rate_limits.db"
    monkeypatch.setattr(db, "DB_PATH", str(test_database))
    db.init_db()

    client.application.config["TESTING_RATE_LIMITS"] = True
    try:
        limiter.clear()

        # 1. Test POST /login rate limiting (limit=10)
        for i in range(10):
            res = client.post("/login", json={
                "email": f"attacker_{i}@domain.com",
                "password": "wrong_password"
            })
            assert res.status_code in (400, 401)

        # 11th attempt must be throttled with HTTP 429
        throttled_login = client.post("/login", json={
            "email": "attacker@domain.com",
            "password": "wrong_password"
        })
        assert throttled_login.status_code == 429
        assert "too many requests" in throttled_login.get_json()["error"].lower()
        assert "Retry-After" in throttled_login.headers

        # GET /login must NOT be blocked by POST rate limit
        res_get_login = client.get("/login")
        assert res_get_login.status_code == 200

        # 2. Test POST /signup rate limiting (limit=5)
        limiter.clear()
        for i in range(5):
            res_signup = client.post("/signup", json={
                "name": f"Bot {i}",
                "email": f"bot_{i}@spam.com",
                "password": "short",
                "confirm_password": "short"
            })
            assert res_signup.status_code == 400

        # 6th attempt must be throttled with HTTP 429
        throttled_signup = client.post("/signup", json={
            "name": "Bot 6",
            "email": "bot_6@spam.com",
            "password": "password12345",
            "confirm_password": "password12345"
        })
        assert throttled_signup.status_code == 429
        assert "too many requests" in throttled_signup.get_json()["error"].lower()

        # GET /signup must NOT be blocked by POST rate limit
        res_get_signup = client.get("/signup")
        assert res_get_signup.status_code == 200

    finally:
        client.application.config["TESTING_RATE_LIMITS"] = False
        limiter.clear()


# =====================================================================
# 8. FIX 3 REGRESSION: SESSION CLEARING ON LOGIN & POST-ONLY LOGOUT
# =====================================================================

def test_session_clearing_and_post_only_logout(client, tmp_path, monkeypatch):
    """
    FIX 3: Verify that:
    1. GET /logout returns 405 Method Not Allowed and preserves active session.
    2. POST /logout clears session and redirects or returns JSON.
    3. Login clears pre-existing session attributes before storing authenticated user.
    """
    test_database = tmp_path / "test_session_hardening.db"
    monkeypatch.setattr(db, "DB_PATH", str(test_database))
    db.init_db()

    # Create user
    res_reg = client.post("/signup", json={
        "name": "Eve Sovereign",
        "email": "eve@skytrack.luxury",
        "password": "eve_secure_pass123",
        "confirm_password": "eve_secure_pass123"
    })
    assert res_reg.status_code == 200

    # User is logged in: dashboard accessible
    assert client.get("/dashboard").status_code == 200

    # Attempt GET /logout -> MUST return 405 Method Not Allowed
    get_logout_res = client.get("/logout")
    assert get_logout_res.status_code == 405

    # Session MUST NOT be terminated by GET /logout (Logout CSRF protection)
    assert client.get("/dashboard").status_code == 200

    # Execute POST /logout -> MUST succeed and clear session
    post_logout_res = client.post("/logout")
    assert post_logout_res.status_code == 302
    assert post_logout_res.headers["Location"] == "/"

    # Dashboard now redirects to login
    assert client.get("/dashboard").status_code == 302

    # JSON POST /logout also supported
    client.post("/login", json={"email": "eve@skytrack.luxury", "password": "eve_secure_pass123"})
    json_logout_res = client.post("/logout", headers={"Content-Type": "application/json"})
    assert json_logout_res.status_code == 200
    assert json_logout_res.get_json()["success"] is True

    # Verify session.clear() on login: set pre-existing key
    with client.session_transaction() as sess:
        sess["untrusted_pre_auth_data"] = "attacker_controlled_value"

    # Log in as Eve
    client.post("/login", json={"email": "eve@skytrack.luxury", "password": "eve_secure_pass123"})

    with client.session_transaction() as sess:
        assert sess.get("user_email") == "eve@skytrack.luxury"
        assert "untrusted_pre_auth_data" not in sess
