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
import re
import time
import json
import uuid
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
PROVIDER_SERPAPI = "serpapi"
PROVIDER_AMADEUS = "amadeus"
PROVIDER_FLIGHTAPI = "flightapi"

CALLER_USER_SEARCH = "user_search"
CALLER_BACKGROUND_CHECK = "background_check"
CALLER_ADMIN = "admin"
CALLER_DIAGNOSTICS = "diagnostics"

# Circuit breaker states
STATE_CLOSED = "CLOSED"
STATE_OPEN = "OPEN"
STATE_HALF_OPEN = "HALF_OPEN"
STATE_QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"

# Request priority levels
PRIORITY_CRITICAL = 1     # User-facing operations already in progress, essential system tasks
PRIORITY_NORMAL = 2       # New user-initiated flight searches, price comparisons
PRIORITY_BACKGROUND = 3   # Scheduled price checks, automatic refreshes, nonessential analytics

# Reservation lifecycle states
STATE_RESERVED = "reserved"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
STATE_UNKNOWN = "unknown"
STATE_RECONCILED = "reconciled"
STATE_RELEASED = "released"

# Documented endpoint cost mapping (in provider billing units)
ENDPOINT_COSTS: Dict[str, Dict[str, int]] = {
    PROVIDER_FLIGHTAPI: {
        "onewaytrip": 1,
        "roundtrip": 1,
        "multitrip": 1,
    },
    PROVIDER_SERPAPI: {
        "search.json": 1,
        "search": 1,
        "search_flights": 1,
    },
    PROVIDER_AMADEUS: {
        "v2/shopping/flight-offers": 1,
        "flight-offers": 1,
        "v1/security/oauth2/token": 0,
        "token": 0,
    }
}


def estimate_endpoint_cost(provider_id: str, endpoint: str) -> Optional[int]:
    """
    Estimate billing cost in provider units for a given provider and endpoint.
    Returns:
        int: Cost in provider billing units (>= 0).
        None: If endpoint cost is unknown/unverified (MUST block request per hard quota policy).
    """
    prov = str(provider_id or "").strip().lower()
    ep = str(endpoint or "").strip().lower()

    # 1. Check environment variable override: e.g. FLIGHTAPI_COST_ONEWAYTRIP=1
    env_key = f"{prov.upper()}_COST_{ep.upper().replace('.', '_').replace('/', '_').replace('-', '_')}"
    env_val = os.getenv(env_key)
    if env_val is not None:
        try:
            return int(env_val)
        except (ValueError, TypeError):
            pass

    prov_map = ENDPOINT_COSTS.get(prov)
    if not prov_map:
        return None

    if ep in prov_map:
        return prov_map[ep]

    # Partial / suffix matching (e.g. "onewaytrip" in path)
    for k, cost in prov_map.items():
        if k in ep:
            return cost

    # Per policy: unknown endpoint costs MUST block request
    return None


def sanitize_flightapi_url(url_or_text: str) -> str:
    """
    Redact API keys, authorization tokens, and credentials from URLs, logs, traces, and error messages.
    Redacts FlightAPI path-embedded keys (/onewaytrip/<key>/, etc.) and environment secrets.
    """
    if not url_or_text:
        return ""
    text = str(url_or_text)
    redacted = re.sub(
        r"/(onewaytrip|roundtrip|multitrip)/([^/?\s\"']+)",
        r"/\1/[REDACTED]",
        text
    )
    redacted = re.sub(r"(api_key|apiKey|key)=([^&\s\"']+)", r"\1=[REDACTED]", redacted)
    redacted = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", r"\1[REDACTED]", redacted)

    for env_var in ("FLIGHTAPI_API_KEY", "SERPAPI_KEY", "AMADEUS_CLIENT_SECRET", "AMADEUS_CLIENT_ID", "SECRET_KEY", "ADMIN_DIAGNOSTICS_TOKEN"):
        val = os.getenv(env_var)
        if val and len(val.strip()) >= 5 and val in redacted:
            redacted = redacted.replace(val, "[REDACTED]")

    return redacted


def is_quota_exhausted_error(status_code: int = 0, error_msg: str = "") -> bool:
    """Classifies whether an HTTP status or provider error message indicates quota exhaustion."""
    err_lower = (str(error_msg or "")).lower()
    return (
        status_code == 429
        or "search limit" in err_lower
        or "limit reached" in err_lower
        or "out of searches" in err_lower
        or "no searches left" in err_lower
        or "plan limit" in err_lower
        or "quota" in err_lower
        or "rate limit" in err_lower
    )


# ============================================================
# DATA MODELS
# ============================================================
@dataclass
class GuardianResponse:
    """Standardized response from the API Quota Guardian."""
    data: Any
    is_cached: bool = False
    is_stale: bool = False
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

        # FlightAPI limits & budgets (Configured 30 credit trial; initial safety reserve 5 credits)
        "flightapi_total_budget": _get_int_env("FLIGHTAPI_TOTAL_BUDGET", 30),
        "flightapi_monthly_budget": _get_int_env("FLIGHTAPI_MONTHLY_BUDGET", 30),
        "flightapi_daily_budget": _get_int_env("FLIGHTAPI_DAILY_BUDGET", 10),
        "flightapi_minute_budget": _get_int_env("FLIGHTAPI_MINUTE_BUDGET", 5),
        "flightapi_reserved_search_quota": _get_int_env("FLIGHTAPI_RESERVED_SEARCH_QUOTA", 5),
        "flightapi_safety_margin": _get_int_env("FLIGHTAPI_SAFETY_MARGIN", 5),  # 5 credits initial safety reserve
        "flightapi_timeout": _get_int_env("FLIGHTAPI_TIMEOUT_SECONDS", 25),

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

        # Atomic reservation and alerting parameters
        "reservation_ttl_seconds": _get_int_env("API_RESERVATION_TTL_SECONDS", 60),
        "alert_cooldown_seconds": _get_int_env("ALERT_COOLDOWN_SECONDS", 3600),
        "enable_ai_analysis": os.getenv("ENABLE_GUARDIAN_AI_ANALYSIS", "").lower() in ("true", "1", "yes"),
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
                if k_clean.lower() in {
                    "departure_id", "arrival_id", "departure", "arrival", "origin", "destination",
                    "originlocationcode", "destinationlocationcode",
                    "departure_airport_code", "arrival_airport_code",
                    "arp1", "arp2", "arp3", "arp4", "arp5", "arp6", "arp7", "arp8", "arp9", "arp10"
                }:
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
    def get_cached(self, cache_key: str, allow_stale: bool = False) -> Optional[GuardianResponse]:
        """
        Lookup cache entry in memory or database.
        If allow_stale is True, returns expired entry when fresh data is unavailable.
        """
        now_utc = datetime.now(timezone.utc)
        now_ts = now_utc.timestamp()

        # 1. Check local in-memory cache
        with self._lock:
            mem_entry = self._memory_cache.get(cache_key)
            if mem_entry:
                is_fresh = mem_entry["expires_at_ts"] > now_ts
                if is_fresh or allow_stale:
                    self._metrics["cache_hits_memory"] += 1
                    age = max(0, int(now_ts - mem_entry["created_at_ts"]))
                    return GuardianResponse(
                        data=mem_entry["data"],
                        is_cached=True,
                        is_stale=(not is_fresh),
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
                    is_fresh = exp_dt.timestamp() > now_ts
                    if is_fresh or allow_stale:
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
                                "is_live": False,
                                "provider_id": row.get("provider_id", ""),
                                "source": f"{row.get('provider_id')} (Shared Cache)"
                            }
                            self._metrics["cache_hits_db"] += 1

                        return GuardianResponse(
                            data=data,
                            is_cached=True,
                            is_stale=(not is_fresh),
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
    # 4. MULTI-WORKER CONCURRENCY-SAFE REQUEST BUDGETS & RESERVATIONS
    # ------------------------------------------------------------
    def reconcile_stale_reservations(self, max_age_seconds: int = 60, conn: Optional[Any] = None) -> int:
        """
        Reconcile abandoned reservations from crashed workers or network timeouts.
        Stale reservations older than their expiration timestamp are conservatively
        transitioned to 'unknown' (treated as billable by upstream provider).
        Uses atomic state check on update to prevent race conditions across concurrent workers.
        """
        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()
        reconciled_count = 0

        close_after = False
        if conn is None:
            conn = db()
            close_after = True

        try:
            is_pg = bool(DATABASE_URL)
            sel_sql = (
                "SELECT reservation_id, provider_id, endpoint, caller_type, cost_units FROM api_reservations WHERE state = 'reserved' AND expires_at < %s"
                if is_pg else
                "SELECT reservation_id, provider_id, endpoint, caller_type, cost_units FROM api_reservations WHERE state = 'reserved' AND expires_at < ?"
            )
            cur = conn.execute(sel_sql, (now_iso,))
            stale_rows = [dict(r) for r in cur.fetchall()]

            for row in stale_rows:
                r_id = row["reservation_id"]
                prov_id = row["provider_id"]
                ep = row["endpoint"]
                caller = row["caller_type"]
                cost = row["cost_units"] or 1

                # Atomic transition: only proceed if state is STILL 'reserved'
                upd_sql = (
                    "UPDATE api_reservations SET state = 'unknown', updated_at = %s WHERE reservation_id = %s AND state = 'reserved'"
                    if is_pg else
                    "UPDATE api_reservations SET state = 'unknown', updated_at = ? WHERE reservation_id = ? AND state = 'reserved'"
                )
                cur_upd = conn.execute(upd_sql, (now_iso, r_id))
                updated = (getattr(cur_upd, "rowcount", 0) > 0)

                if updated:
                    ins_sql = (
                        """INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, error_category, cost, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
                        if is_pg else
                        """INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, error_category, cost, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
                    )
                    conn.execute(ins_sql, (prov_id, ep, caller, 0, 504, "abandoned_reservation_reconciled", cost, now_iso))
                    reconciled_count += 1

            if close_after:
                conn.commit()
        except Exception:
            if close_after:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if close_after:
                try:
                    conn.close()
                except Exception:
                    pass
        return reconciled_count

    def extend_reservation(self, reservation_id: Optional[str], additional_seconds: int = 60) -> bool:
        """
        Heartbeat extension for an active reservation lease.
        Prevents long-running in-flight requests from being prematurely reclaimed
        by reconcile_stale_reservations while they are actively executing.
        """
        if not reservation_id:
            return False
        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()
        exp_iso = datetime.fromtimestamp(now_utc.timestamp() + max(10, additional_seconds), tz=timezone.utc).isoformat()
        conn = db()
        try:
            is_pg = bool(DATABASE_URL)
            sql = (
                "UPDATE api_reservations SET expires_at = %s, updated_at = %s WHERE reservation_id = %s AND state = 'reserved'"
                if is_pg else
                "UPDATE api_reservations SET expires_at = ?, updated_at = ? WHERE reservation_id = ? AND state = 'reserved'"
            )
            cur = conn.execute(sql, (exp_iso, now_iso, reservation_id))
            updated = (getattr(cur, "rowcount", 0) > 0)
            conn.commit()
            return updated
        except Exception:
            return False
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _record_alert(
        self,
        alert_type: str,
        provider_id: str,
        severity: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        conn: Optional[Any] = None
    ) -> bool:
        """
        Record an operational alert with deduplication and cooldown.
        Redacts any sensitive secrets/keys from message and details.
        Returns True if a new alert was logged and dispatched, False if suppressed by cooldown.
        """
        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()
        cfg = get_guardian_config()
        cooldown = cfg.get("alert_cooldown_seconds", 3600)
        cooldown_cutoff = datetime.fromtimestamp(now_utc.timestamp() - cooldown, tz=timezone.utc).isoformat()

        clean_message = sanitize_flightapi_url(message)
        clean_details = sanitize_flightapi_url(json.dumps(details or {}))

        try:
            if conn is not None:
                is_pg = bool(DATABASE_URL)
                sel_sql = (
                    "SELECT id FROM api_alerts_log WHERE alert_type = %s AND provider_id = %s AND sent_at >= %s LIMIT 1"
                    if is_pg else
                    "SELECT id FROM api_alerts_log WHERE alert_type = ? AND provider_id = ? AND sent_at >= ? LIMIT 1"
                )
                cur = conn.execute(sel_sql, (alert_type, provider_id, cooldown_cutoff))
                rows = cur.fetchall()
            else:
                rows = self._execute_sql(
                    query_pg="SELECT id FROM api_alerts_log WHERE alert_type = %s AND provider_id = %s AND sent_at >= %s LIMIT 1",
                    query_sqlite="SELECT id FROM api_alerts_log WHERE alert_type = ? AND provider_id = ? AND sent_at >= ? LIMIT 1",
                    params=(alert_type, provider_id, cooldown_cutoff)
                )
            if rows:
                return False  # Suppressed by alert deduplication cooldown

            if conn is not None:
                ins_sql = (
                    "INSERT INTO api_alerts_log (alert_type, provider_id, severity, message, details_json, sent_at) VALUES (%s, %s, %s, %s, %s, %s)"
                    if is_pg else
                    "INSERT INTO api_alerts_log (alert_type, provider_id, severity, message, details_json, sent_at) VALUES (?, ?, ?, ?, ?, ?)"
                )
                conn.execute(ins_sql, (alert_type, provider_id, severity, clean_message, clean_details, now_iso))
            else:
                self._execute_sql(
                    query_pg="INSERT INTO api_alerts_log (alert_type, provider_id, severity, message, details_json, sent_at) VALUES (%s, %s, %s, %s, %s, %s)",
                    query_sqlite="INSERT INTO api_alerts_log (alert_type, provider_id, severity, message, details_json, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
                    params=(alert_type, provider_id, severity, clean_message, clean_details, now_iso)
                )

            # Optional email dispatch if configured
            admin_email = os.getenv("ADMIN_EMAIL") or os.getenv("ALERT_EMAIL")
            if admin_email and "@" in admin_email:
                try:
                    from services.email_service import send_email
                    subj = f"[SkyTracker Guardian {severity.upper()}] {provider_id.upper()}: {alert_type}"
                    body = f"SkyTracker API Quota Guardian Notification:\nProvider: {provider_id}\nSeverity: {severity}\nAlert: {alert_type}\nMessage: {clean_message}\nTimestamp: {now_iso}\nDetails: {clean_details}"
                    send_email(admin_email, subj, body)
                except Exception:
                    pass

            return True
        except Exception:
            return False

    def record_operator_audit(
        self,
        operator_id: str,
        action: str,
        provider_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Record audited operator adjustments and configuration actions with secret redaction."""
        now_iso = datetime.now(timezone.utc).isoformat()
        clean_details = sanitize_flightapi_url(json.dumps(details or {}))
        try:
            self._execute_sql(
                query_pg="INSERT INTO api_operator_audit (operator_id, action, provider_id, details_json, created_at) VALUES (%s, %s, %s, %s, %s)",
                query_sqlite="INSERT INTO api_operator_audit (operator_id, action, provider_id, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
                params=(operator_id, action, provider_id or "", clean_details, now_iso)
            )
        except Exception:
            pass
        except Exception:
            pass

    def check_budget(
        self,
        provider_id: str,
        caller_type: str = CALLER_USER_SEARCH,
        priority: int = PRIORITY_NORMAL,
        cost: int = 1,
        conn: Optional[Any] = None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Enforce minute, daily, monthly, and lifetime quotas across Gunicorn workers and cron jobs.
        Includes authoritative confirmed usage + active in-flight database reservations.
        Supports executing within an active locked database transaction (conn).
        """
        cfg = get_guardian_config()
        if provider_id == PROVIDER_SERPAPI:
            monthly_budget = cfg["serpapi_monthly_budget"]
            daily_budget = cfg["serpapi_daily_budget"]
            minute_budget = cfg["serpapi_minute_budget"]
            reserved_quota = cfg["serpapi_reserved_search_quota"]
            safety_margin = cfg["serpapi_safety_margin"]
            total_budget = None
        elif provider_id == PROVIDER_AMADEUS:
            monthly_budget = cfg["amadeus_monthly_budget"]
            daily_budget = cfg["amadeus_daily_budget"]
            minute_budget = cfg["amadeus_minute_budget"]
            reserved_quota = cfg["amadeus_reserved_search_quota"]
            safety_margin = cfg["amadeus_safety_margin"]
            total_budget = None
        elif provider_id == PROVIDER_FLIGHTAPI:
            monthly_budget = cfg["flightapi_monthly_budget"]
            daily_budget = cfg["flightapi_daily_budget"]
            minute_budget = cfg["flightapi_minute_budget"]
            reserved_quota = cfg["flightapi_reserved_search_quota"]
            safety_margin = cfg["flightapi_safety_margin"]
            total_budget = cfg.get("flightapi_total_budget", 30)
        else:
            return True, "Unmetered provider", {}

        now_utc = datetime.now(timezone.utc)
        now_ts = now_utc.timestamp()
        now_iso = now_utc.isoformat()
        minute_start = datetime.fromtimestamp(now_ts - 60, tz=timezone.utc).isoformat()
        day_start = now_utc.strftime("%Y-%m-%d 00:00:00")
        month_start = now_utc.strftime("%Y-%m-01 00:00:00")

        def _exec_query(query_pg: str, query_sqlite: str, params: tuple = ()) -> List[Any]:
            if conn is not None:
                sql = query_pg if DATABASE_URL else query_sqlite
                cur = conn.execute(sql, params)
                if query_pg.strip().upper().startswith("SELECT"):
                    return [dict(r) for r in cur.fetchall()]
                return []
            return self._execute_sql(query_pg, query_sqlite, params)

        # 1. Clean up expired reservations
        self.reconcile_stale_reservations(conn=conn)

        try:
            # 2. Check Circuit Breaker State
            c_state, c_cool = self.get_circuit_state(provider_id, conn=conn)
            if c_state in (STATE_OPEN, STATE_QUOTA_EXHAUSTED):
                c_rows = _exec_query(
                    query_pg="SELECT reason, cooldown_until FROM api_circuit_state WHERE provider_id = %s",
                    query_sqlite="SELECT reason, cooldown_until FROM api_circuit_state WHERE provider_id = ?",
                    params=(provider_id,)
                )
                reason_text = c_rows[0].get("reason") or "Quota Exhausted" if c_rows else "Quota Exhausted"
                cool_desc = f" until {c_cool}" if c_cool else " (reset time not supplied by provider)"
                return False, f"Provider {provider_id} quota is exhausted{cool_desc}: {reason_text}", {}

            # 3. Minute usage (Confirmed + Active In-Flight Reserved)
            m_rows = _exec_query(
                query_pg="SELECT COALESCE(SUM(cost), 0) as cnt FROM api_quota_logs WHERE provider_id = %s AND created_at >= %s AND cache_hit = 0",
                query_sqlite="SELECT COALESCE(SUM(cost), 0) as cnt FROM api_quota_logs WHERE provider_id = ? AND created_at >= ? AND cache_hit = 0",
                params=(provider_id, minute_start)
            )
            m_res_rows = _exec_query(
                query_pg="SELECT COALESCE(SUM(cost_units), 0) as cnt FROM api_reservations WHERE provider_id = %s AND state = 'reserved' AND created_at >= %s AND expires_at >= %s",
                query_sqlite="SELECT COALESCE(SUM(cost_units), 0) as cnt FROM api_reservations WHERE provider_id = ? AND state = 'reserved' AND created_at >= ? AND expires_at >= ?",
                params=(provider_id, minute_start, now_iso)
            )
            minute_count = (m_rows[0]["cnt"] if m_rows else 0) + (m_res_rows[0]["cnt"] if m_res_rows else 0)

            # 4. Daily usage (Confirmed + Active In-Flight Reserved)
            d_rows = _exec_query(
                query_pg="SELECT COALESCE(SUM(cost), 0) as cnt FROM api_quota_logs WHERE provider_id = %s AND created_at >= %s AND cache_hit = 0",
                query_sqlite="SELECT COALESCE(SUM(cost), 0) as cnt FROM api_quota_logs WHERE provider_id = ? AND created_at >= ? AND cache_hit = 0",
                params=(provider_id, day_start)
            )
            d_res_rows = _exec_query(
                query_pg="SELECT COALESCE(SUM(cost_units), 0) as cnt FROM api_reservations WHERE provider_id = %s AND state = 'reserved' AND created_at >= %s AND expires_at >= %s",
                query_sqlite="SELECT COALESCE(SUM(cost_units), 0) as cnt FROM api_reservations WHERE provider_id = ? AND state = 'reserved' AND created_at >= ? AND expires_at >= ?",
                params=(provider_id, day_start, now_iso)
            )
            daily_count = (d_rows[0]["cnt"] if d_rows else 0) + (d_res_rows[0]["cnt"] if d_res_rows else 0)

            # 5. Monthly usage (Confirmed + Active In-Flight Reserved)
            mo_rows = _exec_query(
                query_pg="SELECT COALESCE(SUM(cost), 0) as cnt FROM api_quota_logs WHERE provider_id = %s AND created_at >= %s AND cache_hit = 0",
                query_sqlite="SELECT COALESCE(SUM(cost), 0) as cnt FROM api_quota_logs WHERE provider_id = ? AND created_at >= ? AND cache_hit = 0",
                params=(provider_id, month_start)
            )
            mo_res_rows = _exec_query(
                query_pg="SELECT COALESCE(SUM(cost_units), 0) as cnt FROM api_reservations WHERE provider_id = %s AND state = 'reserved' AND created_at >= %s AND expires_at >= %s",
                query_sqlite="SELECT COALESCE(SUM(cost_units), 0) as cnt FROM api_reservations WHERE provider_id = ? AND state = 'reserved' AND created_at >= ? AND expires_at >= ?",
                params=(provider_id, month_start, now_iso)
            )
            monthly_count = (mo_rows[0]["cnt"] if mo_rows else 0) + (mo_res_rows[0]["cnt"] if mo_res_rows else 0)

            remaining_monthly = max(0, monthly_budget - monthly_count)
            remaining_daily = max(0, daily_budget - daily_count)
            stats = {
                "minute_count": minute_count,
                "minute_budget": minute_budget,
                "daily_count": daily_count,
                "daily_budget": daily_budget,
                "monthly_count": monthly_count,
                "monthly_budget": monthly_budget,
                "remaining_monthly": remaining_monthly,
                "remaining_daily": remaining_daily,
            }

            # 3. Enforce total trial budget (FlightAPI: 30 credits total, 5 credits reserve)
            if provider_id == PROVIDER_FLIGHTAPI and total_budget is not None:
                tot_rows = _exec_query(
                    query_pg="SELECT COALESCE(SUM(cost), 0) as cnt FROM api_quota_logs WHERE provider_id = %s AND cache_hit = 0",
                    query_sqlite="SELECT COALESCE(SUM(cost), 0) as cnt FROM api_quota_logs WHERE provider_id = ? AND cache_hit = 0",
                    params=(provider_id,)
                )
                tot_res_rows = _exec_query(
                    query_pg="SELECT COALESCE(SUM(cost_units), 0) as cnt FROM api_reservations WHERE provider_id = %s AND state = 'reserved' AND expires_at >= %s",
                    query_sqlite="SELECT COALESCE(SUM(cost_units), 0) as cnt FROM api_reservations WHERE provider_id = ? AND state = 'reserved' AND expires_at >= ?",
                    params=(provider_id, now_iso)
                )
                total_count = (tot_rows[0]["cnt"] if tot_rows else 0) + (tot_res_rows[0]["cnt"] if tot_res_rows else 0)
                stats["total_count"] = total_count
                stats["total_budget"] = total_budget
                stats["safety_reserve"] = safety_margin
                effective_ordinary_limit = max(0, total_budget - safety_margin)
                stats["effective_ordinary_limit"] = effective_ordinary_limit
                stats["remaining_lifetime_ordinary"] = max(0, effective_ordinary_limit - total_count)
                stats["remaining_lifetime_hard"] = max(0, total_budget - total_count)

                # Hard Lifetime Cap: even Priority 1 (Critical) cannot exceed total_budget (30)
                if total_count + cost > total_budget:
                    self._metrics["budget_rejections"] += 1
                    self._record_alert("budget_exhaustion", provider_id, "critical", f"FlightAPI hard lifetime credit budget ({total_count}/{total_budget}) exhausted. All calls blocked.", conn=conn)
                    return False, f"Provider {provider_id} hard lifetime credit budget exhausted ({total_count}/{total_budget})", stats

                # Ordinary traffic (Priority 2 Normal, Priority 3 Background):
                # CANNOT exceed 25 credits! 5-credit safety reserve strictly protected.
                if priority > PRIORITY_CRITICAL:
                    if total_count + cost > effective_ordinary_limit:
                        self._metrics["budget_rejections"] += 1
                        self._record_alert("budget_threshold", provider_id, "warning", f"FlightAPI ordinary credit ceiling reached ({total_count}/{effective_ordinary_limit}). Reserve of {safety_margin} credits protected.", conn=conn)
                        return False, f"Provider {provider_id} total trial credit budget exhausted ({total_count}/{total_budget} requests used with safety margin)", stats

            # 4. Enforce minute budget
            if minute_count + cost > minute_budget:
                self._metrics["budget_rejections"] += 1
                return False, f"Provider {provider_id} exceeded rate limit ({minute_count}/{minute_budget} req/min)", stats

            # 5. Enforce daily budget
            if daily_count + cost > daily_budget:
                self._metrics["budget_rejections"] += 1
                return False, f"Provider {provider_id} daily budget exhausted ({daily_count}/{daily_budget} req/day)", stats

            # 6. Enforce monthly budget with safety margin (Ordinary traffic capped with safety margin; Critical can access emergency reserve)
            effective_monthly_limit = max(0, monthly_budget - safety_margin) if priority > PRIORITY_CRITICAL else monthly_budget
            if monthly_count + cost > effective_monthly_limit:
                self._metrics["budget_rejections"] += 1
                return False, f"Provider {provider_id} monthly budget limit reached ({monthly_count}/{monthly_budget} req/month with safety margin)", stats

            # Priority reservation: block background checks if remaining quota drops to reserved quota
            remaining_lifetime_ordinary = stats.get("remaining_lifetime_ordinary", 9999)
            remaining_effective = min(remaining_monthly, remaining_daily, remaining_lifetime_ordinary)
            stats["remaining_effective"] = remaining_effective
            if (caller_type == CALLER_BACKGROUND_CHECK or priority == PRIORITY_BACKGROUND) and remaining_effective <= reserved_quota:
                self._metrics["budget_rejections"] += 1
                return False, f"Background check skipped: remaining quota ({remaining_effective}) is within reserved quota ({reserved_quota}) for live user searches", stats

            return True, "Budget available", stats
        except Exception as e:
            return False, f"Budget check failed on database error: {e}", {}

    def reserve_budget(
        self,
        provider_id: str,
        endpoint: str,
        caller_type: str = CALLER_USER_SEARCH,
        priority: int = PRIORITY_NORMAL,
        idempotency_key: Optional[str] = None
    ) -> Tuple[bool, str, Dict[str, Any], Optional[str]]:
        """
        Atomically reserve budget in database before executing an outbound API call.
        Enforces:
        - Unknown endpoint cost blocking
        - Concurrent budget protection across multiple Gunicorn workers and cron runs
          via PostgreSQL transaction-scoped advisory locks / SQLite BEGIN IMMEDIATE
        - Idempotency key deduplication
        - FlightAPI 30-credit hard limit and 5-credit safety reserve
        """
        cost = estimate_endpoint_cost(provider_id, endpoint)
        if cost is None:
            err_msg = (
                f"Unknown endpoint cost for provider '{provider_id}' endpoint '{endpoint}'. "
                f"Requests are strictly blocked until endpoint cost is verified or explicitly configured."
            )
            return False, err_msg, {}, None

        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()
        cfg = get_guardian_config()
        ttl_seconds = cfg.get("reservation_ttl_seconds", 60)
        exp_iso = datetime.fromtimestamp(now_utc.timestamp() + ttl_seconds, tz=timezone.utc).isoformat()
        res_id = f"res_{uuid.uuid4().hex}"

        with self._lock:
            conn = db()
            is_pg = bool(DATABASE_URL)
            try:
                # 1. Multi-process concurrency lock
                if is_pg:
                    conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext('guardian_quota_' || %s)::bigint)",
                        (provider_id,)
                    )
                else:
                    try:
                        conn.execute("BEGIN IMMEDIATE")
                    except Exception:
                        pass

                # 2. Idempotency check inside lock
                if idempotency_key:
                    cur = conn.execute(
                        "SELECT reservation_id, state FROM api_reservations WHERE idempotency_key = %s LIMIT 1"
                        if is_pg else
                        "SELECT reservation_id, state FROM api_reservations WHERE idempotency_key = ? LIMIT 1",
                        (idempotency_key,)
                    )
                    rows = [dict(r) for r in cur.fetchall()]
                    if rows:
                        existing = rows[0]
                        if existing["state"] in (STATE_RESERVED, STATE_SUCCEEDED):
                            conn.commit()
                            return True, "Idempotent reservation reused", {}, existing["reservation_id"]

                # 3. Check budget availability inside lock
                allowed, reason, stats = self.check_budget(
                    provider_id,
                    caller_type,
                    priority=priority,
                    cost=cost,
                    conn=conn
                )
                if not allowed:
                    conn.rollback()
                    return False, reason, stats, None

                # 4. Insert atomic reservation inside lock
                ins_sql = (
                    """
                    INSERT INTO api_reservations (reservation_id, provider_id, endpoint, caller_type, priority, cost_units, billing_unit, state, idempotency_key, created_at, updated_at, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    if is_pg else
                    """
                    INSERT INTO api_reservations (reservation_id, provider_id, endpoint, caller_type, priority, cost_units, billing_unit, state, idempotency_key, created_at, updated_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                )
                conn.execute(
                    ins_sql,
                    (res_id, provider_id, endpoint, caller_type, priority, cost, "credits", STATE_RESERVED, idempotency_key or "", now_iso, now_iso, exp_iso)
                )
                conn.commit()
                return True, "Budget reserved", stats, res_id
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                return False, f"Reservation failed on database error: {e}", {}, None
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def finalize_reservation(
        self,
        reservation_id: Optional[str],
        final_state: str,
        status_code: int = 200,
        error_category: Optional[str] = None
    ) -> None:
        """
        Finalize reservation state in database and record confirmed quota usage.
        - succeeded: cost logged as consumed
        - unknown (timeout/connection failure): cost logged as consumed (conservative)
        - failed / released (validation/auth/429): cost logged as 0 (budget released)
        Idempotent: Duplicate finalization is safely ignored.
        Late completion of reconciled reservations updates state without double-billing.
        """
        if not reservation_id:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        conn = db()
        is_pg = bool(DATABASE_URL)
        try:
            sel_sql = (
                "SELECT provider_id, endpoint, caller_type, cost_units, state FROM api_reservations WHERE reservation_id = %s"
                if is_pg else
                "SELECT provider_id, endpoint, caller_type, cost_units, state FROM api_reservations WHERE reservation_id = ?"
            )
            cur = conn.execute(sel_sql, (reservation_id,))
            rows = [dict(r) for r in cur.fetchall()]
            if not rows:
                return

            res = rows[0]
            current_state = res.get("state")

            # 1. Duplicate finalization guard: if already in a terminal state, NO-OP
            if current_state in (STATE_SUCCEEDED, STATE_FAILED, STATE_RELEASED):
                return

            prov_id = res["provider_id"]
            endpoint = res["endpoint"]
            caller = res["caller_type"]
            cost_units = res["cost_units"] or 1

            # 2. If already transitioned to 'unknown' (e.g. by reconcile_stale_reservations):
            # A cost was ALREADY charged to api_quota_logs during reconciliation!
            if current_state == STATE_UNKNOWN:
                if final_state == STATE_SUCCEEDED:
                    # Update status to succeeded, but DO NOT insert another billable log into api_quota_logs!
                    upd_sql = (
                        "UPDATE api_reservations SET state = %s, updated_at = %s WHERE reservation_id = %s"
                        if is_pg else
                        "UPDATE api_reservations SET state = ?, updated_at = ? WHERE reservation_id = ?"
                    )
                    conn.execute(upd_sql, (final_state, now_iso, reservation_id))
                    conn.commit()
                # For failed/released, conservative accounting dictates we do not refund after uncertain outcome.
                return

            # 3. Normal transition from 'reserved' to final_state
            cost_to_log = cost_units if final_state in (STATE_SUCCEEDED, STATE_UNKNOWN, STATE_RECONCILED) else 0

            upd_sql = (
                "UPDATE api_reservations SET state = %s, updated_at = %s WHERE reservation_id = %s AND state = 'reserved'"
                if is_pg else
                "UPDATE api_reservations SET state = ?, updated_at = ? WHERE reservation_id = ? AND state = 'reserved'"
            )
            cur_upd = conn.execute(upd_sql, (final_state, now_iso, reservation_id))
            if getattr(cur_upd, "rowcount", 0) > 0:
                ins_sql = (
                    """
                    INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, error_category, cost, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    if is_pg else
                    """
                    INSERT INTO api_quota_logs (provider_id, endpoint, caller_type, cache_hit, status_code, error_category, cost, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """
                )
                conn.execute(ins_sql, (prov_id, endpoint, caller, 0, status_code, sanitize_flightapi_url(error_category or "")[:120], cost_to_log, now_iso))
                conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def release_reservation(self, reservation_id: Optional[str]) -> None:
        """Safely release an unspent budget reservation."""
        self.finalize_reservation(reservation_id, STATE_RELEASED, status_code=200, error_category="released")

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
        except Exception:
            pass

    # ------------------------------------------------------------
    # 5. CIRCUIT BREAKER WITH RECOVERY STATE MACHINE & RESET POLICY
    # ------------------------------------------------------------
    def resolve_quota_reset_time(self, provider_id: str, retry_after: Optional[int] = None) -> Tuple[Optional[str], str]:
        """
        Determine provider recovery/reset time according to:
        1. Explicit provider-supplied header (Retry-After)
        2. Configured operator recovery policy (SERPAPI_RESET_POLICY, SERPAPI_BILLING_CYCLE_DAY, SERPAPI_RESET_TIMESTAMP, SERPAPI_RESET_INTERVAL_SECONDS)
        3. If neither provider supplied nor policy configured: DO NOT GUESS. Returns (None, "unspecified_not_guessed")
        """
        now_utc = datetime.now(timezone.utc)
        now_ts = now_utc.timestamp()

        # 1. Provider-supplied Retry-After seconds
        if retry_after is not None and retry_after > 0:
            dt = datetime.fromtimestamp(now_ts + retry_after, tz=timezone.utc)
            return dt.isoformat(), "provider_retry_after"

        prefix = provider_id.upper().replace("-", "_")

        # 2. Configured Recovery / Reset Policy
        policy = (
            os.getenv(f"{prefix}_RESET_POLICY")
            or os.getenv("SERPAPI_RESET_POLICY")
            or ""
        ).lower().strip()

        # A. Explicit Fixed ISO Timestamp
        reset_ts_env = os.getenv(f"{prefix}_RESET_TIMESTAMP") or os.getenv("SERPAPI_RESET_TIMESTAMP")
        if (policy == "fixed_timestamp" or reset_ts_env) and policy != "none":
            if reset_ts_env:
                try:
                    dt = datetime.fromisoformat(reset_ts_env)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.isoformat(), "configured_timestamp"
                except Exception:
                    pass

        # B. Explicit Billing Cycle Day of Month (1 - 31)
        cycle_day_env = os.getenv(f"{prefix}_BILLING_CYCLE_DAY") or os.getenv("SERPAPI_BILLING_CYCLE_DAY")
        if policy in ("monthly_cycle_day", "billing_cycle") or cycle_day_env:
            try:
                cycle_day = max(1, min(31, int(cycle_day_env))) if cycle_day_env else 1
                if now_utc.day < cycle_day:
                    try:
                        next_dt = now_utc.replace(day=cycle_day, hour=0, minute=0, second=0, microsecond=0)
                    except ValueError:
                        next_dt = datetime(now_utc.year, now_utc.month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)
                else:
                    next_month = now_utc.month + 1 if now_utc.month < 12 else 1
                    next_year = now_utc.year if now_utc.month < 12 else now_utc.year + 1
                    try:
                        next_dt = datetime(next_year, next_month, cycle_day, 0, 0, 0, tzinfo=timezone.utc)
                    except ValueError:
                        next_dt = datetime(next_year, next_month, 1, 0, 0, 0, tzinfo=timezone.utc)
                return next_dt.isoformat(), "configured_billing_cycle"
            except Exception:
                pass

        # C. Explicit Fixed Interval (in seconds)
        interval_env = os.getenv(f"{prefix}_RESET_INTERVAL_SECONDS") or os.getenv("SERPAPI_RESET_INTERVAL_SECONDS")
        if policy == "interval" or interval_env:
            if interval_env:
                try:
                    secs = int(interval_env)
                    if secs > 0:
                        dt = datetime.fromtimestamp(now_ts + secs, tz=timezone.utc)
                        return dt.isoformat(), "configured_interval"
                except Exception:
                    pass

        # 3. DO NOT GUESS if provider did not supply it and no policy was configured!
        return None, "unspecified_not_guessed"

    def get_circuit_state(self, provider_id: str, conn: Optional[Any] = None) -> Tuple[str, Optional[str]]:
        """
        Check circuit status. Transitions OPEN/QUOTA_EXHAUSTED -> HALF_OPEN when cooldown expires.
        If no reset time was supplied or configured, DO NOT GUESS — provider stays blocked.
        Returns: (state: str, cooldown_until: Optional[str])
        """
        try:
            if conn is not None:
                is_pg = bool(DATABASE_URL)
                sql = (
                    "SELECT state, cooldown_until, failure_count FROM api_circuit_state WHERE provider_id = %s"
                    if is_pg else
                    "SELECT state, cooldown_until, failure_count FROM api_circuit_state WHERE provider_id = ?"
                )
                cur = conn.execute(sql, (provider_id,))
                rows = [dict(r) for r in cur.fetchall()]
            else:
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

            if state in (STATE_OPEN, STATE_QUOTA_EXHAUSTED):
                if not cooldown_str:
                    # Reset time not supplied and not configured: do NOT guess, remain blocked
                    return state, None

                now_utc = datetime.now(timezone.utc)
                cool_dt = datetime.fromisoformat(cooldown_str)
                if cool_dt.tzinfo is None:
                    cool_dt = cool_dt.replace(tzinfo=timezone.utc)

                if now_utc.timestamp() >= cool_dt.timestamp():
                    # Cooldown expired: allow controlled probe
                    self._update_circuit_state(provider_id, STATE_HALF_OPEN, failure_count=0, cooldown_until=None, conn=conn)
                    return STATE_HALF_OPEN, None
                else:
                    return state, cooldown_str

            return state, cooldown_str
        except Exception:
            return STATE_CLOSED, None

    def get_provider_quota_status(self, provider_id: str) -> Dict[str, Any]:
        """Return comprehensive operational and quota state for a provider."""
        state, cool = self.get_circuit_state(provider_id)
        rows = self._execute_sql(
            query_pg="SELECT failure_count, last_failure_at, opened_at, reason FROM api_circuit_state WHERE provider_id = %s",
            query_sqlite="SELECT failure_count, last_failure_at, opened_at, reason FROM api_circuit_state WHERE provider_id = ?",
            params=(provider_id,)
        )
        row = rows[0] if rows else {}
        return {
            "provider_id": provider_id,
            "state": state,
            "is_quota_exhausted": state in (STATE_OPEN, STATE_QUOTA_EXHAUSTED),
            "cooldown_until": cool,
            "reset_time_supplied": cool is not None,
            "reason": row.get("reason", ""),
            "last_failure_at": row.get("last_failure_at"),
            "failure_count": row.get("failure_count", 0)
        }

    def reset_provider_quota(self, provider_id: str, reason: str = "Quota Restored / Manual Reset") -> None:
        """Reset provider circuit and quota state back to operational CLOSED."""
        self._update_circuit_state(provider_id, STATE_CLOSED, failure_count=0, cooldown_until=None, reason=reason)

    def _update_circuit_state(
        self,
        provider_id: str,
        state: str,
        failure_count: int = 0,
        cooldown_until: Optional[str] = None,
        reason: Optional[str] = None,
        conn: Optional[Any] = None
    ) -> None:
        """Persist circuit state transition into shared database."""
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            if conn is not None:
                is_pg = bool(DATABASE_URL)
                sql = (
                    """
                    INSERT INTO api_circuit_state (provider_id, state, failure_count, last_failure_at, opened_at, cooldown_until, reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (provider_id) DO UPDATE
                    SET state = EXCLUDED.state,
                        failure_count = EXCLUDED.failure_count,
                        last_failure_at = EXCLUDED.last_failure_at,
                        opened_at = CASE WHEN EXCLUDED.state IN ('OPEN', 'QUOTA_EXHAUSTED') THEN EXCLUDED.opened_at ELSE api_circuit_state.opened_at END,
                        cooldown_until = EXCLUDED.cooldown_until,
                        reason = EXCLUDED.reason
                    """
                    if is_pg else
                    """
                    INSERT OR REPLACE INTO api_circuit_state (provider_id, state, failure_count, last_failure_at, opened_at, cooldown_until, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """
                )
                conn.execute(sql, (provider_id, state, failure_count, now_iso, now_iso if state in (STATE_OPEN, STATE_QUOTA_EXHAUSTED) else None, cooldown_until, reason or ""))
            else:
                self._execute_sql(
                    query_pg="""
                        INSERT INTO api_circuit_state (provider_id, state, failure_count, last_failure_at, opened_at, cooldown_until, reason)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (provider_id) DO UPDATE
                        SET state = EXCLUDED.state,
                            failure_count = EXCLUDED.failure_count,
                            last_failure_at = EXCLUDED.last_failure_at,
                            opened_at = CASE WHEN EXCLUDED.state IN ('OPEN', 'QUOTA_EXHAUSTED') THEN EXCLUDED.opened_at ELSE api_circuit_state.opened_at END,
                            cooldown_until = EXCLUDED.cooldown_until,
                            reason = EXCLUDED.reason
                    """,
                    query_sqlite="""
                        INSERT OR REPLACE INTO api_circuit_state (provider_id, state, failure_count, last_failure_at, opened_at, cooldown_until, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    params=(provider_id, state, failure_count, now_iso, now_iso if state in (STATE_OPEN, STATE_QUOTA_EXHAUSTED) else None, cooldown_until, reason or "")
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
        - 429 / Quota exhaustion: Immediate OPEN/QUOTA_EXHAUSTED without guessing reset time
        - 401 / 403: Immediate OPEN (auth/config failure)
        - 5xx / Timeouts: Increment failure counter; trip if threshold reached
        """
        cfg = get_guardian_config()
        now_utc = datetime.now(timezone.utc)
        now_ts = now_utc.timestamp()

        # 1. Quota Exhaustion / 429
        if is_quota_exhausted_error(status_code, error_msg):
            cooldown_until, policy_used = self.resolve_quota_reset_time(provider_id, retry_after=retry_after)
            reason = f"Provider Quota Exhausted: {error_msg[:120]} [Policy: {policy_used}]"
            self._update_circuit_state(
                provider_id,
                STATE_OPEN,
                failure_count=1,
                cooldown_until=cooldown_until,
                reason=reason
            )
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
        force_refresh: bool = False,
        priority: int = PRIORITY_NORMAL,
        idempotency_key: Optional[str] = None
    ) -> GuardianResponse:
        """
        Execute request through the Quota Guardian.
        Enforces caching, request coalescing, circuit breaker, atomic budget reservation, and bounded retries.
        """
        cfg = get_guardian_config()
        ttl = cache_ttl or cfg["cache_ttl_search"]
        cache_key = self.build_cache_key(provider_id, endpoint, params)

        # 1. Check cache (unless explicit refresh)
        if not force_refresh:
            cached_resp = self.get_cached(cache_key)
            if cached_resp:
                self.log_quota_request(provider_id, endpoint, caller_type, cache_hit=True, status_code=200, cost=0)
                return cached_resp

        # 2. Check Circuit Breaker & Quota State
        c_state, cooldown_until = self.get_circuit_state(provider_id)
        if c_state in (STATE_OPEN, STATE_QUOTA_EXHAUSTED):
            self._metrics["circuit_rejections"] += 1
            cool_desc = f" until {cooldown_until}" if cooldown_until else " (reset time not supplied by provider)"
            err_msg = f"Provider {provider_id} circuit breaker is OPEN ({c_state}){cool_desc}. Upstream calls blocked."
            return GuardianResponse(
                data=None,
                is_cached=False,
                is_live=False,
                source=provider_id,
                status="circuit_open",
                error=sanitize_flightapi_url(err_msg),
                provider_id=provider_id
            )

        # 3. Request Coalescing (Single-Flight Deduplication)
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

        # 4. Atomic Pre-Flight Budget Check & Reservation
        allowed, reason, stats, res_id = self.reserve_budget(
            provider_id,
            endpoint,
            caller_type=caller_type,
            priority=priority,
            idempotency_key=idempotency_key
        )
        if not allowed:
            # Clean up in-flight event so waiters don't hang
            with self._lock:
                if cache_key in self._in_flight:
                    self._in_flight[cache_key].set()
                    del self._in_flight[cache_key]

            self.log_quota_request(provider_id, endpoint, caller_type, cache_hit=False, status_code=429, error_category="budget_exhausted", cost=0)
            return GuardianResponse(
                data=None,
                is_cached=False,
                is_live=False,
                source=provider_id,
                status="blocked_budget",
                error=sanitize_flightapi_url(f"Request blocked by API Quota Guardian: {reason}"),
                provider_id=provider_id,
                meta=stats
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
                    self.finalize_reservation(res_id, STATE_SUCCEEDED, status_code=200)

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
                    err_str = sanitize_flightapi_url(str(exc))

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
                    if status_code in (400, 401, 403, 422, 429) or is_quota_exhausted_error(status_code, err_str):
                        break

                    # Transient retry with backoff and jitter
                    if attempts <= max_retries:
                        backoff = 0.05 * (2 ** (attempts - 1)) + random.uniform(0.005, 0.02)
                        time.sleep(backoff)

            # All attempts exhausted
            err_msg = sanitize_flightapi_url(str(last_error or "Unknown provider failure"))
            is_timeout = (
                "timeout" in err_msg.lower()
                or "timed out" in err_msg.lower()
                or "timeouterror" in err_msg.lower()
                or last_status in (504, 408)
            )

            # Conservative accounting on billing and provider responses:
            # - Timeouts: finalized as STATE_UNKNOWN (status_code 504, timeout_conservative_accounting, cost=1)
            # - Deterministic client rejections (400, 401, 403, 422, 429): finalized as STATE_FAILED (cost=0)
            # - All other 5xx, socket resets, connection drops, and unknown outcomes: finalized as STATE_UNKNOWN (cost charged conservatively)
            if is_timeout:
                self.finalize_reservation(res_id, STATE_UNKNOWN, status_code=504, error_category="timeout_conservative_accounting")
            elif last_status in (400, 401, 403, 422, 429) or is_quota_exhausted_error(last_status, err_msg):
                self.finalize_reservation(res_id, STATE_FAILED, status_code=last_status, error_category=err_msg[:60])
            else:
                self.finalize_reservation(res_id, STATE_UNKNOWN, status_code=last_status or 500, error_category=f"server_or_network_error_{last_status}"[:60])

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
        now_iso = now_utc.isoformat()
        day_start = now_utc.strftime("%Y-%m-%d 00:00:00")
        month_start = now_utc.strftime("%Y-%m-01 00:00:00")
        cfg = get_guardian_config()

        # Circuit states
        circuits: Dict[str, Any] = {}
        for pid in [PROVIDER_SERPAPI, PROVIDER_AMADEUS, PROVIDER_FLIGHTAPI]:
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
        flightapi_lifetime_spent = 0
        flightapi_active_reservations = 0
        recent_alerts: List[Dict[str, Any]] = []
        recent_audits: List[Dict[str, Any]] = []

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

            # FlightAPI lifetime confirmed cost
            fa_tot_rows = self._execute_sql(
                query_pg="SELECT COALESCE(SUM(cost), 0) as cnt FROM api_quota_logs WHERE provider_id = 'flightapi' AND cache_hit = 0",
                query_sqlite="SELECT COALESCE(SUM(cost), 0) as cnt FROM api_quota_logs WHERE provider_id = 'flightapi' AND cache_hit = 0"
            )
            if fa_tot_rows:
                flightapi_lifetime_spent = fa_tot_rows[0]["cnt"] or 0

            # FlightAPI active in-flight reservations
            fa_res_rows = self._execute_sql(
                query_pg="SELECT COALESCE(SUM(cost_units), 0) as cnt FROM api_reservations WHERE provider_id = 'flightapi' AND state = 'reserved' AND expires_at >= %s",
                query_sqlite="SELECT COALESCE(SUM(cost_units), 0) as cnt FROM api_reservations WHERE provider_id = 'flightapi' AND state = 'reserved' AND expires_at >= ?",
                params=(now_iso,)
            )
            if fa_res_rows:
                flightapi_active_reservations = fa_res_rows[0]["cnt"] or 0

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

            # Recent alerts
            recent_alerts = self._execute_sql(
                query_pg="SELECT id, alert_type, provider_id, severity, message, sent_at FROM api_alerts_log ORDER BY id DESC LIMIT 5",
                query_sqlite="SELECT id, alert_type, provider_id, severity, message, sent_at FROM api_alerts_log ORDER BY id DESC LIMIT 5"
            )

            # Recent operator audits
            recent_audits = self._execute_sql(
                query_pg="SELECT id, operator_id, action, provider_id, details_json, created_at FROM api_operator_audit ORDER BY id DESC LIMIT 5",
                query_sqlite="SELECT id, operator_id, action, provider_id, details_json, created_at FROM api_operator_audit ORDER BY id DESC LIMIT 5"
            )

        except Exception:
            pass

        cache_rate_pct = 0.0
        if total_today > 0:
            cache_rate_pct = round((cache_hits_today / total_today) * 100, 1)

        remaining_serp = max(0, cfg["serpapi_monthly_budget"] - total_month)

        # FlightAPI trial calculations
        fa_total_budget = cfg.get("flightapi_total_budget", 30)
        fa_safety_margin = cfg.get("flightapi_safety_margin", 5)
        fa_ordinary_limit = max(0, fa_total_budget - fa_safety_margin)
        fa_allocated = flightapi_lifetime_spent + flightapi_active_reservations
        fa_remaining_ordinary = max(0, fa_ordinary_limit - fa_allocated)
        fa_remaining_hard = max(0, fa_total_budget - fa_allocated)

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
                },
                "flightapi": {
                    "lifetime_trial_budget": fa_total_budget,
                    "ordinary_traffic_ceiling": fa_ordinary_limit,
                    "safety_reserve": fa_safety_margin,
                    "confirmed_spent": flightapi_lifetime_spent,
                    "active_reservations": flightapi_active_reservations,
                    "total_allocated": fa_allocated,
                    "remaining_ordinary_budget": fa_remaining_ordinary,
                    "remaining_hard_budget": fa_remaining_hard,
                    "actual_provider_quota": "unknown_from_provider",
                    "monthly_budget": cfg["flightapi_monthly_budget"],
                    "daily_budget": cfg["flightapi_daily_budget"],
                    "minute_budget": cfg["flightapi_minute_budget"],
                    "reserved_search_quota": cfg["flightapi_reserved_search_quota"],
                    "budget_type": "configured_trial_with_hard_reserve"
                }
            },
            "circuits": circuits,
            "last_successful_request": last_success,
            "last_quota_exhaustion_event": last_exhaustion,
            "background_check_activity": bg_activity,
            "recent_alerts": recent_alerts,
            "recent_operator_audits": recent_audits,
            "cache_ttls": {
                "search_ttl_seconds": cfg["cache_ttl_search"],
                "checker_ttl_seconds": cfg["cache_ttl_checker"],
                "ttl_type": "configured"
            }
        }

    def get_ai_usage_analysis(self, days: int = 7) -> Dict[str, Any]:
        """Generate AI and heuristic usage analysis and optimization recommendations."""
        analyzer = UsageAnalysisAgent(self)
        return analyzer.analyze_usage(days=days)

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
            self._execute_sql(
                query_pg="DELETE FROM api_reservations",
                query_sqlite="DELETE FROM api_reservations"
            )
            self._execute_sql(
                query_pg="DELETE FROM api_alerts_log",
                query_sqlite="DELETE FROM api_alerts_log"
            )
            self._execute_sql(
                query_pg="DELETE FROM api_operator_audit",
                query_sqlite="DELETE FROM api_operator_audit"
            )
        except Exception:
            pass


# ============================================================
# 9. AI USAGE ANALYSIS AGENT (ADVISORY & SAFETY PRESERVING)
# ============================================================
class UsageAnalysisAgent:
    """
    Intelligent Usage Analysis and Reliability Advisor.
    Examines historical access patterns, detects anomalies, and generates safe
    operational recommendations for operator review.

    Safety Guarantees:
    - Strictly advisory; NEVER autonomously increases quotas, spends money, or overrides safety controls.
    - Zero external paid AI API dependency (uses local deterministic heuristics by default).
    - Never exports API keys, passwords, or customer PII.
    """

    def __init__(self, guardian_instance: ApiGuardian):
        self.guardian = guardian_instance

    def analyze_usage(self, days: int = 7) -> Dict[str, Any]:
        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()
        cutoff_dt = datetime.fromtimestamp(now_utc.timestamp() - (days * 86400), tz=timezone.utc)
        cutoff_iso = cutoff_dt.isoformat()

        cfg = get_guardian_config()
        is_ai_enabled = cfg.get("enable_ai_analysis", False)

        total_requests = 0
        cache_hits = 0
        errors_count = 0
        total_cost_consumed = 0
        provider_stats: Dict[str, Dict[str, Any]] = {}
        caller_stats: Dict[str, int] = {}
        blocked_reasons: Dict[str, int] = {}
        anomalies: List[Dict[str, Any]] = []
        recommendations: List[Dict[str, Any]] = []

        try:
            # Query log aggregates
            rows = self.guardian._execute_sql(
                query_pg="""
                    SELECT provider_id, endpoint, caller_type, cache_hit, status_code, error_category, cost
                    FROM api_quota_logs
                    WHERE created_at >= %s
                """,
                query_sqlite="""
                    SELECT provider_id, endpoint, caller_type, cache_hit, status_code, error_category, cost
                    FROM api_quota_logs
                    WHERE created_at >= ?
                """,
                params=(cutoff_iso,)
            )

            for r in rows:
                total_requests += 1
                prov = r.get("provider_id") or "unknown"
                caller = r.get("caller_type") or "unknown"
                is_hit = bool(r.get("cache_hit"))
                code = r.get("status_code") or 200
                cost = r.get("cost") or 0
                err_cat = r.get("error_category") or ""

                if is_hit:
                    cache_hits += 1
                if code >= 400:
                    errors_count += 1
                total_cost_consumed += cost

                # Provider breakdown
                if prov not in provider_stats:
                    provider_stats[prov] = {"requests": 0, "cache_hits": 0, "errors": 0, "cost": 0}
                provider_stats[prov]["requests"] += 1
                if is_hit:
                    provider_stats[prov]["cache_hits"] += 1
                if code >= 400:
                    provider_stats[prov]["errors"] += 1
                provider_stats[prov]["cost"] += cost

                # Caller breakdown
                caller_stats[caller] = caller_stats.get(caller, 0) + 1

                # Blocked reasons
                if code == 429 or "exhaust" in err_cat.lower() or "limit" in err_cat.lower():
                    reason_key = err_cat[:60] if err_cat else "Rate Limit / Quota Exhausted"
                    blocked_reasons[reason_key] = blocked_reasons.get(reason_key, 0) + 1

        except Exception:
            pass

        # Calculate overall metrics
        hit_rate = round((cache_hits / total_requests * 100), 1) if total_requests > 0 else 0.0
        error_rate = round((errors_count / total_requests * 100), 1) if total_requests > 0 else 0.0

        # Anomaly Detection
        if error_rate > 20.0 and total_requests >= 5:
            anomalies.append({
                "type": "high_error_rate",
                "severity": "high",
                "description": f"Elevated error rate ({error_rate}%) across providers during the past {days} days."
            })

        fa_stats = provider_stats.get(PROVIDER_FLIGHTAPI, {})
        fa_cost = fa_stats.get("cost", 0)
        fa_total = cfg.get("flightapi_total_budget", 30)
        fa_reserve = cfg.get("flightapi_safety_margin", 5)
        if fa_cost >= (fa_total - fa_reserve):
            anomalies.append({
                "type": "flightapi_near_exhaustion",
                "severity": "critical",
                "description": f"FlightAPI trial quota has reached {fa_cost}/{fa_total} credits. The {fa_reserve}-credit safety reserve is now actively protecting critical traffic."
            })

        bg_calls = caller_stats.get(CALLER_BACKGROUND_CHECK, 0)
        if total_requests > 0 and (bg_calls / total_requests) > 0.6:
            anomalies.append({
                "type": "excessive_background_traffic",
                "severity": "medium",
                "description": f"Background price checks account for {round(bg_calls/total_requests*100)}% of all API calls, consuming scarce quota."
            })

        # Advisory Optimization Recommendations
        rec_id = 1
        if hit_rate < 30.0 and total_requests >= 10:
            recommendations.append({
                "id": f"REC-{rec_id:03d}",
                "category": "cache_optimization",
                "title": "Increase Cache TTL for Flight Searches",
                "rationale": f"Current cache hit rate is low ({hit_rate}%). Extending cache TTL from {cfg['cache_ttl_search']}s to 1800s will reduce outbound API usage on recurring searches.",
                "action": "Set CACHE_TTL_SEARCH=1800 in environment configuration.",
                "requires_operator_approval": True,
                "autonomous_execution": False
            })
            rec_id += 1

        if bg_calls > 15:
            recommendations.append({
                "id": f"REC-{rec_id:03d}",
                "category": "polling_interval",
                "title": "Lengthen Background Checking Intervals",
                "rationale": "High frequency of background tracking requests is depleting provider budget.",
                "action": "Increase cron interval or decrease BACKGROUND_CHECK_MAX_CALLS_PER_RUN.",
                "requires_operator_approval": True,
                "autonomous_execution": False
            })
            rec_id += 1

        if fa_cost >= 20:
            recommendations.append({
                "id": f"REC-{rec_id:03d}",
                "category": "provider_allocation",
                "title": "Enable Alternate Provider for Background Checks",
                "rationale": f"FlightAPI trial credits ({fa_cost}/{fa_total}) are limited. Configure SerpAPI or Amadeus to absorb non-critical background checks.",
                "action": "Verify credentials for SerpAPI or Amadeus and set as secondary provider.",
                "requires_operator_approval": True,
                "autonomous_execution": False
            })
            rec_id += 1

        return {
            "status": "success",
            "analysis_timestamp": now_iso,
            "period_days": days,
            "ai_analysis_enabled": is_ai_enabled,
            "metrics_summary": {
                "total_requests": total_requests,
                "cache_hits": cache_hits,
                "cache_hit_rate_pct": hit_rate,
                "total_errors": errors_count,
                "error_rate_pct": error_rate,
                "total_credits_consumed": total_cost_consumed
            },
            "provider_breakdown": provider_stats,
            "caller_distribution": caller_stats,
            "blocked_reasons_summary": blocked_reasons,
            "anomalies_detected": anomalies,
            "recommendations": recommendations,
            "safety_policy": {
                "autonomous_budget_adjustment": False,
                "operator_approval_required": True,
                "enforcement": "Deterministic Guardian limits take absolute precedence over AI recommendations."
            }
        }


# ============================================================
# GLOBAL GUARDIAN SINGLETON INSTANCE
# ============================================================
guardian = ApiGuardian()
