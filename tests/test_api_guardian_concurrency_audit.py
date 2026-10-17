"""
Pre-Deployment Security and Concurrency Audit Tests for SkyTracker API Guardian.
Validates:
A. Multi-process quota enforcement & atomic reservation admission.
B. Reservation lifecycle: long requests, extend_reservation heartbeats, crash recovery, duplicate finalization, conservative reconciliation.
C. Budget invariants: 25-credit ordinary limit, 30-credit lifetime hard cap, unknown cost blocking, zero extra reservations for cache/coalesced queries.
D. Billing and provider responses: conservative accounting on timeouts, connection errors, 5xx; zero cost on 400/401/403/429.
E. Security review: admin tokens strictly prohibited in query strings, header auth enforced, reset never erases logs or replenishes credits, secret scrubbing.
"""

import json
import os
import threading
import time
import pytest
from datetime import datetime, timezone

from app import app
from db import db
from services.api_guardian import (
    guardian,
    PROVIDER_FLIGHTAPI,
    PROVIDER_SERPAPI,
    PROVIDER_AMADEUS,
    CALLER_USER_SEARCH,
    CALLER_BACKGROUND_CHECK,
    PRIORITY_CRITICAL,
    PRIORITY_NORMAL,
    PRIORITY_BACKGROUND,
    STATE_RESERVED,
    STATE_SUCCEEDED,
    STATE_FAILED,
    STATE_UNKNOWN,
    STATE_CLOSED,
    STATE_OPEN,
    estimate_endpoint_cost,
    sanitize_flightapi_url,
)


def _clean_tables():
    """Wipe guardian tables for deterministic audit testing."""
    conn = db()
    try:
        conn.execute("DELETE FROM api_reservations")
        conn.execute("DELETE FROM api_quota_logs")
        conn.execute("DELETE FROM api_circuit_state")
        conn.execute("DELETE FROM api_alerts_log")
        conn.execute("DELETE FROM api_operator_audit")
        conn.commit()
    finally:
        conn.close()
    guardian.clear_cache()


# =====================================================================
# SECTION A: MULTI-PROCESS & CONCURRENT QUOTA ENFORCEMENT
# =====================================================================

def test_concurrent_multi_connection_reservation_race(monkeypatch):
    """
    Simulate multiple separate concurrent worker threads/processes competing
    for the exact same remaining credit quota before hitting the ceiling.
    Verifies that atomic locking prevents over-allocation.
    """
    _clean_tables()
    monkeypatch.setenv("FLIGHTAPI_MINUTE_BUDGET", "50")
    monkeypatch.setenv("FLIGHTAPI_DAILY_BUDGET", "50")

    # Pre-seed 24 used credits in api_quota_logs (ordinary limit is 25 out of 30)
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = db()
    for i in range(24):
        conn.execute(
            "INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (PROVIDER_FLIGHTAPI, "onewaytrip", CALLER_USER_SEARCH, 0, 200, 1, now_iso)
        )
    conn.commit()
    conn.close()

    # Exactly 1 credit remains for ordinary requests (25 - 24 = 1).
    # Launch 10 simultaneous workers competing to reserve that single remaining credit.
    results = []
    barrier = threading.Barrier(10)

    def worker_attempt():
        barrier.wait()
        allowed, reason, stats, res_id = guardian.reserve_budget(
            PROVIDER_FLIGHTAPI,
            "onewaytrip",
            caller_type=CALLER_USER_SEARCH,
            priority=PRIORITY_NORMAL
        )
        results.append((allowed, res_id, reason))

    threads = [threading.Thread(target=worker_attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successful = [r for r in results if r[0] is True]
    rejected = [r for r in results if r[0] is False]

    # Strictly 1 must succeed and 9 must be rejected!
    assert len(successful) == 1, f"Expected exactly 1 successful reservation, got {len(successful)}"
    assert len(rejected) == 9, f"Expected exactly 9 rejected reservations, got {len(rejected)}"

    # Authoritative DB verification: total reservations + confirmed logs must equal 25
    conn = db()
    cur = conn.execute("SELECT COUNT(*) as cnt FROM api_reservations WHERE state = 'reserved'")
    active_res = cur.fetchone()["cnt"]
    conn.close()
    assert active_res == 1


# =====================================================================
# SECTION B: RESERVATION LIFECYCLE, LEASE EXTENSION & RECONCILIATION
# =====================================================================

def test_extend_reservation_protects_long_running_requests():
    """
    Active requests lasting longer than TTL must be able to extend their lease
    so reconcile_stale_reservations does not prematurely mark them abandoned.
    """
    _clean_tables()

    # 1. Create a reservation
    allowed, reason, stats, res_id = guardian.reserve_budget(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_NORMAL
    )
    assert allowed is True and res_id is not None

    # Artificially set expires_at in the past (simulate 70s old request)
    past_iso = "2020-01-01T00:00:00+00:00"
    conn = db()
    conn.execute("UPDATE api_reservations SET expires_at = ? WHERE reservation_id = ?", (past_iso, res_id))
    conn.commit()
    conn.close()

    # Extend reservation heartbeat
    extended = guardian.extend_reservation(res_id, additional_seconds=120)
    assert extended is True

    # Reconcile stale reservations should NOT reap the extended reservation
    reconciled = guardian.reconcile_stale_reservations()
    assert reconciled == 0

    # Reservation state should remain 'reserved'
    conn = db()
    row = conn.execute("SELECT state FROM api_reservations WHERE reservation_id = ?", (res_id,)).fetchone()
    conn.close()
    assert row["state"] == STATE_RESERVED


def test_abandoned_reservation_conservative_reconciliation():
    """
    Crashed worker or unheartbeated request older than TTL must be reconciled
    conservatively to 'unknown' with cost charged to prevent credit leaks.
    """
    _clean_tables()

    allowed, reason, stats, res_id = guardian.reserve_budget(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_NORMAL
    )
    assert allowed is True

    # Artificially expire the reservation
    past_iso = "2020-01-01T00:00:00+00:00"
    conn = db()
    conn.execute("UPDATE api_reservations SET expires_at = ? WHERE reservation_id = ?", (past_iso, res_id))
    conn.commit()
    conn.close()

    # Run reconciliation
    reconciled_count = guardian.reconcile_stale_reservations()
    assert reconciled_count == 1

    # State must be 'unknown' and cost of 1 logged to api_quota_logs
    conn = db()
    res_row = conn.execute("SELECT state FROM api_reservations WHERE reservation_id = ?", (res_id,)).fetchone()
    assert res_row["state"] == STATE_UNKNOWN

    log_row = conn.execute("SELECT cost, status_code, error_category FROM api_quota_logs WHERE provider_id = ?", (PROVIDER_FLIGHTAPI,)).fetchone()
    assert log_row["cost"] == 1
    assert log_row["status_code"] == 504
    conn.close()


def test_duplicate_finalization_is_idempotent():
    """
    Calling finalize_reservation multiple times must never insert duplicate quota logs
    or double-bill credits.
    """
    _clean_tables()

    allowed, _, _, res_id = guardian.reserve_budget(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_NORMAL
    )
    assert allowed is True

    # Finalize 3 times
    guardian.finalize_reservation(res_id, STATE_SUCCEEDED, status_code=200)
    guardian.finalize_reservation(res_id, STATE_SUCCEEDED, status_code=200)
    guardian.finalize_reservation(res_id, STATE_SUCCEEDED, status_code=200)

    conn = db()
    cur = conn.execute("SELECT COUNT(*) as cnt, SUM(cost) as total_cost FROM api_quota_logs WHERE provider_id = ?", (PROVIDER_FLIGHTAPI,))
    row = cur.fetchone()
    conn.close()

    assert row["cnt"] == 1
    assert row["total_cost"] == 1


def test_late_completion_after_stale_reconciliation_avoids_double_billing():
    """
    If a slow upstream request was reconciled as 'unknown' due to timeout,
    and the 200 OK response eventually arrives late, finalizing must update
    the reservation state without inserting another charged credit.
    """
    _clean_tables()

    allowed, _, _, res_id = guardian.reserve_budget(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_NORMAL
    )
    assert allowed is True

    # Expire and reconcile as unknown
    conn = db()
    conn.execute("UPDATE api_reservations SET expires_at = '2020-01-01T00:00:00+00:00' WHERE reservation_id = ?", (res_id,))
    conn.commit()
    conn.close()

    guardian.reconcile_stale_reservations()

    # Now late 200 OK arrives
    guardian.finalize_reservation(res_id, STATE_SUCCEEDED, status_code=200)

    conn = db()
    res_row = conn.execute("SELECT state FROM api_reservations WHERE reservation_id = ?", (res_id,)).fetchone()
    assert res_row["state"] == STATE_SUCCEEDED

    # Must still have only 1 cost logged in api_quota_logs (from the reconciliation), not 2!
    cur = conn.execute("SELECT COUNT(*) as cnt, SUM(cost) as total_cost FROM api_quota_logs WHERE provider_id = ?", (PROVIDER_FLIGHTAPI,))
    row = cur.fetchone()
    conn.close()

    assert row["cnt"] == 1
    assert row["total_cost"] == 1


# =====================================================================
# SECTION C: BUDGET INVARIANTS & HARD CAPS
# =====================================================================

def test_ordinary_requests_cannot_exceed_25_credits():
    """
    FlightAPI trial budget: 30 credits total, 5-credit safety reserve.
    Ordinary user searches (PRIORITY_NORMAL) and background checks (PRIORITY_BACKGROUND)
    must be blocked as soon as 25 credits have been consumed.
    """
    _clean_tables()

    now_iso = datetime.now(timezone.utc).isoformat()
    conn = db()
    for _ in range(25):
        conn.execute(
            "INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (PROVIDER_FLIGHTAPI, "onewaytrip", CALLER_USER_SEARCH, 0, 200, 1, now_iso)
        )
    conn.commit()
    conn.close()

    # 26th ordinary request MUST be blocked!
    allowed, reason, stats, res_id = guardian.reserve_budget(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_NORMAL
    )
    assert allowed is False
    assert "total trial credit budget exhausted" in reason
    assert res_id is None


def test_critical_requests_can_use_reserve_up_to_30_credits(monkeypatch):
    """
    Critical requests (PRIORITY_CRITICAL) can access the 5-credit emergency reserve
    (25-30), but ALL priorities combined cannot exceed the 30-credit lifetime ceiling.
    """
    _clean_tables()
    monkeypatch.setenv("FLIGHTAPI_MINUTE_BUDGET", "50")
    monkeypatch.setenv("FLIGHTAPI_DAILY_BUDGET", "50")

    now_iso = datetime.now(timezone.utc).isoformat()
    conn = db()
    for _ in range(25):
        conn.execute(
            "INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (PROVIDER_FLIGHTAPI, "onewaytrip", CALLER_USER_SEARCH, 0, 200, 1, now_iso)
        )
    conn.commit()
    conn.close()

    # 1. Critical request is allowed in the reserve zone (26th credit)
    crit_allowed, reason, stats, crit_res = guardian.reserve_budget(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_CRITICAL
    )
    assert crit_allowed is True
    assert crit_res is not None

    guardian.finalize_reservation(crit_res, STATE_SUCCEEDED, 200)

    # Pre-seed up to 30 credits total
    conn = db()
    for _ in range(4):  # 26 + 4 = 30 credits used
        conn.execute(
            "INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (PROVIDER_FLIGHTAPI, "onewaytrip", CALLER_USER_SEARCH, 0, 200, 1, now_iso)
        )
    conn.commit()
    conn.close()

    # At 30 credits, EVEN CRITICAL requests must be rejected!
    hard_allowed, hard_reason, _, _ = guardian.reserve_budget(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_CRITICAL
    )
    assert hard_allowed is False
    assert "hard lifetime credit budget exhausted" in hard_reason


def test_global_minute_and_daily_limits_across_workers():
    """
    Verify that minute burst limits and daily limits operate authoritatively
    across all independent workers via persistent database state.
    """
    _clean_tables()

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    # 1. Test Minute Rate Limit (FlightAPI default: 5 req/min)
    conn = db()
    for _ in range(5):
        conn.execute(
            "INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (PROVIDER_FLIGHTAPI, "onewaytrip", CALLER_USER_SEARCH, 0, 200, 1, now_iso)
        )
    conn.commit()
    conn.close()

    min_allowed, min_reason, _, _ = guardian.reserve_budget(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_NORMAL
    )
    assert min_allowed is False
    assert "exceeded rate limit" in min_reason

    # 2. Test Daily Limit (FlightAPI default: 10 req/day)
    _clean_tables()
    earlier_today_iso = datetime.fromtimestamp(now_utc.timestamp() - 600, tz=timezone.utc).isoformat()
    conn = db()
    for _ in range(10):
        conn.execute(
            "INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (PROVIDER_FLIGHTAPI, "onewaytrip", CALLER_USER_SEARCH, 0, 200, 1, earlier_today_iso)
        )
    conn.commit()
    conn.close()

    day_allowed, day_reason, _, _ = guardian.reserve_budget(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_NORMAL
    )
    assert day_allowed is False
    assert "daily budget exhausted" in day_reason


def test_unknown_endpoint_cost_is_strictly_blocked():
    """
    Endpoints without a verified cost in ENDPOINT_COSTS or environment variable
    must be strictly blocked to prevent unmetered credit exhaustion.
    """
    _clean_tables()

    allowed, reason, stats, res_id = guardian.reserve_budget(
        PROVIDER_FLIGHTAPI,
        "unknown_unmapped_endpoint_12345",
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_CRITICAL
    )
    assert allowed is False
    assert "Unknown endpoint cost" in reason
    assert res_id is None


def test_cached_and_coalesced_requests_create_zero_reservations():
    """
    Cached responses and deduplicated in-flight requests must return immediately
    without creating new upstream reservations or deducting credits.
    """
    _clean_tables()

    mock_data = {"flights": [{"price": 199, "airline": "IndiGo"}]}
    cache_key = guardian.build_cache_key(PROVIDER_FLIGHTAPI, "onewaytrip", {"from": "DEL", "to": "BOM"})
    guardian.set_cached(cache_key, PROVIDER_FLIGHTAPI, "onewaytrip", mock_data, ttl_seconds=300)

    call_count = 0
    def dummy_fetch(params):
        nonlocal call_count
        call_count += 1
        return mock_data

    # Execute request
    resp = guardian.execute_request(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        {"from": "DEL", "to": "BOM"},
        dummy_fetch,
        caller_type=CALLER_USER_SEARCH
    )

    assert resp.is_cached is True
    assert call_count == 0  # Dummy fetch never called

    # No reservations created in database
    conn = db()
    res_count = conn.execute("SELECT COUNT(*) as cnt FROM api_reservations").fetchone()["cnt"]
    conn.close()
    assert res_count == 0


# =====================================================================
# SECTION D: BILLING AND PROVIDER RESPONSES
# =====================================================================

def test_conservative_billing_on_5xx_and_connection_errors():
    """
    5xx server errors, connection resets, and timeouts must be marked STATE_UNKNOWN
    and cost must be charged conservatively (cannot assume rejected response was free).
    """
    _clean_tables()

    def server_error_fetch(params):
        err = Exception("500 Internal Server Error: upstream crashed")
        setattr(err, "status_code", 500)
        raise err

    resp = guardian.execute_request(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        {"from": "DEL", "to": "MAA", "req_id": "err500"},
        server_error_fetch,
        caller_type=CALLER_USER_SEARCH
    )
    assert resp.status == "error"

    conn = db()
    row = conn.execute("SELECT state FROM api_reservations WHERE provider_id = ?", (PROVIDER_FLIGHTAPI,)).fetchone()
    assert row["state"] == STATE_UNKNOWN

    log = conn.execute("SELECT cost, status_code FROM api_quota_logs WHERE provider_id = ?", (PROVIDER_FLIGHTAPI,)).fetchone()
    assert log["cost"] == 1
    assert log["status_code"] == 500
    conn.close()


def test_deterministic_client_errors_are_not_billed():
    """
    400 Bad Request, 401 Unauthorized, and 429 Too Many Requests are client errors
    rejected before upstream query execution; cost must be 0 and budget released.
    """
    _clean_tables()

    def client_error_fetch(params):
        err = Exception("400 Bad Request: missing departure date")
        setattr(err, "status_code", 400)
        raise err

    resp = guardian.execute_request(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        {"from": "DEL", "to": "CCU", "req_id": "err400"},
        client_error_fetch,
        caller_type=CALLER_USER_SEARCH
    )
    assert resp.status == "error"

    conn = db()
    row = conn.execute("SELECT state FROM api_reservations WHERE provider_id = ?", (PROVIDER_FLIGHTAPI,)).fetchone()
    assert row["state"] == STATE_FAILED

    log = conn.execute("SELECT cost, status_code FROM api_quota_logs WHERE provider_id = ?", (PROVIDER_FLIGHTAPI,)).fetchone()
    assert log["cost"] == 0
    assert log["status_code"] == 400
    conn.close()


# =====================================================================
# SECTION E: SECURITY REVIEW & ADMIN ACCESS CONTROLS
# =====================================================================

def test_admin_token_in_url_query_param_strictly_rejected(client, monkeypatch):
    """
    Passing admin tokens via ?token=... in URLs must be strictly rejected with 401
    to protect against URL leakage in access logs, proxies, and browser histories.
    """
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "secure_secret_token_12345")

    # 1. URL query param token attempt must be rejected with 401
    resp_query = client.get("/api/diagnostics/quota?token=secure_secret_token_12345")
    assert resp_query.status_code == 401
    assert "URL query strings are rejected" in resp_query.get_json().get("error", "")

    # 2. Authorization header must succeed
    resp_header = client.get(
        "/api/diagnostics/quota",
        headers={"Authorization": "Bearer secure_secret_token_12345"}
    )
    assert resp_header.status_code == 200
    data = resp_header.get_json()
    assert data["status"] == "operational"


def test_quota_reset_never_erases_history_or_replenishes_lifetime_credits(client, monkeypatch):
    """
    Resetting the quota endpoint must close the circuit breaker, but NEVER
    erase historical api_quota_logs or replenish consumed FlightAPI lifetime credits.
    """
    _clean_tables()
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "admin_reset_token_99")

    # Consume all 30 FlightAPI lifetime credits
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = db()
    for _ in range(30):
        conn.execute(
            "INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (PROVIDER_FLIGHTAPI, "onewaytrip", CALLER_USER_SEARCH, 0, 200, 1, now_iso)
        )
    conn.commit()
    conn.close()

    # Reset quota/circuit state via admin API
    resp = client.post(
        "/api/diagnostics/quota/reset",
        headers={"Authorization": "Bearer admin_reset_token_99"},
        json={"provider_id": PROVIDER_FLIGHTAPI, "reason": "Monthly cycle review"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["circuit_state"] == "CLOSED"

    # Verify historical records still exist (NOT erased)
    conn = db()
    count = conn.execute("SELECT COUNT(*) as cnt FROM api_quota_logs WHERE provider_id = ?", (PROVIDER_FLIGHTAPI,)).fetchone()["cnt"]
    conn.close()
    assert count == 30

    # Quota check must STILL block requests because 30/30 credits are used!
    allowed, reason, stats, _ = guardian.reserve_budget(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_NORMAL
    )
    assert allowed is False
    assert "hard lifetime credit budget exhausted" in reason


def test_secret_scrubbing_in_alerts_and_audits():
    """
    Sensitive keys and raw URLs with path keys must be sanitized before being written
    to api_alerts_log or api_operator_audit.
    """
    _clean_tables()

    dirty_url = "https://api.flightapi.io/onewaytrip/super_secret_raw_key_12345/DEL/BOM/2026-12-01/1/0/0/Economy/INR"
    sanitized = sanitize_flightapi_url(dirty_url)
    assert "super_secret_raw_key_12345" not in sanitized
    assert "/onewaytrip/[REDACTED]/" in sanitized

    # Trigger alert with raw key
    guardian._record_alert(
        alert_type="test_leak_check",
        provider_id="flightapi",
        severity="warning",
        message=f"Request to {dirty_url} timed out",
        details={"url": dirty_url}
    )

    conn = db()
    row = conn.execute("SELECT message, details_json FROM api_alerts_log WHERE alert_type = 'test_leak_check'").fetchone()
    conn.close()

    assert "super_secret_raw_key_12345" not in row["message"]
    assert "super_secret_raw_key_12345" not in row["details_json"]
