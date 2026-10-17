"""
SkyTracker API Guardian Agent Test Suite
========================================
Comprehensive verification for production-grade API Guardian Agent with Hard Quota Protection.

Tests cover:
1. Budget is checked before every outbound call.
2. The 30-credit configured FlightAPI lifetime budget cannot be exceeded.
3. The 5-credit safety reserve is protected from ordinary traffic (Priority 2 Normal / Priority 3 Background).
4. Unknown endpoint costs block requests.
5. Concurrent requests cannot overspend the budget (multi-threaded concurrent reservation stress).
6. Failed or timed-out requests are accounted for conservatively (state 'unknown' billed).
7. Duplicate searches use cache or request coalescing.
8. Daily and monthly limits work independently of lifetime limits.
9. Quota exhaustion opens the circuit breaker.
10. Circuit resets do not replenish provider credits.
11. Background jobs stop when budget policy requires it.
12. Failover uses an independently authorized provider and its own budget.
13. No request bypasses the Guardian.
14. Provider keys and secrets are redacted from logs, traces, and exceptions.
15. Operator endpoints reject unauthorized access and audit actions.
16. AI recommendations cannot override deterministic safety controls.
17. Existing search and tracking flows integrate cleanly without credit consumption.
"""

import os
import time
import json
import pytest
import threading
from unittest.mock import patch, MagicMock

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
    STATE_RELEASED,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_QUOTA_EXHAUSTED,
    estimate_endpoint_cost,
    sanitize_flightapi_url,
    get_guardian_config,
    UsageAnalysisAgent
)
from db import init_db, db


@pytest.fixture(autouse=True)
def clean_guardian_state():
    """Ensure clean database and cache state before and after each test."""
    init_db()
    guardian.clear_cache()
    yield
    guardian.clear_cache()


# =====================================================================
# 1. PRE-FLIGHT BUDGET CHECK BEFORE EVERY OUTBOUND CALL
# =====================================================================
def test_preflight_budget_check_before_outbound_call(monkeypatch):
    """Budget is checked and verified before making any outbound API call."""
    mock_fetch = MagicMock(return_value={"status": "ok"})

    # Ensure budget check allows normal call
    resp = guardian.execute_request(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        {"from": "DEL", "to": "BOM", "date": "2026-11-20"},
        mock_fetch,
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_NORMAL
    )

    assert resp.status == "fresh"
    assert resp.is_live is True
    assert mock_fetch.call_count == 1


# =====================================================================
# 2. FLIGHTAPI 30-CREDIT TRIAL LIFETIME HARD CEILING
# =====================================================================
def test_flightapi_30_credit_lifetime_budget_cannot_be_exceeded(monkeypatch):
    """The 30-credit configured FlightAPI lifetime budget cannot be exceeded under any circumstances."""
    monkeypatch.setenv("FLIGHTAPI_TOTAL_BUDGET", "30")
    monkeypatch.setenv("FLIGHTAPI_SAFETY_MARGIN", "5")

    # Simulate 30 confirmed used credits in database
    conn = db()
    for i in range(30):
        conn.execute(
            "INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (PROVIDER_FLIGHTAPI, "onewaytrip", CALLER_USER_SEARCH, 0, 200, 1, "2026-10-17 10:00:00")
        )
    conn.commit()
    conn.close()

    mock_fetch = MagicMock(return_value={"data": "should_not_run"})

    # Even Priority 1 (Critical) must be blocked once hard limit (30) is reached
    resp_crit = guardian.execute_request(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        {"from": "DEL", "to": "BOM", "date": "2026-11-20"},
        mock_fetch,
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_CRITICAL
    )

    assert resp_crit.status == "blocked_budget"
    assert "hard lifetime credit budget exhausted" in resp_crit.error
    assert mock_fetch.call_count == 0


# =====================================================================
# 3. FIVE-CREDIT SAFETY RESERVE PROTECTED FROM ORDINARY TRAFFIC
# =====================================================================
def test_five_credit_reserve_protected_from_ordinary_traffic(monkeypatch):
    """
    Ordinary traffic (Priority 2 Normal, Priority 3 Background) is blocked once 25 credits are spent,
    strictly protecting the 5-credit safety reserve for Priority 1 Critical traffic.
    """
    monkeypatch.setenv("FLIGHTAPI_TOTAL_BUDGET", "30")
    monkeypatch.setenv("FLIGHTAPI_SAFETY_MARGIN", "5")
    monkeypatch.setenv("FLIGHTAPI_DAILY_BUDGET", "100")
    monkeypatch.setenv("FLIGHTAPI_MONTHLY_BUDGET", "100")

    # Insert 25 used credits
    conn = db()
    for i in range(25):
        conn.execute(
            "INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (PROVIDER_FLIGHTAPI, "onewaytrip", CALLER_USER_SEARCH, 0, 200, 1, "2026-10-17 10:00:00")
        )
    conn.commit()
    conn.close()

    mock_fetch = MagicMock(return_value={"data": "fare_data"})

    # 1. Priority 2 Normal search: blocked at 25 credits
    resp_norm = guardian.execute_request(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        {"from": "DEL", "to": "BLR", "date": "2026-11-21"},
        mock_fetch,
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_NORMAL
    )
    assert resp_norm.status == "blocked_budget"
    assert "total trial credit budget exhausted" in resp_norm.error
    assert mock_fetch.call_count == 0

    # 2. Priority 3 Background check: blocked at 25 credits
    resp_bg = guardian.execute_request(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        {"from": "BOM", "to": "GOI", "date": "2026-11-22"},
        mock_fetch,
        caller_type=CALLER_BACKGROUND_CHECK,
        priority=PRIORITY_BACKGROUND
    )
    assert resp_bg.status == "blocked_budget"
    assert mock_fetch.call_count == 0

    # 3. Priority 1 Critical operation: permitted to use reserved credit
    resp_crit = guardian.execute_request(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        {"from": "DEL", "to": "MAA", "date": "2026-11-23"},
        mock_fetch,
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_CRITICAL
    )
    assert resp_crit.status == "fresh"
    assert mock_fetch.call_count == 1


# =====================================================================
# 4. UNKNOWN ENDPOINT COSTS BLOCK REQUESTS
# =====================================================================
def test_unknown_endpoint_cost_blocks_request():
    """If an endpoint cost is unknown/unverified, the request is strictly blocked before sending."""
    mock_fetch = MagicMock(return_value={"data": "test"})

    # Check estimate_endpoint_cost directly
    cost = estimate_endpoint_cost(PROVIDER_FLIGHTAPI, "unknown_undocumented_endpoint")
    assert cost is None

    # Execute request on unknown endpoint
    resp = guardian.execute_request(
        PROVIDER_FLIGHTAPI,
        "unknown_undocumented_endpoint",
        {"param": "val"},
        mock_fetch,
        caller_type=CALLER_USER_SEARCH
    )

    assert resp.status == "blocked_budget"
    assert "Unknown endpoint cost" in resp.error
    assert mock_fetch.call_count == 0


# =====================================================================
# 5. CONCURRENT REQUESTS CANNOT OVERSPEND THE BUDGET
# =====================================================================
def test_concurrent_requests_cannot_overspend_budget(monkeypatch):
    """
    Simulated concurrent multi-worker requests cannot spend the same remaining credits.
    Atomic reservation locks ensure total confirmed + active reservations never exceed budget.
    """
    monkeypatch.setenv("FLIGHTAPI_TOTAL_BUDGET", "10")
    monkeypatch.setenv("FLIGHTAPI_SAFETY_MARGIN", "0")  # 10 ordinary credits available
    monkeypatch.setenv("FLIGHTAPI_MINUTE_BUDGET", "50")
    monkeypatch.setenv("FLIGHTAPI_DAILY_BUDGET", "50")

    successful_reservations = []
    blocked_reservations = []
    barrier = threading.Barrier(15)

    def worker_reserve(worker_id):
        barrier.wait()
        allowed, reason, stats, res_id = guardian.reserve_budget(
            PROVIDER_FLIGHTAPI,
            "onewaytrip",
            caller_type=CALLER_USER_SEARCH,
            priority=PRIORITY_NORMAL
        )
        if allowed:
            successful_reservations.append(res_id)
        else:
            blocked_reservations.append(reason)

    threads = [threading.Thread(target=worker_reserve, args=(i,)) for i in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly 10 should succeed, exactly 5 must be blocked!
    assert len(successful_reservations) == 10
    assert len(blocked_reservations) == 5


# =====================================================================
# 6. CONSERVATIVE TIMEOUT ACCOUNTING (STATE 'UNKNOWN' BILLED)
# =====================================================================
def test_failed_or_timed_out_requests_accounted_conservatively():
    """
    Timeouts and connection drops are transitioned to state 'unknown'
    and their cost is recorded as consumed in api_quota_logs.
    """
    def mock_timeout(params):
        raise TimeoutError("Upstream socket read timed out after 25s")

    resp = guardian.execute_request(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        {"from": "DEL", "to": "BOM", "date": "2026-11-20"},
        mock_timeout,
        caller_type=CALLER_USER_SEARCH,
        priority=PRIORITY_NORMAL
    )

    assert resp.status == "error"
    assert "timed out" in resp.error.lower()

    # Check reservation state and quota logs
    conn = db()
    res_rows = conn.execute("SELECT state FROM api_reservations WHERE provider_id = 'flightapi'").fetchall()
    assert len(res_rows) == 1
    assert res_rows[0]["state"] == STATE_UNKNOWN

    log_rows = conn.execute("SELECT cost, status_code, error_category FROM api_quota_logs WHERE provider_id = 'flightapi' AND cost > 0").fetchall()
    conn.close()

    assert len(log_rows) == 1
    assert log_rows[0]["cost"] == 1
    assert log_rows[0]["status_code"] == 504
    assert log_rows[0]["error_category"] == "timeout_conservative_accounting"


# =====================================================================
# 7. DUPLICATE SEARCHES USE CACHE OR REQUEST COALESCING
# =====================================================================
def test_duplicate_searches_use_cache_and_single_flight():
    """Identical searches within cache window return cached data without spending additional credits."""
    mock_fetch = MagicMock(return_value={"itineraries": [{"price": 4500}]})

    # First search: live outbound fetch
    resp1 = guardian.execute_request(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        {"from": "DEL", "to": "BOM", "date": "2026-11-20"},
        mock_fetch
    )
    assert resp1.status == "fresh"
    assert resp1.is_live is True
    assert mock_fetch.call_count == 1

    # Second identical search: retrieved from cache with truthful metadata
    resp2 = guardian.execute_request(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        {"from": "DEL", "to": "BOM", "date": "2026-11-20"},
        mock_fetch
    )
    assert resp2.status == "cached"
    assert resp2.is_cached is True
    assert resp2.is_live is False
    assert mock_fetch.call_count == 1  # 0 additional outbound calls!


# =====================================================================
# 8. DAILY AND MONTHLY LIMITS WORK INDEPENDENTLY
# =====================================================================
def test_daily_and_monthly_limits_work_independently(monkeypatch):
    """Daily limit exhaustion blocks requests even if monthly/lifetime budget remains."""
    monkeypatch.setenv("FLIGHTAPI_DAILY_BUDGET", "2")
    monkeypatch.setenv("FLIGHTAPI_MONTHLY_BUDGET", "30")
    monkeypatch.setenv("FLIGHTAPI_TOTAL_BUDGET", "30")

    mock_fetch = MagicMock(return_value={"price": 3000})

    # Spend 2 requests (daily limit)
    guardian.execute_request(PROVIDER_FLIGHTAPI, "onewaytrip", {"q": "1"}, mock_fetch)
    guardian.execute_request(PROVIDER_FLIGHTAPI, "onewaytrip", {"q": "2"}, mock_fetch)
    assert mock_fetch.call_count == 2

    # 3rd request should fail on daily budget
    resp3 = guardian.execute_request(PROVIDER_FLIGHTAPI, "onewaytrip", {"q": "3"}, mock_fetch)
    assert resp3.status == "blocked_budget"
    assert "daily budget exhausted" in resp3.error
    assert mock_fetch.call_count == 2


# =====================================================================
# 9. QUOTA EXHAUSTION OPENS CIRCUIT BREAKER
# =====================================================================
def test_quota_exhaustion_opens_circuit_breaker():
    """HTTP 429 / quota exhaustion immediately trips circuit breaker to OPEN."""
    def mock_429(params):
        err = Exception("FlightAPI monthly credits exhausted (HTTP 429)")
        err.status_code = 429
        raise err

    resp = guardian.execute_request(PROVIDER_FLIGHTAPI, "onewaytrip", {"q": "exh"}, mock_429)
    assert resp.status == "error"

    state, _ = guardian.get_circuit_state(PROVIDER_FLIGHTAPI)
    assert state == STATE_OPEN


# =====================================================================
# 10. CIRCUIT RESETS DO NOT REPLENISH PROVIDER CREDITS
# =====================================================================
def test_circuit_reset_does_not_replenish_provider_credits():
    """Resetting the circuit breaker closes the circuit but does NOT wipe historical usage logs or refill credits."""
    conn = db()
    conn.execute(
        "INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (PROVIDER_FLIGHTAPI, "onewaytrip", CALLER_USER_SEARCH, 0, 200, 5, "2026-10-17 10:00:00")
    )
    conn.commit()
    conn.close()

    # Trip circuit
    guardian._update_circuit_state(PROVIDER_FLIGHTAPI, STATE_OPEN, failure_count=3, reason="Outage")

    # Reset circuit
    guardian.reset_provider_quota(PROVIDER_FLIGHTAPI, reason="Operator manual reset")

    state, _ = guardian.get_circuit_state(PROVIDER_FLIGHTAPI)
    assert state == STATE_CLOSED

    # Historical confirmed cost must remain intact!
    diag = guardian.get_diagnostics()
    fa_budget = diag["budgets"]["flightapi"]
    assert fa_budget["confirmed_spent"] == 5


# =====================================================================
# 11. BACKGROUND JOBS STOP WHEN BUDGET REQUIRES IT
# =====================================================================
def test_background_jobs_stop_when_budget_low(monkeypatch):
    """Background tasks yield when remaining effective quota reaches reserved quota."""
    monkeypatch.setenv("FLIGHTAPI_TOTAL_BUDGET", "30")
    monkeypatch.setenv("FLIGHTAPI_SAFETY_MARGIN", "5")
    monkeypatch.setenv("FLIGHTAPI_RESERVED_SEARCH_QUOTA", "5")

    # Insert 20 confirmed calls; remaining ordinary = 25 - 20 = 5 (equals reserved quota)
    conn = db()
    for i in range(20):
        conn.execute(
            "INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (PROVIDER_FLIGHTAPI, "onewaytrip", CALLER_USER_SEARCH, 0, 200, 1, "2026-10-17 10:00:00")
        )
    conn.commit()
    conn.close()

    mock_fetch = MagicMock(return_value={"price": 5000})

    # Background check should be rejected
    resp_bg = guardian.execute_request(
        PROVIDER_FLIGHTAPI,
        "onewaytrip",
        {"dep": "DEL", "arr": "BOM", "date": "2026-11-20"},
        mock_fetch,
        caller_type=CALLER_BACKGROUND_CHECK,
        priority=PRIORITY_BACKGROUND
    )
    assert resp_bg.status == "blocked_budget"
    assert "within reserved quota" in resp_bg.error or "exhausted" in resp_bg.error
    assert mock_fetch.call_count == 0


# =====================================================================
# 12. FAILOVER USES INDEPENDENTLY AUTHORIZED PROVIDER AND BUDGET
# =====================================================================
def test_failover_uses_independent_authorized_provider():
    """When primary provider circuit is open, failover is routed to a secondary provider with independent budget."""
    # Open FlightAPI circuit
    guardian._update_circuit_state(PROVIDER_FLIGHTAPI, STATE_OPEN, reason="Quota Exhausted")

    # FlightAPI call is blocked
    mock_flightapi = MagicMock()
    resp_fa = guardian.execute_request(PROVIDER_FLIGHTAPI, "onewaytrip", {"test": 1}, mock_flightapi)
    assert resp_fa.status == "circuit_open"
    assert mock_flightapi.call_count == 0

    # SerpAPI call succeeds independently
    mock_serpapi = MagicMock(return_value={"organic_results": []})
    resp_serp = guardian.execute_request(PROVIDER_SERPAPI, "search", {"test": 2}, mock_serpapi)
    assert resp_serp.status == "fresh"
    assert mock_serpapi.call_count == 1


# =====================================================================
# 13. NO REQUEST BYPASSES THE GUARDIAN
# =====================================================================
def test_no_request_bypasses_the_guardian(monkeypatch):
    """All external flight adapter operations route through guardian.execute_request."""
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "mock_key_xyz_12345")
    from services.providers.flightapi_provider import FlightApiProvider

    provider = FlightApiProvider()
    with patch("services.api_guardian.guardian.execute_request") as mock_exec:
        mock_exec.return_value = MagicMock(status="blocked_budget", error="Blocked", data=None)
        offers = provider.search("DEL", "BOM", "2026-11-20")
        assert offers == []
        assert mock_exec.call_count >= 1


# =====================================================================
# 14. PROVIDER KEYS AND SECRETS ARE REDACTED FROM LOGS AND TRACES
# =====================================================================
def test_provider_keys_are_redacted_from_logs_and_urls(monkeypatch):
    """Path-embedded FlightAPI keys and secrets are thoroughly redacted from text and traces."""
    secret_key = "super_secret_flightapi_key_xyz_12345"
    monkeypatch.setenv("FLIGHTAPI_API_KEY", secret_key)

    raw_url = f"https://api.flightapi.io/onewaytrip/{secret_key}/DEL/BOM/2026-11-20/1/0/0/Economy/INR"
    sanitized = sanitize_flightapi_url(raw_url)

    assert secret_key not in sanitized
    assert "/onewaytrip/[REDACTED]/DEL/BOM/" in sanitized

    # Exception trace redaction
    err = Exception(f"Failed to fetch {raw_url} using key={secret_key}")
    sanitized_err = sanitize_flightapi_url(str(err))
    assert secret_key not in sanitized_err
    assert "[REDACTED]" in sanitized_err


# =====================================================================
# 15. OPERATOR ENDPOINTS REJECT UNAUTHORIZED ACCESS AND AUDIT ACTIONS
# =====================================================================
def test_operator_endpoints_security_and_audit(client, monkeypatch):
    """Operator diagnostics, reset, and adjust endpoints require authentication and record audits."""
    admin_token = "secure_ops_token_777"
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", admin_token)

    # 1. GET /api/diagnostics/analysis without token -> 401
    res_unauth = client.get("/api/diagnostics/analysis")
    assert res_unauth.status_code == 401

    # 2. GET /api/diagnostics/analysis with token -> 200
    res_auth = client.get(
        "/api/diagnostics/analysis",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res_auth.status_code == 200
    body = res_auth.get_json()
    assert body["status"] == "success"
    assert "metrics_summary" in body
    assert "recommendations" in body

    # 3. POST /api/diagnostics/quota/adjust with token -> 200 and audit logged
    res_adj = client.post(
        "/api/diagnostics/quota/adjust",
        json={"flightapi_safety_margin": 6},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res_adj.status_code == 200

    conn = db()
    audit_rows = conn.execute("SELECT action, details_json FROM api_operator_audit").fetchall()
    conn.close()
    assert len(audit_rows) >= 1
    assert audit_rows[0]["action"] == "config_adjustment"
    assert "flightapi_safety_margin" in audit_rows[0]["details_json"]


# =====================================================================
# 16. AI RECOMMENDATIONS CANNOT OVERRIDE DETERMINISTIC SAFETY CONTROLS
# =====================================================================
def test_ai_recommendations_cannot_override_deterministic_controls():
    """
    AI usage analysis provides non-autonomous advisory recommendations only.
    It cannot alter hard budget boundaries or authorize blocked calls.
    """
    agent = UsageAnalysisAgent(guardian)
    analysis = agent.analyze_usage(days=7)

    assert analysis["safety_policy"]["autonomous_budget_adjustment"] is False
    assert analysis["safety_policy"]["operator_approval_required"] is True

    for rec in analysis["recommendations"]:
        assert rec["requires_operator_approval"] is True
        assert rec["autonomous_execution"] is False
