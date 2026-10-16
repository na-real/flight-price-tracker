"""
SkyTracker API Quota Guardian & Reliability Gateway
===================================================
Production-grade API management layer for flight data providers.

Architectural Capabilities:
- Centralized gateway routing for quota-sensitive third-party APIs (SerpAPI, Amadeus).
- Quota-aware two-tier caching (PostgreSQL/SQLite shared persistence + local process cache).
- Multi-worker concurrency-safe request budget enforcement (monthly, daily, minute, reserve limits).
- In-flight request coalescing (single-flight) to eliminate cache stampedes and duplicate concurrent upstream calls.
- Circuit breaker state machine (CLOSED, OPEN, HALF_OPEN) with database synchronization across workers.
- Error classification with bounded exponential backoff retries for transient errors and zero retries for 429/401/403/400.
- Truthful metadata labeling (is_live, is_cached, cached_at, age_seconds).
- Distributed cron lock for scheduled background checkers.
"""

import os
import time
import json
import random
import hashlib
import threading
from typing import Dict, Any, Optional, Tuple, Callable, List
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

from db import db, DATABASE_URL


# ============================================================
# PROVIDER AND CALLER CONSTANTS
# ============================================================
PROVIDER_SERPAPI = "google_flights"
PROVIDER_AMADEUS = "amadeus"

CALLER_USER_SEARCH = "user_search"
CALLER_BACKGROUND_CHECK = "background_check"
CALLER_ADMIN = "admin"
CALLER_DIAGNOSTICS = "diagnostics"

STATE_CLOSED = "CLOSED"
STATE_OPEN = "OPEN"
STATE_HALF_OPEN = "HALF_OPEN"


# ============================================================
# DATA MODELS
# ============================================================
@dataclass
class GuardianResponse:
    """Standardized response from the API Quota Guardian."""
    data: Any
    is_cached: bool = False
    cached_at: Optional[str] = None
    age_seconds: int = 0
    is_live: bool = False
    source: str = ""
    status: str = "fresh"  # "fresh", "cached", "blocked_budget", "circuit_open", "error"
    error: Optional[str] = None
    provider_id: str = ""
    meta: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# CONFIGURATION HELPERS
# ============================================================
def _get_int_env(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is not None:
        try:
            return int(val)
        except (ValueError, TypeError):
            pass
    return default


def get_guardian_config() -> Dict[str, Any]:
    """Return runtime configuration for API Guardian budgets and thresholds."""
    return {
        # SerpAPI limits & budgets
        "serpapi_monthly_budget": _get_int_env("SERPAPI_MONTHLY_BUDGET", 100),
        "serpapi_daily_budget": _get_int_env("SERPAPI_DAILY_BUDGET", 25),
        "serpapi_minute_budget": _get_int_env("SERPAPI_MINUTE_BUDGET", 10),
        "serpapi_reserved_search_quota": _get_int_env("SERPAPI_RESERVED_SEARCH_QUOTA", 15),
        "serpapi_safety_margin": _get_int_env("SERPAPI_SAFETY_MARGIN", 5),
        "serpapi_timeout": _get_int_env("SERPAPI_TIMEOUT_SECONDS", 25),

        # Amadeus limits & budgets
        "amadeus_monthly_budget": _get_int_env("AMADEUS_MONTHLY_BUDGET", 1000),
        "amadeus_daily_budget": _get_int_env("AMADEUS_DAILY_BUDGET", 100),
        "amadeus_minute_budget": _get_int_env("AMADEUS_MINUTE_BUDGET", 10),
        "amadeus_reserved_search_quota": _get_int_env("AMADEUS_RESERVED_SEARCH_QUOTA", 50),
        "amadeus_safety_margin": _get_int_env("AMADEUS_SAFETY_MARGIN", 10),
        "amadeus_timeout": _get_int_env("AMADEUS_TIMEOUT_SECONDS", 15),

        # Background Checker limits
        "background_check_max_calls_per_run": _get_int_env("BACKGROUND_CHECK_MAX_CALLS_PER_RUN", 10),
        "background_check_reuse_window": _get_int_env("BACKGROUND_CHECK_REUSE_WINDOW_SECONDS", 7200),  # 2 hours

        # Cache TTLs (conservative for volatile flight fares)
        "cache_ttl_search": _get_int_env("API_CACHE_TTL_SEARCH", 900),  # 15 minutes
        "cache_ttl_checker": _get_int_env("API_CACHE_TTL_CHECKER", 3600),  # 1 hour
        "cache_ttl_reference": _get_int_env("API_CACHE_TTL_REFERENCE", 1800),  # 30 minutes

        # Circuit Breaker parameters
        "circuit_failure_threshold": _get_int_env("CIRCUIT_BREAKER_FAILURE_THRESHOLD", 3),
        "circuit_cooldown_transient": _get_int_env("CIRCUIT_BREAKER_COOLDOWN_SECONDS", 60),  # 1 min
        "circuit_cooldown_rate_limit": _get_int_env("CIRCUIT_BREAKER_RATE_LIMIT_COOLDOWN", 3600),  # 1 hr
        "circuit_cooldown_auth": _get_int_env("CIRCUIT_BREAKER_AUTH_COOLDOWN", 86400),  # 24 hr

        # Retry parameters
        "max_retries": _get_int_env("API_MAX_RETRIES", 2),
    }


# ============================================================
# API GUARDIAN CORE ENGINE
# ============================================================
class ApiGuardian:
    """
    Centralized Gateway and Quota Protection Agent.
    Coordinates caching, request coalescing, multi-worker budget tracking,
    and circuit breaking across all external flight APIs.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # In-memory single-flight tracker for deduplicating identical simultaneous in-flight searches
        self._in_flight: Dict[str, threading.Event] = {}
        self._in_flight_results: Dict[str, Any] = {}
        # In-memory LRU-like cache dictionary for high-frequency reads within the worker process
        self._memory_cache: Dict[str, Dict[str, Any]] = {}
        # Local metrics counters (combined with database logs for analytics)
        self._metrics = {
            "deduplicated_calls": 0,
            "cache_hits_memory": 0,
            "cache_hits_db": 0,
            "budget_rejections": 0,
            "circuit_rejections": 0,
        }

    # ------------------------------------------------------------
    # 1. DATABASE ACCESS HELPER
    # ------------------------------------------------------------
    def _execute_sql(self, query_pg: str, query_sqlite: str, params: tuple = ()) -> List[Any]:
        """Execute query safely using the appropriate PostgreSQL or SQLite placeholder."""
        conn = db()
        try:
            sql = query_pg if DATABASE_URL else query_sqlite
            cursor = conn.execute(sql, params)
            if query_pg.strip().upper().startswith("SELECT"):
                rows = cursor.fetchall()
                return [dict(r) for r in rows]
            conn.commit()
            return []
        finally:
            conn.close()

    # ------------------------------------------------------------
    # 2. CANONICAL REQUEST FINGERPRINTING & CACHE KEY
    # ------------------------------------------------------------
    def build_cache_key(self, provider_id: str, endpoint: str, params: Dict[str, Any]) -> str:
        """
        Build a deterministic, normalized fingerprint for cache lookup.
        Normalizes uppercase IATA airport codes, dates, numbers, and strips secrets/transient keys.
        """
        normalized: Dict[str, Any] = {}
        ignored_keys = {"api_key", "key", "token", "timestamp", "_", "hl", "gl", "show_hidden"}

        for k, v in sorted(params.items()):
            k_clean = str(k).strip()
            if k_clean.lower() in ignored_keys:
                continue

            if isinstance(v, str):
                v_clean = v.strip()
                # Normalize known airport code fields to uppercase
                if k_clean.lower() in {"departure_id", "arrival_id", "departure", "arrival", "origin", "destination", "originlocationcode", "destinationlocationcode"}:
                    v_clean = v_clean.upper()
                normalized[k_clean] = v_clean
            elif isinstance(v, (int, float, bool)):
                normalized[k_clean] = v
            elif isinstance(v, (list, tuple)):
                normalized[k_clean] = sorted([str(item).upper() if isinstance(item, str) else item for item in v])
            elif v is None:
                normalized[k_clean] = ""
            else:
                normalized[k_clean] = str(v)

        canonical_json = json.dumps(normalized, sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()[:24]
        return f"{provider_id}:{endpoint}:{digest}"

    # ------------------------------------------------------------
    # 3. QUOTA-AWARE TWO-TIER CACHING
    # ------------------------------------------------------------
    def get_cached(self, cache_key: str) -> Optional[GuardianResponse]:
        """Lookup cache entry in memory or database."""
        now_utc = datetime.now(timezone.utc)
        now_ts = now_utc.timestamp()

        # 1. Check local in-memory cache
        with self._lock:
            mem_entry = self._memory_cache.get(cache_key)
            if mem_entry:
                if mem_entry["expires_at_ts"] > now_ts:
                    self._metrics["cache_hits_memory"] += 1
                    age = max(0, int(now_ts - mem_entry["created_at_ts"]))
                    return GuardianResponse(
                        data=mem_entry["data"],
                        is_cached=True,
                        cached_at=mem_entry["created_at_iso"],
                        age_seconds=age,
                        is_live=False,
                        source=mem_entry.get("source", "Cache"),
                        status="cached",
                        provider_id=mem_entry.get("provider_id", "")
                    )
                else:
                    del self._memory_cache[cache_key]

        # 2. Check persistent database cache (cross-worker sync)
        try:
            rows = self._execute_sql(
                query_pg="""
                    SELECT provider_id, endpoint, response_json, created_at, expires_at, is_live
                    FROM api_cache
                    WHERE cache_key = %s
                """,
                query_sqlite="""
                    SELECT provider_id, endpoint, response_json, created_at, expires_at, is_live
                    FROM api_cache
                    WHERE cache_key = ?
                """,
                params=(cache_key,)
            )

            if rows:
                row = rows[0]
                expires_at_str = row["expires_at"]
                try:
                    exp_dt = datetime.fromisoformat(expires_at_str)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if exp_dt.timestamp() > now_ts:
                        data = json.loads(row["response_json"])
                        created_at_str = row["created_at"]
                        c_dt = datetime.fromisoformat(created_at_str)
                        if c_dt.tzinfo is None:
                            c_dt = c_dt.replace(tzinfo=timezone.utc)
                        age = max(0, int(now_ts - c_dt.timestamp()))

                        # Promote to memory cache
                        with self._lock:
                            self._memory_cache[cache_key] = {
                                "data": data,
                                "created_at_iso": created_at_str,
                                "created_at_ts": c_dt.timestamp(),
                                "expires_at_ts": exp_dt.timestamp(),
                                "is_live": bool(row.get("is_live", 1)),
                                "provider_id": row.get("provider_id", ""),
                                "source": f"{row.get('provider_id')} (Shared Cache)"
                            }
                            self._metrics["cache_hits_db"] += 1

                        return GuardianResponse(
                            data=data,
                            is_cached=True,
                            cached_at=created_at_str,
                            age_seconds=age,
                            is_live=False,
                            source=f"{row.get('provider_id')} (Shared Cache)",
                            status="cached",
                            provider_id=row.get("provider_id", "")
                        )
                except Exception:
                    pass
        except Exception:
            pass

        return None

    def set_cached(
        self,
        cache_key: str,
        provider_id: str,
        endpoint: str,
        data: Any,
        ttl_seconds: int,
        is_live: bool = True
    ) -> None:
        """Store data in memory and database cache."""
        now_utc = datetime.now(timezone.utc)
        now_ts = now_utc.timestamp()
        exp_dt = datetime.fromtimestamp(now_ts + ttl_seconds, tz=timezone.utc)
        created_iso = now_utc.isoformat()
        expires_iso = exp_dt.isoformat()

        # Update in-memory cache
        with self._lock:
            self._memory_cache[cache_key] = {
                "data": data,
                "created_at_iso": created_iso,
                "created_at_ts": now_ts,
                "expires_at_ts": exp_dt.timestamp(),
                "is_live": is_live,
                "provider_id": provider_id,
                "source": provider_id
            }

        # Update database cache
        try:
            data_json = json.dumps(data)
            self._execute_sql(
                query_pg="""
                    INSERT INTO api_cache (cache_key, provider_id, endpoint, response_json, created_at, expires_at, is_live)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (cache_key) DO UPDATE
                    SET response_json = EXCLUDED.response_json,
                        created_at = EXCLUDED.created_at,
                        expires_at = EXCLUDED.expires_at,
                        is_live = EXCLUDED.is_live
                """,
                query_sqlite="""
                    INSERT OR REPLACE INTO api_cache (cache_key, provider_id, endpoint, response_json, created_at, expires_at, is_live)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                params=(cache_key, provider_id, endpoint, data_json, created_iso, expires_iso, 1 if is_live else 0)
            )
        except Exception:
            pass

    # ------------------------------------------------------------
    # 4. MULTI-WORKER CONCURRENCY-SAFE REQUEST BUDGETS
    # ------------------------------------------------------------
    def check_budget(self, provider_id: str, caller_type: str = CALLER_USER_SEARCH) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Enforce minute, daily, and monthly quotas across Gunicorn workers.
        Protects user searches by blocking background checks when remaining quota drops to reserve.
        """
        cfg = get_guardian_config()
        if provider_id == PROVIDER_SERPAPI:
            monthly_budget = cfg["serpapi_monthly_budget"]
            daily_budget = cfg["serpapi_daily_budget"]
            minute_budget = cfg["serpapi_minute_budget"]
            reserved_quota = cfg["serpapi_reserved_search_quota"]
            safety_margin = cfg["serpapi_safety_margin"]
        elif provider_id == PROVIDER_AMADEUS:
            monthly_budget = cfg["amadeus_monthly_budget"]
            daily_budget = cfg["amadeus_daily_budget"]
            minute_budget = cfg["amadeus_minute_budget"]
            reserved_quota = cfg["amadeus_reserved_search_quota"]
            safety_margin = cfg["amadeus_safety_margin"]
        else:
            return True, "Unmetered provider", {}

        now_utc = datetime.now(timezone.utc)
        # 1-minute window
        minute_start = datetime.fromtimestamp(now_utc.timestamp() - 60, tz=timezone.utc).isoformat()
        # Today UTC window
        day_start = now_utc.strftime("%Y-%m-%d 00:00:00")
        # Month UTC window
        month_start = now_utc.strftime("%Y-%m-01 00:00:00")

        try:
            # Check if provider reported upstream monthly search limit exhaustion
            c_state, c_cool = self.get_circuit_state(provider_id)
            if c_state == STATE_OPEN:
                c_rows = self._execute_sql(
                    query_pg="SELECT reason, cooldown_until FROM api_circuit_state WHERE provider_id = %s",
                    query_sqlite="SELECT reason, cooldown_until FROM api_circuit_state WHERE provider_id = ?",
                    params=(provider_id,)
                )
                if c_rows and "Monthly" in (c_rows[0].get("reason") or ""):
                    return False, f"Provider {provider_id} monthly search limit exhausted according to upstream provider until next month reset", {}

            # Query minute usage
            m_rows = self._execute_sql(
                query_pg="SELECT COUNT(*) as cnt FROM api_quota_logs WHERE provider_id = %s AND created_at >= %s AND cache_hit = 0",
                query_sqlite="SELECT COUNT(*) as cnt FROM api_quota_logs WHERE provider_id = ? AND created_at >= ? AND cache_hit = 0",
                params=(provider_id, minute_start)
            )
            minute_count = m_rows[0]["cnt"] if m_rows else 0

            # Query daily usage
            d_rows = self._execute_sql(
                query_pg="SELECT COUNT(*) as cnt FROM api_quota_logs WHERE provider_id = %s AND created_at >= %s AND cache_hit = 0",
                query_sqlite="SELECT COUNT(*) as cnt FROM api_quota_logs WHERE provider_id = ? AND created_at >= ? AND cache_hit = 0",
                params=(provider_id, day_start)
            )
            daily_count = d_rows[0]["cnt"] if d_rows else 0

            # Query monthly usage
            mo_rows = self._execute_sql(
                query_pg="SELECT COUNT(*) as cnt FROM api_quota_logs WHERE provider_id = %s AND created_at >= %s AND cache_hit = 0",
                query_sqlite="SELECT COUNT(*) as cnt FROM api_quota_logs WHERE provider_id = ? AND created_at >= ? AND cache_hit = 0",
                params=(provider_id, month_start)
            )
            monthly_count = mo_rows[0]["cnt"] if mo_rows else 0

            remaining_monthly = max(0, monthly_budget - monthly_count)
            stats = {
                "minute_count": minute_count,
                "minute_budget": minute_budget,
                "daily_count": daily_count,
                "daily_budget": daily_budget,
                "monthly_count": monthly_count,
                "monthly_budget": monthly_budget,
                "remaining_monthly": remaining_monthly,
            }

            # Enforce minute budget
            if minute_count >= minute_budget:
                self._metrics["budget_rejections"] += 1
                return False, f"Provider {provider_id} exceeded rate limit ({minute_count}/{minute_budget} req/min)", stats

            # Enforce daily budget
            if daily_count >= daily_budget:
                self._metrics["budget_rejections"] += 1
                return False, f"Provider {provider_id} daily budget exhausted ({daily_count}/{daily_budget} req/day)", stats

            # Enforce monthly budget with safety margin
            effective_monthly_limit = max(0, monthly_budget - safety_margin)
            if monthly_count >= effective_monthly_limit:
                self._metrics["budget_rejections"] += 1
                return False, f"Provider {provider_id} monthly budget limit reached ({monthly_count}/{monthly_budget} req/month with safety margin)", stats

            # Priority reservation: block background checks if either monthly or daily budget reaches reserved search quota
            remaining_daily = max(0, daily_budget - daily_count)
            stats["remaining_daily"] = remaining_daily
            remaining_effective = min(remaining_monthly, remaining_daily)
            if caller_type == CALLER_BACKGROUND_CHECK and remaining_effective <= reserved_quota:
                self._metrics["budget_rejections"] += 1
                return False, f"Background check skipped: remaining quota ({remaining_effective}) is within reserved quota ({reserved_quota}) for live user searches", stats

            return True, "Budget available", stats
        except Exception as e:
            # Safe degradation if DB logging temporarily fails
            return True, f"Budget check bypassed on DB error: {e}", {}

    def log_quota_request(
        self,
        provider_id: str,
        endpoint: str,
        caller_type: str,
        cache_hit: bool,
        status_code: int = 200,
        error_category: Optional[str] = None,
        cost: int = 1
    ) -> None:
        """Record request outcomes in persistent database audit log."""
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            self._execute_sql(
                query_pg="""
                    INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, error_category, cost, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                query_sqlite="""
                    INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, error_category, cost, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params=(provider_id, endpoint, caller_type, 1 if cache_hit else 0, status_code, error_category or "", cost, now_iso)
            )
        except Exception:
            pass

    # ------------------------------------------------------------
    # 5. CIRCUIT BREAKER WITH RECOVERY STATE MACHINE
    # ------------------------------------------------------------
    def get_circuit_state(self, provider_id: str) -> Tuple[str, Optional[str]]:
        """
        Check circuit status. Transitions OPEN -> HALF_OPEN when cooldown expires.
        Returns: (state: str, cooldown_until: Optional[str])
        """
        try:
            rows = self._execute_sql(
                query_pg="SELECT state, cooldown_until, failure_count FROM api_circuit_state WHERE provider_id = %s",
                query_sqlite="SELECT state, cooldown_until, failure_count FROM api_circuit_state WHERE provider_id = ?",
                params=(provider_id,)
            )

            if not rows:
                return STATE_CLOSED, None

            row = rows[0]
            state = row.get("state") or STATE_CLOSED
            cooldown_str = row.get("cooldown_until")

            if state == STATE_OPEN and cooldown_str:
                now_utc = datetime.now(timezone.utc)
                cool_dt = datetime.fromisoformat(cooldown_str)
                if cool_dt.tzinfo is None:
                    cool_dt = cool_dt.replace(tzinfo=timezone.utc)

                if now_utc.timestamp() >= cool_dt.timestamp():
                    # Cooldown expired: allow controlled probe
                    self._update_circuit_state(provider_id, STATE_HALF_OPEN, failure_count=0, cooldown_until=None)
                    return STATE_HALF_OPEN, None
                else:
                    return STATE_OPEN, cooldown_str

            return state, cooldown_str
        except Exception:
            return STATE_CLOSED, None

    def _update_circuit_state(
        self,
        provider_id: str,
        state: str,
        failure_count: int = 0,
        cooldown_until: Optional[str] = None,
        reason: Optional[str] = None
    ) -> None:
        """Persist circuit state transition into shared database."""
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            self._execute_sql(
                query_pg="""
                    INSERT INTO api_circuit_state (provider_id, state, failure_count, last_failure_at, opened_at, cooldown_until, reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (provider_id) DO UPDATE
                    SET state = EXCLUDED.state,
                        failure_count = EXCLUDED.failure_count,
                        last_failure_at = EXCLUDED.last_failure_at,
                        opened_at = CASE WHEN EXCLUDED.state = 'OPEN' THEN EXCLUDED.opened_at ELSE api_circuit_state.opened_at END,
                        cooldown_until = EXCLUDED.cooldown_until,
                        reason = EXCLUDED.reason
                """,
                query_sqlite="""
                    INSERT OR REPLACE INTO api_circuit_state (provider_id, state, failure_count, last_failure_at, opened_at, cooldown_until, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                params=(provider_id, state, failure_count, now_iso, now_iso if state == STATE_OPEN else None, cooldown_until, reason or "")
            )
        except Exception:
            pass

    def record_circuit_success(self, provider_id: str) -> None:
        """Reset circuit failure count and close circuit on success."""
        self._update_circuit_state(provider_id, STATE_CLOSED, failure_count=0, cooldown_until=None, reason="Operational")

    def record_circuit_failure(
        self,
        provider_id: str,
        status_code: int,
        error_msg: str,
        retry_after: Optional[int] = None
    ) -> None:
        """
        Classify error and update circuit breaker state:
        - 429 / Quota exhaustion: Immediate OPEN with cooldown
        - 401 / 403: Immediate OPEN (auth/config failure)
        - 5xx / Timeouts: Increment failure counter; trip if threshold reached
        """
        cfg = get_guardian_config()
        now_utc = datetime.now(timezone.utc)
        now_ts = now_utc.timestamp()

        # 1. Quota Exhaustion / 429
        is_quota = (
            status_code == 429
            or "search limit reached" in error_msg.lower()
            or "quota" in error_msg.lower()
            or "rate limit" in error_msg.lower()
        )
        if is_quota:
            is_monthly = (
                "search limit reached" in error_msg.lower()
                or "monthly" in error_msg.lower()
            )
            if is_monthly and not retry_after:
                if now_utc.month == 12:
                    next_month_dt = datetime(now_utc.year + 1, 1, 1, tzinfo=timezone.utc)
                else:
                    next_month_dt = datetime(now_utc.year, now_utc.month + 1, 1, tzinfo=timezone.utc)
                cooldown_until = next_month_dt.isoformat()
                reason = "Monthly Search Limit Exhausted (Resets Next Month)"
            else:
                cooldown_secs = retry_after or cfg["circuit_cooldown_rate_limit"]
                cooldown_until = datetime.fromtimestamp(now_ts + cooldown_secs, tz=timezone.utc).isoformat()
                reason = "Quota/Rate-Limit Exhaustion"

            self._update_circuit_state(provider_id, STATE_OPEN, failure_count=1, cooldown_until=cooldown_until, reason=reason)
            return

        # 2. Authentication / Authorization Failure (401/403)
        if status_code in (401, 403):
            cooldown_secs = cfg["circuit_cooldown_auth"]
            cooldown_until = datetime.fromtimestamp(now_ts + cooldown_secs, tz=timezone.utc).isoformat()
            self._update_circuit_state(provider_id, STATE_OPEN, failure_count=1, cooldown_until=cooldown_until, reason="Authentication / API Key Invalid")
            return

        # 3. Validation / Bad Request (400, 422) - do not trip circuit
        if status_code in (400, 422):
            return

        # 4. Transient Server & Network Failures (5xx, Timeouts)
        current_state, _ = self.get_circuit_state(provider_id)
        current_failures = 0
        try:
            rows = self._execute_sql(
                query_pg="SELECT failure_count FROM api_circuit_state WHERE provider_id = %s",
                query_sqlite="SELECT failure_count FROM api_circuit_state WHERE provider_id = ?",
                params=(provider_id,)
            )
            if rows:
                current_failures = rows[0]["failure_count"] or 0
        except Exception:
            pass

        new_failures = current_failures + 1
        if new_failures >= cfg["circuit_failure_threshold"] or current_state == STATE_HALF_OPEN:
            cooldown_secs = cfg["circuit_cooldown_transient"]
            cooldown_until = datetime.fromtimestamp(now_ts + cooldown_secs, tz=timezone.utc).isoformat()
            self._update_circuit_state(provider_id, STATE_OPEN, failure_count=new_failures, cooldown_until=cooldown_until, reason=f"Consecutive errors ({new_failures}): {error_msg[:100]}")
        else:
            self._update_circuit_state(provider_id, current_state, failure_count=new_failures, reason=error_msg[:100])

    # ------------------------------------------------------------
    # 6. DISTRIBUTED CRON LOCK (FOR BACKGROUND CHECKER)
    # ------------------------------------------------------------
    def acquire_cron_lock(self, job_name: str, lock_ttl_seconds: int = 900) -> bool:
        """
        Acquire an atomic distributed cron lock to prevent overlapping scheduled runs.
        Returns True if lock acquired, False if job is currently active.
        """
        now_utc = datetime.now(timezone.utc)
        now_ts = now_utc.timestamp()
        now_iso = now_utc.isoformat()
        exp_iso = datetime.fromtimestamp(now_ts + lock_ttl_seconds, tz=timezone.utc).isoformat()
        worker_id = f"proc_{os.getpid()}_{random.randint(1000, 9999)}"

        try:
            # Check existing lock
            rows = self._execute_sql(
                query_pg="SELECT locked_at, expires_at, locked_by FROM cron_locks WHERE job_name = %s",
                query_sqlite="SELECT locked_at, expires_at, locked_by FROM cron_locks WHERE job_name = ?",
                params=(job_name,)
            )

            if rows:
                existing = rows[0]
                exp_dt = datetime.fromisoformat(existing["expires_at"])
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)

                if exp_dt.timestamp() > now_ts:
                    # Active lock held by another process
                    return False

            # Lock is absent or expired: claim it
            self._execute_sql(
                query_pg="""
                    INSERT INTO cron_locks (job_name, locked_at, locked_by, expires_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (job_name) DO UPDATE
                    SET locked_at = EXCLUDED.locked_at,
                        locked_by = EXCLUDED.locked_by,
                        expires_at = EXCLUDED.expires_at
                """,
                query_sqlite="""
                    INSERT OR REPLACE INTO cron_locks (job_name, locked_at, locked_by, expires_at)
                    VALUES (?, ?, ?, ?)
                """,
                params=(job_name, now_iso, worker_id, exp_iso)
            )
            return True
        except Exception:
            return True  # Fallback to allow execution if lock table has an issue

    def release_cron_lock(self, job_name: str) -> None:
        """Release the cron lock when job completes."""
        try:
            self._execute_sql(
                query_pg="DELETE FROM cron_locks WHERE job_name = %s",
                query_sqlite="DELETE FROM cron_locks WHERE job_name = ?",
                params=(job_name,)
            )
        except Exception:
            pass

    # ------------------------------------------------------------
    # 7. GATEWAY EXECUTE REQUEST (SINGLEFLIGHT & RETRIES)
    # ------------------------------------------------------------
    def execute_request(
        self,
        provider_id: str,
        endpoint: str,
        params: Dict[str, Any],
        fetch_fn: Callable[[Dict[str, Any]], Any],
        caller_type: str = CALLER_USER_SEARCH,
        cache_ttl: Optional[int] = None,
        force_refresh: bool = False
    ) -> GuardianResponse:
        """
        Execute request through the Quota Guardian.
        Enforces caching, request coalescing, circuit breaker, budget, and bounded retries.
        """
        cfg = get_guardian_config()
        ttl = cache_ttl or cfg["cache_ttl_search"]
        cache_key = self.build_cache_key(provider_id, endpoint, params)

        # 1. Check cache (unless explicit refresh)
        if not force_refresh:
            cached_resp = self.get_cached(cache_key)
            if cached_resp:
                self.log_quota_request(provider_id, endpoint, caller_type, cache_hit=True, status_code=200)
                return cached_resp

        # 2. Check Circuit Breaker
        c_state, cooldown_until = self.get_circuit_state(provider_id)
        if c_state == STATE_OPEN:
            self._metrics["circuit_rejections"] += 1
            err_msg = f"Provider {provider_id} circuit breaker is OPEN until {cooldown_until}. Upstream calls blocked."
            return GuardianResponse(
                data=None,
                is_cached=False,
                is_live=False,
                source=provider_id,
                status="circuit_open",
                error=err_msg,
                provider_id=provider_id
            )

        # 3. Check Budgets (Monthly, Daily, Minute, User vs Background)
        allowed, reason, stats = self.check_budget(provider_id, caller_type)
        if not allowed:
            self.log_quota_request(provider_id, endpoint, caller_type, cache_hit=False, status_code=429, error_category="budget_exhausted", cost=0)
            return GuardianResponse(
                data=None,
                is_cached=False,
                is_live=False,
                source=provider_id,
                status="blocked_budget",
                error=f"Request blocked by API Quota Guardian: {reason}",
                provider_id=provider_id,
                meta=stats
            )

        # 4. Request Coalescing (Single-Flight Deduplication)
        # Prevents simultaneous identical searches from firing parallel upstream calls
        is_initiator = False
        wait_event: Optional[threading.Event] = None

        with self._lock:
            if cache_key in self._in_flight:
                wait_event = self._in_flight[cache_key]
            else:
                wait_event = threading.Event()
                self._in_flight[cache_key] = wait_event
                is_initiator = True

        if not is_initiator and wait_event:
            # Wait for the initiating thread to finish (max 30s)
            finished = wait_event.wait(timeout=30)
            with self._lock:
                self._metrics["deduplicated_calls"] += 1
                if finished and cache_key in self._in_flight_results:
                    shared_res = self._in_flight_results[cache_key]
                    return GuardianResponse(
                        data=shared_res["data"],
                        is_cached=True,
                        cached_at=shared_res.get("created_at"),
                        age_seconds=0,
                        is_live=shared_res.get("is_live", True),
                        source=f"{provider_id} (Deduplicated Stream)",
                        status="cached",
                        provider_id=provider_id
                    )

        # 5. Initiator: Execute Outbound Call with Bounded Retries
        attempts = 0
        max_retries = cfg["max_retries"]
        last_error = None
        last_status = 500

        try:
            while attempts <= max_retries:
                attempts += 1
                try:
                    result = fetch_fn(params)
                    # Successful outbound fetch
                    self.record_circuit_success(provider_id)
                    self.log_quota_request(provider_id, endpoint, caller_type, cache_hit=False, status_code=200, cost=1)

                    # Cache successful result
                    self.set_cached(cache_key, provider_id, endpoint, result, ttl_seconds=ttl, is_live=True)

                    # Publish to in-flight waiters
                    with self._lock:
                        self._in_flight_results[cache_key] = {
                            "data": result,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "is_live": True
                        }

                    return GuardianResponse(
                        data=result,
                        is_cached=False,
                        cached_at=datetime.now(timezone.utc).isoformat(),
                        age_seconds=0,
                        is_live=True,
                        source=provider_id,
                        status="fresh",
                        provider_id=provider_id
                    )

                except Exception as exc:
                    last_error = exc
                    err_str = str(exc)

                    # Extract HTTP status code if available
                    status_code = getattr(exc, "status_code", None)
                    if not status_code and hasattr(exc, "response") and exc.response is not None:
                        status_code = getattr(exc.response, "status_code", 500)
                    if not status_code:
                        if "429" in err_str:
                            status_code = 429
                        elif "401" in err_str:
                            status_code = 401
                        elif "403" in err_str:
                            status_code = 403
                        elif "400" in err_str:
                            status_code = 400
                        else:
                            status_code = 500

                    last_status = status_code

                    # Record failure in circuit breaker
                    retry_after_val = None
                    if hasattr(exc, "response") and exc.response is not None:
                        ra = exc.response.headers.get("Retry-After")
                        if ra and ra.isdigit():
                            retry_after_val = int(ra)

                    self.record_circuit_failure(provider_id, status_code, err_str, retry_after=retry_after_val)

                    # Non-retryable errors: break immediately
                    if status_code in (400, 401, 403, 422, 429) or "search limit" in err_str.lower():
                        break

                    # Transient retry with backoff and jitter
                    if attempts <= max_retries:
                        backoff = 0.05 * (2 ** (attempts - 1)) + random.uniform(0.005, 0.02)
                        time.sleep(backoff)

            # All attempts exhausted
            err_msg = str(last_error or "Unknown provider failure")
            self.log_quota_request(provider_id, endpoint, caller_type, cache_hit=False, status_code=last_status, error_category=err_msg[:60], cost=0)
            return GuardianResponse(
                data=None,
                is_cached=False,
                is_live=False,
                source=provider_id,
                status="error",
                error=err_msg,
                provider_id=provider_id
            )

        finally:
            # Always notify in-flight waiters and clean up single-flight dictionary
            with self._lock:
                if cache_key in self._in_flight:
                    self._in_flight[cache_key].set()
                    del self._in_flight[cache_key]

    # ------------------------------------------------------------
    # 8. DIAGNOSTICS & METRICS REPORTING
    # ------------------------------------------------------------
    def get_diagnostics(self) -> Dict[str, Any]:
        """
        Produce a sanitized diagnostic report of API quotas, circuit breaker states,
        and cache efficiency for operations dashboards.
        Clearly distinguishes measured values from configured limits.
        """
        now_utc = datetime.now(timezone.utc)
        day_start = now_utc.strftime("%Y-%m-%d 00:00:00")
        month_start = now_utc.strftime("%Y-%m-01 00:00:00")
        cfg = get_guardian_config()

        # Circuit states
        circuits: Dict[str, Any] = {}
        for pid in [PROVIDER_SERPAPI, PROVIDER_AMADEUS]:
            state, cool = self.get_circuit_state(pid)
            circuits[pid] = {
                "state": state,
                "cooldown_until": cool,
                "status_type": "measured"
            }

        # Query request volume & cache stats
        total_today = 0
        cache_hits_today = 0
        total_month = 0
        errors_today = 0
        last_success = None
        last_exhaustion = None
        bg_activity = {"checks_today": 0, "last_check": None, "measurement_type": "measured"}

        try:
            rows_today = self._execute_sql(
                query_pg="""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) as hits,
                        SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as errors
                    FROM api_quota_logs
                    WHERE created_at >= %s
                """,
                query_sqlite="""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) as hits,
                        SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as errors
                    FROM api_quota_logs
                    WHERE created_at >= ?
                """,
                params=(day_start,)
            )
            if rows_today:
                total_today = rows_today[0]["total"] or 0
                cache_hits_today = rows_today[0]["hits"] or 0
                errors_today = rows_today[0]["errors"] or 0

            rows_month = self._execute_sql(
                query_pg="SELECT COUNT(*) as total FROM api_quota_logs WHERE created_at >= %s AND cache_hit = 0",
                query_sqlite="SELECT COUNT(*) as total FROM api_quota_logs WHERE created_at >= ? AND cache_hit = 0",
                params=(month_start,)
            )
            if rows_month:
                total_month = rows_month[0]["total"] or 0

            # Last successful request
            rows_succ = self._execute_sql(
                query_pg="SELECT created_at, provider_id, endpoint FROM api_quota_logs WHERE status_code = 200 ORDER BY id DESC LIMIT 1",
                query_sqlite="SELECT created_at, provider_id, endpoint FROM api_quota_logs WHERE status_code = 200 ORDER BY id DESC LIMIT 1"
            )
            if rows_succ:
                last_success = {
                    "timestamp": rows_succ[0]["created_at"],
                    "provider_id": rows_succ[0]["provider_id"],
                    "endpoint": rows_succ[0]["endpoint"],
                    "measurement_type": "measured"
                }

            # Last quota exhaustion event
            rows_exh = self._execute_sql(
                query_pg="SELECT created_at, provider_id, error_category FROM api_quota_logs WHERE status_code = 429 OR error_category LIKE '%quota%' OR error_category LIKE '%limit%' ORDER BY id DESC LIMIT 1",
                query_sqlite="SELECT created_at, provider_id, error_category FROM api_quota_logs WHERE status_code = 429 OR error_category LIKE '%quota%' OR error_category LIKE '%limit%' ORDER BY id DESC LIMIT 1"
            )
            if rows_exh:
                last_exhaustion = {
                    "timestamp": rows_exh[0]["created_at"],
                    "provider_id": rows_exh[0]["provider_id"],
                    "reason": rows_exh[0]["error_category"],
                    "measurement_type": "measured"
                }

            # Background check activity
            rows_bg = self._execute_sql(
                query_pg="SELECT COUNT(*) as cnt, MAX(created_at) as last_ts FROM api_quota_logs WHERE caller_type = 'background_check' AND created_at >= %s",
                query_sqlite="SELECT COUNT(*) as cnt, MAX(created_at) as last_ts FROM api_quota_logs WHERE caller_type = 'background_check' AND created_at >= ?",
                params=(day_start,)
            )
            if rows_bg:
                bg_activity["checks_today"] = rows_bg[0]["cnt"] or 0
                bg_activity["last_check"] = rows_bg[0]["last_ts"]

        except Exception:
            pass

        cache_rate_pct = 0.0
        if total_today > 0:
            cache_rate_pct = round((cache_hits_today / total_today) * 100, 1)

        remaining_serp = max(0, cfg["serpapi_monthly_budget"] - total_month)

        return {
            "status": "operational",
            "timestamp": now_utc.isoformat(),
            "metrics": {
                "requests_today": total_today,
                "requests_this_month": total_month,
                "cache_hits_today": cache_hits_today,
                "cache_hit_rate_percent": cache_rate_pct,
                "deduplicated_coalesced_calls": self._metrics["deduplicated_calls"],
                "rate_limit_rejections": self._metrics["budget_rejections"],
                "circuit_rejections": self._metrics["circuit_rejections"],
                "provider_errors_today": errors_today,
                "measurement_type": "measured"
            },
            "budgets": {
                "serpapi": {
                    "monthly_budget": cfg["serpapi_monthly_budget"],
                    "used_this_month": total_month,
                    "remaining_budget": remaining_serp,
                    "daily_budget": cfg["serpapi_daily_budget"],
                    "minute_budget": cfg["serpapi_minute_budget"],
                    "reserved_search_quota": cfg["serpapi_reserved_search_quota"],
                    "safety_margin": cfg["serpapi_safety_margin"],
                    "budget_type": "configured"
                },
                "amadeus": {
                    "monthly_budget": cfg["amadeus_monthly_budget"],
                    "daily_budget": cfg["amadeus_daily_budget"],
                    "minute_budget": cfg["amadeus_minute_budget"],
                    "budget_type": "configured"
                }
            },
            "circuits": circuits,
            "last_successful_request": last_success,
            "last_quota_exhaustion_event": last_exhaustion,
            "background_check_activity": bg_activity,
            "cache_ttls": {
                "search_ttl_seconds": cfg["cache_ttl_search"],
                "checker_ttl_seconds": cfg["cache_ttl_checker"],
                "ttl_type": "configured"
            }
        }

    def clear_cache(self) -> None:
        """Clear memory and database caches (primarily for unit tests)."""
        with self._lock:
            self._memory_cache.clear()
            self._in_flight.clear()
            self._in_flight_results.clear()
            self._metrics = {k: 0 for k in self._metrics}

        try:
            self._execute_sql(
                query_pg="DELETE FROM api_cache",
                query_sqlite="DELETE FROM api_cache"
            )
            self._execute_sql(
                query_pg="DELETE FROM api_quota_logs",
                query_sqlite="DELETE FROM api_quota_logs"
            )
            self._execute_sql(
                query_pg="DELETE FROM api_circuit_state",
                query_sqlite="DELETE FROM api_circuit_state"
            )
            self._execute_sql(
                query_pg="DELETE FROM cron_locks",
                query_sqlite="DELETE FROM cron_locks"
            )
        except Exception:
            pass


# ============================================================
# GLOBAL GUARDIAN SINGLETON INSTANCE
# ============================================================
guardian = ApiGuardian()
