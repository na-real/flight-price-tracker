"""
Comprehensive deterministic test suite for SkyTracker API Quota Guardian.
Verifies all 17 requirements specified in the API Quota Guardian Specification.
"""

import time
import threading
from datetime import datetime, timezone
import pytest
import requests

from services.api_guardian import (
    guardian,
    ApiGuardian,
    PROVIDER_SERPAPI,
    PROVIDER_AMADEUS,
    CALLER_USER_SEARCH,
    CALLER_BACKGROUND_CHECK,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_HALF_OPEN,
)
from services.providers.base import FlightOffer
from services.providers.serpapi_provider import SerpApiFlightsProvider
from db import db, DATABASE_URL


# =====================================================================
# 1. CACHE HITS AVOID UPSTREAM CALLS
# =====================================================================
def test_cache_hit_avoids_upstream_calls():
    calls = []

    def mock_fetch(p):
        calls.append(p)
        return {"flights": [{"airline": "IndiGo", "price": 5000}]}

    params = {"departure_id": "DEL", "arrival_id": "BOM", "outbound_date": "2026-11-20"}

    # 1st call: Cache miss -> calls upstream fetch
    resp1 = guardian.execute_request(
        provider_id=PROVIDER_SERPAPI,
        endpoint="search",
        params=params,
        fetch_fn=mock_fetch
    )
    assert resp1.status == "fresh"
    assert resp1.is_cached is False
    assert len(calls) == 1

    # 2nd call: Cache hit -> does NOT call upstream fetch
    resp2 = guardian.execute_request(
        provider_id=PROVIDER_SERPAPI,
        endpoint="search",
        params=params,
        fetch_fn=mock_fetch
    )
    assert resp2.status == "cached"
    assert resp2.is_cached is True
    assert len(calls) == 1  # Still 1, upstream was not called!


# =====================================================================
# 2. EXPIRED CACHE ENTRIES TRIGGER A FRESH REQUEST
# =====================================================================
def test_expired_cache_entry_triggers_fresh_request():
    calls = []

    def mock_fetch(p):
        calls.append(p)
        return {"timestamp": time.time(), "price": 4500}

    params = {"departure_id": "DEL", "arrival_id": "BOM", "outbound_date": "2026-11-20"}

    # 1st call: TTL of 1 second
    resp1 = guardian.execute_request(
        provider_id=PROVIDER_SERPAPI,
        endpoint="search",
        params=params,
        fetch_fn=mock_fetch,
        cache_ttl=1
    )
    assert resp1.status == "fresh"
    assert len(calls) == 1

    # Wait for entry to expire
    time.sleep(1.1)

    # 2nd call: Expired entry -> triggers fresh upstream fetch
    resp2 = guardian.execute_request(
        provider_id=PROVIDER_SERPAPI,
        endpoint="search",
        params=params,
        fetch_fn=mock_fetch,
        cache_ttl=1
    )
    assert resp2.status == "fresh"
    assert resp2.is_cached is False
    assert len(calls) == 2


# =====================================================================
# 3. DIFFERENT ROUTES AND DATES DO NOT SHARE CACHED RESULTS
# =====================================================================
def test_different_routes_and_dates_do_not_share_cached_results():
    calls = []

    def mock_fetch(p):
        calls.append(p)
        return {"route": f"{p['departure_id']}->{p['arrival_id']}", "date": p["outbound_date"]}

    p_del_bom = {"departure_id": "DEL", "arrival_id": "BOM", "outbound_date": "2026-11-20"}
    p_del_blr = {"departure_id": "DEL", "arrival_id": "BLR", "outbound_date": "2026-11-20"}
    p_del_bom_d2 = {"departure_id": "DEL", "arrival_id": "BOM", "outbound_date": "2026-11-21"}

    resp1 = guardian.execute_request(PROVIDER_SERPAPI, "search", p_del_bom, mock_fetch)
    resp2 = guardian.execute_request(PROVIDER_SERPAPI, "search", p_del_blr, mock_fetch)
    resp3 = guardian.execute_request(PROVIDER_SERPAPI, "search", p_del_bom_d2, mock_fetch)

    assert len(calls) == 3
    assert resp1.data["route"] == "DEL->BOM"
    assert resp2.data["route"] == "DEL->BLR"
    assert resp3.data["date"] == "2026-11-21"


# =====================================================================
# 4. EQUIVALENT REQUESTS NORMALIZE TO THE SAME CACHE KEY
# =====================================================================
def test_equivalent_requests_normalize_to_same_cache_key():
    p1 = {
        "departure_id": "del",
        "arrival_id": "bom",
        "outbound_date": "2026-11-20",
        "api_key": "secret_key_1",
        "hl": "en",
        "gl": "in"
    }
    p2 = {
        "arrival_id": "BOM",
        "departure_id": "DEL",
        "outbound_date": "2026-11-20",
        "api_key": "secret_key_different",
        "gl": "in",
        "hl": "fr"
    }

    key1 = guardian.build_cache_key(PROVIDER_SERPAPI, "search", p1)
    key2 = guardian.build_cache_key(PROVIDER_SERPAPI, "search", p2)

    assert key1 == key2, "Equivalent search parameters with case or transient differences must normalize to identical cache key"


# =====================================================================
# 5. CONCURRENT IDENTICAL REQUESTS ARE DEDUPLICATED (SINGLE-FLIGHT)
# =====================================================================
def test_concurrent_identical_requests_are_deduplicated():
    calls = []
    barrier = threading.Barrier(5)

    def slow_fetch(p):
        calls.append(p)
        time.sleep(0.3)
        return {"data": "shared_result", "count": len(calls)}

    params = {"departure_id": "DEL", "arrival_id": "BOM", "outbound_date": "2026-12-01"}
    results = [None] * 5

    def worker(idx):
        barrier.wait()
        results[idx] = guardian.execute_request(
            provider_id=PROVIDER_SERPAPI,
            endpoint="search",
            params=params,
            fetch_fn=slow_fetch
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Upstream was called exactly once, despite 5 simultaneous incoming searches!
    assert len(calls) == 1
    for r in results:
        assert r is not None
        assert r.data["data"] == "shared_result"


# =====================================================================
# 6. DAILY AND MONTHLY BUDGETS BLOCK CALLS AT CONFIGURED LIMITS
# =====================================================================
def test_daily_and_monthly_budgets_block_calls(monkeypatch):
    monkeypatch.setenv("SERPAPI_DAILY_BUDGET", "3")
    monkeypatch.setenv("SERPAPI_MINUTE_BUDGET", "50")
    monkeypatch.setenv("SERPAPI_SAFETY_MARGIN", "0")
    monkeypatch.setenv("SERPAPI_RESERVED_SEARCH_QUOTA", "0")

    calls = []

    def mock_fetch(p):
        calls.append(p)
        return {"ok": True}

    for i in range(3):
        resp = guardian.execute_request(
            provider_id=PROVIDER_SERPAPI,
            endpoint="search",
            params={"dep": "DEL", "arr": "BOM", "outbound": f"2026-11-0{i+1}"},
            fetch_fn=mock_fetch
        )
        assert resp.status == "fresh"

    assert len(calls) == 3

    # 4th call: Daily budget is 3 -> blocked before making upstream call
    resp_blocked = guardian.execute_request(
        provider_id=PROVIDER_SERPAPI,
        endpoint="search",
        params={"dep": "DEL", "arr": "BOM", "outbound": "2026-11-04"},
        fetch_fn=mock_fetch
    )
    assert resp_blocked.status == "blocked_budget"
    assert "budget" in resp_blocked.error.lower()
    assert len(calls) == 3  # Upstream fetch was NOT called!


# =====================================================================
# 7. CONCURRENT WORKERS CANNOT EXCEED SHARED BUDGETS
# =====================================================================
def test_concurrent_workers_shared_budgets(monkeypatch):
    monkeypatch.setenv("SERPAPI_DAILY_BUDGET", "4")
    monkeypatch.setenv("SERPAPI_MINUTE_BUDGET", "50")
    monkeypatch.setenv("SERPAPI_SAFETY_MARGIN", "0")
    monkeypatch.setenv("SERPAPI_RESERVED_SEARCH_QUOTA", "0")

    worker_a = ApiGuardian()
    worker_b = ApiGuardian()

    calls = []

    def mock_fetch(p):
        calls.append(p)
        return {"worker_call": len(calls)}

    # Worker A makes 2 calls
    worker_a.execute_request(PROVIDER_SERPAPI, "search", {"id": "1"}, mock_fetch)
    worker_a.execute_request(PROVIDER_SERPAPI, "search", {"id": "2"}, mock_fetch)

    # Worker B makes 2 calls
    worker_b.execute_request(PROVIDER_SERPAPI, "search", {"id": "3"}, mock_fetch)
    worker_b.execute_request(PROVIDER_SERPAPI, "search", {"id": "4"}, mock_fetch)

    assert len(calls) == 4

    # Worker A tries 5th call: Shared budget exhausted across processes!
    resp_a = worker_a.execute_request(PROVIDER_SERPAPI, "search", {"id": "5"}, mock_fetch)
    assert resp_a.status == "blocked_budget"

    # Worker B tries 5th call: Also blocked!
    resp_b = worker_b.execute_request(PROVIDER_SERPAPI, "search", {"id": "6"}, mock_fetch)
    assert resp_b.status == "blocked_budget"


# =====================================================================
# 8. RATE LIMITS WORK ACROSS APPLICATION WORKERS
# =====================================================================
def test_rate_limits_work_across_workers(monkeypatch):
    monkeypatch.setenv("SERPAPI_MINUTE_BUDGET", "2")
    monkeypatch.setenv("SERPAPI_SAFETY_MARGIN", "0")
    monkeypatch.setenv("SERPAPI_RESERVED_SEARCH_QUOTA", "0")

    worker_1 = ApiGuardian()
    worker_2 = ApiGuardian()

    calls = []

    def mock_fetch(p):
        calls.append(p)
        return {"ok": True}

    # Worker 1 makes 1st call in the minute
    resp1 = worker_1.execute_request(PROVIDER_SERPAPI, "search", {"req": "w1"}, mock_fetch)
    assert resp1.status == "fresh"

    # Worker 2 makes 2nd call in the minute
    resp2 = worker_2.execute_request(PROVIDER_SERPAPI, "search", {"req": "w2"}, mock_fetch)
    assert resp2.status == "fresh"

    # Worker 1 makes 3rd call in the same minute -> blocked by minute rate limit
    resp3 = worker_1.execute_request(PROVIDER_SERPAPI, "search", {"req": "w3"}, mock_fetch)
    assert resp3.status == "blocked_budget"
    assert "rate limit" in resp3.error.lower()


# =====================================================================
# 9. HTTP 429 STOPS REPEATED CALLS (CIRCUIT BREAKER)
# =====================================================================
def test_http_429_stops_repeated_calls():
    calls = []

    def fail_with_429(p):
        calls.append(p)
        err = Exception("SerpAPI search limit reached (HTTP 429)")
        setattr(err, "status_code", 429)
        raise err

    # 1st call hits 429 and trips circuit breaker to OPEN
    resp1 = guardian.execute_request(PROVIDER_SERPAPI, "search", {"req": "test_429"}, fail_with_429)
    assert resp1.status == "error"
    assert len(calls) == 1

    # Circuit breaker is now OPEN
    state, cooldown = guardian.get_circuit_state(PROVIDER_SERPAPI)
    assert state == STATE_OPEN

    # 2nd call: Immediately rejected by Circuit Breaker with zero upstream calls
    resp2 = guardian.execute_request(PROVIDER_SERPAPI, "search", {"req": "next_search"}, fail_with_429)
    assert resp2.status == "circuit_open"
    assert "circuit breaker is open" in resp2.error.lower()
    assert len(calls) == 1  # No upstream call was made!


# =====================================================================
# 10. HTTP 401/403 DOES NOT TRIGGER RETRY LOOPS
# =====================================================================
def test_http_401_403_does_not_trigger_retry_loops():
    calls = []

    def auth_error(p):
        calls.append(p)
        err = Exception("Invalid API key (HTTP 401)")
        setattr(err, "status_code", 401)
        raise err

    resp = guardian.execute_request(PROVIDER_SERPAPI, "search", {"req": "test_auth"}, auth_error)
    assert resp.status == "error"
    # Must NOT retry: exactly 1 call made!
    assert len(calls) == 1


# =====================================================================
# 11. RETRYABLE ERRORS USE BOUNDED RETRIES
# =====================================================================
def test_retryable_errors_use_bounded_retries(monkeypatch):
    monkeypatch.setenv("API_MAX_RETRIES", "2")
    calls = []

    def transient_failure(p):
        calls.append(p)
        if len(calls) < 3:
            err = Exception("503 Gateway Unavailable")
            setattr(err, "status_code", 503)
            raise err
        return {"recovered": True}

    resp = guardian.execute_request(PROVIDER_SERPAPI, "search", {"req": "retry_test"}, transient_failure)
    assert resp.status == "fresh"
    assert resp.data["recovered"] is True
    # Initial attempt + 2 retries = 3 calls
    assert len(calls) == 3


# =====================================================================
# 12. CIRCUIT BREAKERS OPEN AND RECOVER CORRECTLY
# =====================================================================
def test_circuit_breakers_open_and_recover():
    # 1. Trip circuit breaker with failures
    guardian.record_circuit_failure(PROVIDER_SERPAPI, status_code=429, error_msg="Rate limit")
    state, _ = guardian.get_circuit_state(PROVIDER_SERPAPI)
    assert state == STATE_OPEN

    # 2. Reset / recover on success
    guardian.record_circuit_success(PROVIDER_SERPAPI)
    recovered_state, _ = guardian.get_circuit_state(PROVIDER_SERPAPI)
    assert recovered_state == STATE_CLOSED


# =====================================================================
# 13. CACHED RESULTS ARE LABELED WITH REAL AGE AND SOURCE AND NEVER LABELED LIVE
# =====================================================================
def test_cached_results_labeled_with_real_age_and_source():
    def mock_fetch(p):
        return {"price": 6000}

    params = {"dep": "DEL", "arr": "BOM", "date": "2026-11-20"}
    resp1 = guardian.execute_request(PROVIDER_SERPAPI, "search", params, mock_fetch)
    assert resp1.is_cached is False
    assert resp1.is_live is True
    assert resp1.age_seconds == 0

    time.sleep(0.1)

    resp2 = guardian.execute_request(PROVIDER_SERPAPI, "search", params, mock_fetch)
    assert resp2.is_cached is True
    assert resp2.is_live is False  # Cached results must NEVER be labeled live
    assert resp2.status == "cached"
    assert resp2.cached_at is not None
    assert resp2.age_seconds >= 0


def test_monthly_quota_exhaustion_cannot_repeatedly_retry(monkeypatch):
    monkeypatch.setenv("SERPAPI_RESET_POLICY", "billing_cycle")
    calls = []

    def fail_monthly(p):
        calls.append(p)
        err = Exception("Google Flights: SerpAPI monthly search limit reached")
        setattr(err, "status_code", 429)
        raise err

    # First call encounters monthly search limit
    resp1 = guardian.execute_request(PROVIDER_SERPAPI, "search", {"dep": "DEL", "arr": "BOM"}, fail_monthly)
    assert resp1.status == "error"
    assert len(calls) == 1

    # Check circuit state: cooldown must extend until next month reset
    state, cooldown_until = guardian.get_circuit_state(PROVIDER_SERPAPI)
    assert state == STATE_OPEN
    assert cooldown_until is not None

    cool_dt = datetime.fromisoformat(cooldown_until)
    now_dt = datetime.now(timezone.utc)
    # Must be at least 24 hours in the future (resetting on 1st of next month)
    assert (cool_dt.timestamp() - now_dt.timestamp()) > 86400

    # Budget check must also block immediately
    allowed, reason, _ = guardian.check_budget(PROVIDER_SERPAPI, CALLER_USER_SEARCH)
    assert allowed is False
    assert "monthly" in reason.lower()

    # Subsequent requests must NOT call upstream provider
    for _ in range(5):
        resp = guardian.execute_request(PROVIDER_SERPAPI, "search", {"dep": "DEL", "arr": "BOM"}, fail_monthly)
        assert resp.status in ("circuit_open", "blocked_budget")

    assert len(calls) == 1  # Exactly 1 call, zero repeat probes!


# =====================================================================
# 14. NO FALLBACK FABRICATES LIVE PRICES
# =====================================================================
def test_no_fallback_fabricates_live_prices(monkeypatch):
    provider = SerpApiFlightsProvider()
    monkeypatch.delenv("SERPAPI_KEY", raising=False)

    # Provider is unconfigured
    offers = provider.search("DEL", "BOM", "2026-11-20")
    assert offers == [], "Unconfigured provider must return empty list, not fake offers"

    # Trip circuit breaker
    monkeypatch.setenv("SERPAPI_KEY", "mock_key")
    guardian.record_circuit_failure(provider.provider_id, status_code=429, error_msg="Search limit reached")

    offers_blocked = provider.search("DEL", "BOM", "2026-11-20")
    assert offers_blocked == [], "Circuit open provider must return empty list, not fake offers"
    assert provider.last_status == "rate_limited"


# =====================================================================
# 15. BACKGROUND JOBS RESPECT RESERVED SEARCH QUOTA
# =====================================================================
def test_background_jobs_respect_reserved_search_quota(monkeypatch):
    monkeypatch.setenv("SERPAPI_DAILY_BUDGET", "10")
    monkeypatch.setenv("SERPAPI_RESERVED_SEARCH_QUOTA", "4")
    monkeypatch.setenv("SERPAPI_SAFETY_MARGIN", "0")
    monkeypatch.setenv("SERPAPI_MINUTE_BUDGET", "50")

    def mock_fetch(p):
        return {"ok": True}

    # Consume 6 requests via user searches
    for i in range(6):
        guardian.execute_request(PROVIDER_SERPAPI, "search", {"id": str(i)}, mock_fetch, caller_type=CALLER_USER_SEARCH)

    # 6 used out of 10. Remaining = 4. Reserved quota = 4.
    # Background check should be BLOCKED to protect user searches
    bg_allowed, reason, _ = guardian.check_budget(PROVIDER_SERPAPI, CALLER_BACKGROUND_CHECK)
    assert bg_allowed is False
    assert "reserved quota" in reason.lower()

    # User search should still be ALLOWED
    user_allowed, _, _ = guardian.check_budget(PROVIDER_SERPAPI, CALLER_USER_SEARCH)
    assert user_allowed is True


# =====================================================================
# 16. DUPLICATE SCHEDULED RUNS PREVENTED BY CRON LOCK
# =====================================================================
def test_duplicate_scheduled_runs_prevented_by_cron_lock():
    # Process 1 acquires lock
    acquired_1 = guardian.acquire_cron_lock("price_checker", lock_ttl_seconds=60)
    assert acquired_1 is True

    # Process 2 attempts to acquire lock for same job -> BLOCKED!
    acquired_2 = guardian.acquire_cron_lock("price_checker", lock_ttl_seconds=60)
    assert acquired_2 is False

    # Process 1 completes and releases lock
    guardian.release_cron_lock("price_checker")

    # Process 2 can now acquire lock
    acquired_3 = guardian.acquire_cron_lock("price_checker", lock_ttl_seconds=60)
    assert acquired_3 is True
    guardian.release_cron_lock("price_checker")


# =====================================================================
# 17. DIAGNOSTICS ENDPOINT SECURITY AND METRICS INTEGRITY
# =====================================================================
def test_diagnostics_endpoint_security_and_metrics(client, monkeypatch):
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "super_secret_diagnostics_token_99")

    # 1. Unauthorized request without token
    res_unauth = client.get("/api/diagnostics/quota")
    assert res_unauth.status_code == 401

    # 2. Authorized request with Bearer token
    res_auth = client.get(
        "/api/diagnostics/quota",
        headers={"Authorization": "Bearer super_secret_diagnostics_token_99"}
    )
    assert res_auth.status_code == 200
    data = res_auth.get_json()

    assert data["status"] == "operational"
    assert "metrics" in data
    assert "budgets" in data
    assert "circuits" in data
    assert "cache_ttls" in data
    assert data["metrics"]["measurement_type"] == "measured"
    assert data["budgets"]["serpapi"]["budget_type"] == "configured"
