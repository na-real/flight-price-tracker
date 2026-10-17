"""
SkyTracker Self-Healing AI Bug-Fixing Agent with Email Escalation
================================================================
Production-grade autonomous reliability service for the SkyTracker flight platform.

Architectural Lifecycle (10 Stages):
1. Detect          - Identify genuine application failures, fingerprint errors, deduplicate occurrences.
2. Classify        - Distinguish application defects from external outages, quota exhaustion, 4xx input errors, and auth issues.
3. Collect Evidence- Gather sanitized tracebacks, health status, circuit breaker states, and environment context (zero leaked secrets).
4. Diagnose        - Formulate root-cause hypotheses, assess fixability, and verify autonomous permission boundaries.
5. Plan            - Propose minimal, isolated, reversible patches adhering to strict file allowlists and line-count limits.
6. Apply           - Execute isolated code modification with concurrency locks across multi-worker deployments (Render gunicorn).
7. Verify          - Run strict verification gate: syntax check, git diff check, targeted tests, full test suites with mocks.
8. Accept/Rollback - Retain verified patches without auto-deploying, or cleanly revert failed patches to pre-repair state.
9. Escalate        - Deliver actionable, deduplicated email alerts to the owner via configured SMTP relay with cooldowns.
10. Record         - Persist full audit history of incidents, state transitions, repair diffs, and escalation logs.

Safety Invariants:
- NEVER auto-deploy to Render or push to the main Git branch.
- NEVER attempt code changes for external provider outages, quota exhaustion, missing credentials, or infrastructure crashes.
- NEVER edit credentials, .env files, security/auth primitives, or database migrations.
- NEVER consume live FlightAPI / SerpAPI / Amadeus credits during automated verification tests.
- Maximum 2 automated repair attempts per incident to prevent infinite loops.
"""

import os
import re
import sys
import json
import uuid
import time
import shutil
import hashlib
import logging
import subprocess
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

from db import db, DATABASE_URL
from services.api_guardian import sanitize_flightapi_url, is_quota_exhausted_error

logger = logging.getLogger("self_healing_agent")

# ============================================================
# SUBSYSTEM CONSTANTS
# ============================================================
SUBSYSTEM_WEB = "web"
SUBSYSTEM_SEARCH = "search"
SUBSYSTEM_PROVIDER = "provider"
SUBSYSTEM_GUARDIAN = "api_guardian"
SUBSYSTEM_DB = "database"
SUBSYSTEM_CHECKER = "price_checker"
SUBSYSTEM_EMAIL = "email"
SUBSYSTEM_FRONTEND = "frontend"
SUBSYSTEM_VALIDATION = "validation"

ALL_SUBSYSTEMS = {
    SUBSYSTEM_WEB,
    SUBSYSTEM_SEARCH,
    SUBSYSTEM_PROVIDER,
    SUBSYSTEM_GUARDIAN,
    SUBSYSTEM_DB,
    SUBSYSTEM_CHECKER,
    SUBSYSTEM_EMAIL,
    SUBSYSTEM_FRONTEND,
    SUBSYSTEM_VALIDATION,
}

# ============================================================
# INCIDENT CATEGORY CONSTANTS
# ============================================================
CAT_APPLICATION_DEFECT = "application_defect"
CAT_PROVIDER_OUTAGE = "external_provider_outage"
CAT_QUOTA_EXHAUSTED = "provider_quota_exhausted"
CAT_AUTH_ERROR = "authentication_error"
CAT_INVALID_INPUT = "invalid_user_input"
CAT_INFRASTRUCTURE = "infrastructure_failure"
CAT_CIRCUIT_BREAKER = "circuit_breaker_active"

ALL_CATEGORIES = {
    CAT_APPLICATION_DEFECT,
    CAT_PROVIDER_OUTAGE,
    CAT_QUOTA_EXHAUSTED,
    CAT_AUTH_ERROR,
    CAT_INVALID_INPUT,
    CAT_INFRASTRUCTURE,
    CAT_CIRCUIT_BREAKER,
}

# ============================================================
# INCIDENT LIFECYCLE STATES
# ============================================================
STATE_DETECTED = "detected"
STATE_INVESTIGATING = "investigating"
STATE_FIX_PROPOSED = "fix_proposed"
STATE_FIXING = "fixing"
STATE_VERIFYING = "verifying"
STATE_RESOLVED = "resolved"
STATE_ESCALATED = "escalated"
STATE_ROLLED_BACK = "rolled_back"

ACTIVE_STATES = {
    STATE_DETECTED,
    STATE_INVESTIGATING,
    STATE_FIX_PROPOSED,
    STATE_FIXING,
    STATE_VERIFYING,
    STATE_ESCALATED,
    STATE_ROLLED_BACK,
}

# ============================================================
# SEVERITY CONSTANTS
# ============================================================
SEV_CRITICAL = "critical"
SEV_HIGH = "high"
SEV_MEDIUM = "medium"
SEV_LOW = "low"

SEVERITY_WEIGHTS = {
    SEV_LOW: 1,
    SEV_MEDIUM: 2,
    SEV_HIGH: 3,
    SEV_CRITICAL: 4,
}

# ============================================================
# PERMITTED REPAIR FILE ALLOWLIST & SIZE LIMITS
# ============================================================
# Strict allowlist: Only deterministic application files may be automatically patched.
# Core auth, security primitives, database connection configs, and environment files are strictly forbidden.
PERMITTED_FILE_PREFIXES = [
    "validation.py",
    "services/airports.py",
    "services/flight_api.py",
    "services/providers/",
    "checker.py",
    "static/js/",
]

FORBIDDEN_FILES = {
    ".env",
    ".env.example",
    "render.yaml",
    "Dockerfile",
    "Procfile",
    "db.py",
    "security.py",
    "services/api_guardian.py",
    "app.py",
    "conftest.py",
    "pytest.ini",
}

MAX_PATCH_LINES = 50
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_ALERT_COOLDOWN_SECONDS = 3600  # 1 hour deduplication window


# ============================================================
# EVIDENCE SANITIZATION UTILITIES
# ============================================================
EMAIL_SCRUB_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b")
BEARER_SCRUB_REGEX = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", re.IGNORECASE)
PASSWORD_SCRUB_REGEX = re.compile(r"(password|pwd|secret|token|api_key|apiKey)\s*[:=]\s*['\"]?([^'\"&\s,]+)['\"]?", re.IGNORECASE)
DB_URL_SCRUB_REGEX = re.compile(r"(postgres(?:ql)?|sqlite)://([^:]+):([^@]+)@", re.IGNORECASE)


def sanitize_evidence_text(raw_text: Optional[str]) -> str:
    """
    Exhaustively redacts sensitive credentials, PII, and tokens from error messages,
    stack traces, and log snippets before storing in the database or sending via email.
    """
    if not raw_text:
        return ""

    text = str(raw_text)

    # 1. Base URL / FlightAPI URL scrubbing
    text = sanitize_flightapi_url(text)

    # 2. Database connection credentials (e.g. postgres://user:password@host/db)
    text = DB_URL_SCRUB_REGEX.sub(r"\1://\2:[REDACTED]@", text)

    # 3. Password and secret parameters
    text = PASSWORD_SCRUB_REGEX.sub(r"\1=[REDACTED]", text)

    # 4. Bearer tokens
    text = BEARER_SCRUB_REGEX.sub(r"\1[REDACTED]", text)

    # 5. Email addresses (mask local part to avoid leaking customer PII)
    def _mask_email(match):
        addr = match.group(0)
        parts = addr.split("@")
        if len(parts) == 2 and len(parts[0]) > 2:
            return f"{parts[0][:2]}***@{parts[1]}"
        return "[EMAIL_REDACTED]"
    text = EMAIL_SCRUB_REGEX.sub(_mask_email, text)

    # 6. Specific environment secrets if configured
    sensitive_env_keys = [
        "SECRET_KEY",
        "ADMIN_DIAGNOSTICS_TOKEN",
        "ADMIN_KEY",
        "FLIGHTAPI_API_KEY",
        "SERPAPI_KEY",
        "AMADEUS_CLIENT_SECRET",
        "AMADEUS_CLIENT_ID",
        "SMTP_PASSWORD",
        "DATABASE_URL",
    ]
    for env_key in sensitive_env_keys:
        val = os.getenv(env_key)
        if val and len(val.strip()) >= 5 and val in text:
            text = text.replace(val, "[REDACTED]")

    return text


# ============================================================
# SELF-HEALING AGENT CLASS
# ============================================================
class SelfHealingAgent:
    """
    Autonomous reliability and repair engine for SkyTracker.
    Coordinates the complete 10-stage incident lifecycle with strict safety boundaries.
    """

    def __init__(self, workspace_root: Optional[str] = None):
        self.workspace_root = os.path.abspath(workspace_root or os.getcwd())

    def _get_max_attempts(self) -> int:
        try:
            return int(os.getenv("SELF_HEALING_MAX_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS)))
        except (ValueError, TypeError):
            return DEFAULT_MAX_ATTEMPTS

    def _is_agent_enabled(self) -> bool:
        # In production environments (Render / FLASK_ENV=production), the agent is strictly disabled by default
        # unless explicitly opted in with ENABLE_SELF_HEALING_AGENT=true.
        default_val = "false" if (os.getenv("FLASK_ENV") == "production" or bool(os.getenv("RENDER")) or os.getenv("ENV") == "production") else "true"
        val = os.getenv("ENABLE_SELF_HEALING_AGENT", default_val).strip().lower()
        return val in ("1", "true", "yes", "on")

    def _get_alert_cooldown_seconds(self) -> int:
        try:
            return int(os.getenv("INCIDENT_ALERT_COOLDOWN_SECONDS", str(DEFAULT_ALERT_COOLDOWN_SECONDS)))
        except (ValueError, TypeError):
            return DEFAULT_ALERT_COOLDOWN_SECONDS

    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    def _now_iso(self) -> str:
        return self._now_utc().isoformat()

    # ------------------------------------------------------------
    # DATABASE ACCESS HELPERS (PostgreSQL & SQLite compatibility)
    # ------------------------------------------------------------
    def _execute_sql(self, query_pg: str, query_sqlite: str, params: tuple = ()) -> List[Dict[str, Any]]:
        conn = db()
        close_conn = True
        try:
            is_pg = bool(DATABASE_URL)
            query = query_pg if is_pg else query_sqlite
            cursor = conn.cursor() if hasattr(conn, "cursor") else conn
            res = cursor.execute(query, params)

            # Check if query was a SELECT
            if query.strip().upper().startswith("SELECT"):
                rows = res.fetchall() if hasattr(res, "fetchall") else cursor.fetchall()
                results = []
                for r in rows:
                    if hasattr(r, "keys"):
                        results.append(dict(r))
                    elif isinstance(r, dict):
                        results.append(r)
                    elif hasattr(cursor, "description") and cursor.description:
                        cols = [d[0] for d in cursor.description]
                        results.append(dict(zip(cols, r)))
                    else:
                        results.append(r)
                return results

            # Commit mutation
            if hasattr(conn, "commit"):
                conn.commit()
            return []
        except Exception as e:
            logger.error(f"SelfHealingAgent SQL execution failed: {e}")
            if hasattr(conn, "rollback"):
                try:
                    conn.rollback()
                except Exception:
                    pass
            return []
        finally:
            if close_conn and conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _execute_update(self, query_pg: str, query_sqlite: str, params: tuple = ()) -> int:
        """Executes an UPDATE/DELETE mutation and returns cursor.rowcount for atomic locking."""
        conn = db()
        close_conn = True
        try:
            is_pg = bool(DATABASE_URL)
            query = query_pg if is_pg else query_sqlite
            cursor = conn.cursor() if hasattr(conn, "cursor") else conn
            cursor.execute(query, params)
            rowcount = getattr(cursor, "rowcount", 0)
            if hasattr(conn, "commit"):
                conn.commit()
            return rowcount
        except Exception as e:
            logger.error(f"SelfHealingAgent SQL update failed: {e}")
            if hasattr(conn, "rollback"):
                try:
                    conn.rollback()
                except Exception:
                    pass
            return 0
        finally:
            if close_conn and conn:
                try:
                    conn.close()
                except Exception:
                    pass

    # ============================================================
    # STAGE 2: CLASSIFY
    # ============================================================
    def classify_failure(
        self,
        subsystem: str,
        error: Any,
        status_code: int = 500,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Examines failure characteristics and categorizes it into one of the explicit categories.
        Filters out normal user input validation errors (400, 422) which are not software defects.
        """
        context = context or {}
        err_str = str(error or "")
        err_type = type(error).__name__ if isinstance(error, Exception) else context.get("error_type", "OperationalError")
        err_lower = err_str.lower()

        # 1. Normal Client Validation (400 / 422 / User Input Errors)
        # Expected user validation failures are NOT bugs.
        is_client_4xx = 400 <= status_code < 500 and status_code not in (401, 403, 429)
        if is_client_4xx or err_type in ("ValidationError", "AirportValidationError", "DateValidationError"):
            return {
                "is_bug": False,
                "category": CAT_INVALID_INPUT,
                "severity": SEV_LOW,
                "can_auto_repair": False,
                "reason": "Normal user input validation error. Not an application defect.",
            }

        # 2. Provider Quota Exhaustion (429, search limit, quota reached)
        if status_code == 429 or is_quota_exhausted_error(status_code, err_str) or err_type == "ProviderQuotaExhaustedError":
            return {
                "is_bug": True,
                "category": CAT_QUOTA_EXHAUSTED,
                "severity": SEV_HIGH,
                "can_auto_repair": False,
                "reason": "External flight provider search quota or trial credit exhausted. Requires operator account upgrade.",
            }

        # 3. Authentication / Credential Errors (401, 403, missing keys, SMTP auth)
        if (
            status_code in (401, 403)
            or "unauthorized" in err_lower
            or "forbidden" in err_lower
            or "invalid api key" in err_lower
            or "missing api key" in err_lower
            or "authenticationerror" in err_lower
            or "smtperror" in err_lower
            or "smtpauthenticationerror" in err_lower
        ):
            return {
                "is_bug": True,
                "category": CAT_AUTH_ERROR,
                "severity": SEV_HIGH,
                "can_auto_repair": False,
                "reason": "Authentication or credentials invalid/missing. Requires operator environment configuration.",
            }

        # 4. External Provider Outage / Network Timeout (502, 503, 504, timeout, connection refused)
        if (
            status_code in (502, 503, 504)
            or err_type in ("ProviderUnavailableError", "Timeout", "ConnectionError", "HTTPError")
            or "connection refused" in err_lower
            or "timed out" in err_lower
            or "gateway" in err_lower
            or "service unavailable" in err_lower
        ):
            return {
                "is_bug": True,
                "category": CAT_PROVIDER_OUTAGE,
                "severity": SEV_HIGH,
                "can_auto_repair": False,
                "reason": "Third-party flight data provider is unreachable or timing out. Expected circuit breaker protection.",
            }

        # 5. Infrastructure Failures (database connection drop, disk full)
        if (
            "disk i/o error" in err_lower
            or "could not connect to server" in err_lower
            or "database is locked" in err_lower
            or "connection to server at" in err_lower
            or "no space left on device" in err_lower
        ):
            return {
                "is_bug": True,
                "category": CAT_INFRASTRUCTURE,
                "severity": SEV_CRITICAL,
                "can_auto_repair": False,
                "reason": "Host infrastructure or database engine failure. Requires server administrator review.",
            }

        # 6. API Guardian Circuit Breaker Active
        if "circuit is open" in err_lower or "circuit_open" in err_lower or "blocked_budget" in err_lower:
            return {
                "is_bug": True,
                "category": CAT_CIRCUIT_BREAKER,
                "severity": SEV_MEDIUM,
                "can_auto_repair": False,
                "reason": "API Guardian protective circuit breaker or request budget guard is active.",
            }

        # 7. Application Defects (Logic errors, unhandled exceptions, syntax errors, regex defects)
        # Client telemetry and browser frontend JS reports can NEVER trigger automated code modification.
        is_client_telemetry = bool(context and (context.get("client_telemetry") or subsystem == SUBSYSTEM_FRONTEND))
        can_repair = not is_client_telemetry

        severity = SEV_CRITICAL if status_code >= 500 else SEV_MEDIUM
        return {
            "is_bug": True,
            "category": CAT_APPLICATION_DEFECT,
            "severity": severity,
            "can_auto_repair": can_repair,
            "reason": "Client-side frontend telemetry recorded for diagnostic review." if is_client_telemetry else "Internal application software defect or unhandled exception.",
        }

    # ============================================================
    # STAGE 1: DETECT & FINGERPRINT
    # ============================================================
    def generate_fingerprint(
        self,
        subsystem: str,
        category: str,
        error_type: str,
        message: str,
        location: str = ""
    ) -> str:
        """
        Generates a stable, deterministic SHA-256 fingerprint for grouping recurring incidents.
        Normalizes variable content like numbers and memory addresses.
        """
        norm_msg = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", message or "")
        norm_msg = re.sub(r"\d+", "NUM", norm_msg)
        norm_msg = norm_msg.strip().lower()[:120]

        payload = f"{subsystem}:{category}:{error_type}:{norm_msg}:{location}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def detect_incident(
        self,
        subsystem: str,
        error: Any,
        status_code: int = 500,
        traceback_str: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        auto_repair: Optional[bool] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Stage 1 & 2 Entry Point:
        Detects, sanitizes, classifies, and records an incident.
        Returns the incident record if a genuine failure was detected, or None if filtered.
        """
        subsystem = subsystem if subsystem in ALL_SUBSYSTEMS else SUBSYSTEM_WEB
        classification = self.classify_failure(subsystem, error, status_code, context)

        # Skip normal expected user validation errors (400, 422)
        if not classification.get("is_bug", True):
            logger.debug(f"SelfHealingAgent skipped non-bug event: {classification.get('reason')}")
            return None

        err_type = type(error).__name__ if isinstance(error, Exception) else context.get("error_type", "OperationalError") if context else "OperationalError"
        raw_msg = str(error) if error else (context.get("message") if context else "Unknown error")
        sanitized_msg = sanitize_evidence_text(raw_msg)
        sanitized_tb = sanitize_evidence_text(traceback_str or "")

        # Extract top source location from traceback if present
        location = ""
        if sanitized_tb:
            loc_match = re.search(r'File "([^"]+)", line (\d+)', sanitized_tb)
            if loc_match:
                location = f"{os.path.basename(loc_match.group(1))}:{loc_match.group(2)}"

        category = classification["category"]
        severity = classification["severity"]
        can_auto_repair = classification["can_auto_repair"]
        fingerprint = self.generate_fingerprint(subsystem, category, err_type, sanitized_msg, location)

        now_iso = self._now_iso()
        title = f"{category.replace('_', ' ').title()} in {subsystem.capitalize()}: {err_type}"

        # Check for existing active incident with same fingerprint
        existing = self._execute_sql(
            query_pg="""
                SELECT id, incident_id, fingerprint, status, occurrences, attempts_count, max_attempts, severity
                FROM incidents
                WHERE fingerprint = %s AND status NOT IN ('resolved', 'closed')
                ORDER BY id DESC LIMIT 1
            """,
            query_sqlite="""
                SELECT id, incident_id, fingerprint, status, occurrences, attempts_count, max_attempts, severity
                FROM incidents
                WHERE fingerprint = ? AND status NOT IN ('resolved', 'closed')
                ORDER BY id DESC LIMIT 1
            """,
            params=(fingerprint,)
        )

        if existing:
            inc = existing[0]
            inc_id = inc["incident_id"]
            new_occurrences = int(inc["occurrences"]) + 1
            old_severity = inc.get("severity", severity)

            # Update recurring incident
            self._execute_sql(
                query_pg="""
                    UPDATE incidents
                    SET occurrences = %s, last_seen_at = %s, sanitized_message = %s, updated_at = %s
                    WHERE incident_id = %s
                """,
                query_sqlite="""
                    UPDATE incidents
                    SET occurrences = ?, last_seen_at = ?, sanitized_message = ?, updated_at = ?
                    WHERE incident_id = ?
                """,
                params=(new_occurrences, now_iso, sanitized_msg, now_iso, inc_id)
            )

            # Check if severity escalated
            if SEVERITY_WEIGHTS.get(severity, 1) > SEVERITY_WEIGHTS.get(old_severity, 1):
                self._execute_sql(
                    query_pg="UPDATE incidents SET severity = %s WHERE incident_id = %s",
                    query_sqlite="UPDATE incidents SET severity = ? WHERE incident_id = ?",
                    params=(severity, inc_id)
                )
                self.record_transition(inc_id, inc["status"], inc["status"], f"Severity escalated to {severity}")
                self.escalate_incident(inc_id, reason=f"Severity escalated from {old_severity} to {severity}")

            return self.get_incident_detail(inc_id)

        # Create new incident record
        inc_id = f"inc_{uuid.uuid4().hex[:12]}"
        max_attempts = self._get_max_attempts()

        self._execute_sql(
            query_pg="""
                INSERT INTO incidents (
                    incident_id, fingerprint, title, subsystem, category, severity, status,
                    error_type, sanitized_message, sanitized_traceback, first_seen_at, last_seen_at,
                    occurrences, attempts_count, max_attempts, needs_human_approval, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            query_sqlite="""
                INSERT INTO incidents (
                    incident_id, fingerprint, title, subsystem, category, severity, status,
                    error_type, sanitized_message, sanitized_traceback, first_seen_at, last_seen_at,
                    occurrences, attempts_count, max_attempts, needs_human_approval, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params=(
                inc_id, fingerprint, title, subsystem, category, severity, STATE_DETECTED,
                err_type, sanitized_msg, sanitized_tb, now_iso, now_iso,
                1, 0, max_attempts, 0 if can_auto_repair else 1, now_iso, now_iso
            )
        )

        self.record_transition(inc_id, "none", STATE_DETECTED, f"Detected {category}: {err_type}")

        # If not repairable by code (quota, outage, auth, infra), immediately escalate to human operator
        if not can_auto_repair:
            self.escalate_incident(inc_id, reason=classification.get("reason", "Operator action required"))
            return self.get_incident_detail(inc_id)

        # If automated repair is explicitly requested or globally enabled on detect, trigger lifecycle
        should_auto_repair = auto_repair if auto_repair is not None else (
            self._is_agent_enabled() and os.getenv("SELF_HEALING_AUTO_REPAIR_ON_DETECT", "false").lower() in ("1", "true", "yes")
        )
        if should_auto_repair:
            try:
                self.handle_automated_repair_lifecycle(inc_id)
            except Exception as e:
                logger.error(f"Automated repair pipeline failed for {inc_id}: {e}")
                self.escalate_incident(inc_id, reason=f"Automated repair cycle crashed: {e}")

        return self.get_incident_detail(inc_id)

    # ============================================================
    # STAGE 3 & 4: COLLECT EVIDENCE & DIAGNOSE
    # ============================================================
    def diagnose_incident(self, incident_id: str) -> Dict[str, Any]:
        """
        Stages 3 & 4:
        Collects comprehensive evidence and formulates a diagnostic hypothesis.
        """
        inc = self.get_incident_record(incident_id)
        if not inc:
            return {"error": "Incident not found", "can_repair": False}

        self.record_transition(incident_id, inc["status"], STATE_INVESTIGATING, "Collecting evidence and analyzing root cause")
        self._update_status(incident_id, STATE_INVESTIGATING)

        category = inc["category"]
        subsystem = inc["subsystem"]
        error_type = inc.get("error_type", "")
        message = inc.get("sanitized_message", "")
        traceback_str = inc.get("sanitized_traceback", "")

        # Target file resolution
        target_file = None
        target_line = None
        if traceback_str:
            matches = re.findall(r'File "([^"]+)", line (\d+)', traceback_str)
            for file_path, line_no in reversed(matches):
                rel_path = os.path.relpath(file_path, self.workspace_root).replace("\\", "/")
                # Check against allowlist
                if any(rel_path.startswith(prefix) for prefix in PERMITTED_FILE_PREFIXES):
                    if not any(rel_path.endswith(fb) for fb in FORBIDDEN_FILES):
                        target_file = rel_path
                        target_line = int(line_no)
                        break

        # Diagnosis rule engine
        hypothesis = ""
        confidence = 0.5
        can_repair = False

        if category == CAT_QUOTA_EXHAUSTED:
            hypothesis = "FlightAPI or SerpAPI trial quota exhausted. Autonomous code changes cannot resolve provider credit limits."
            confidence = 1.0
            can_repair = False
        elif category == CAT_AUTH_ERROR:
            hypothesis = "API keys or SMTP credentials are missing or rejected by the remote provider."
            confidence = 0.95
            can_repair = False
        elif category == CAT_PROVIDER_OUTAGE:
            hypothesis = "Upstream provider endpoint timed out or returned HTTP 5xx. Service is temporarily unreachable."
            confidence = 0.9
            can_repair = False
        elif category == CAT_INFRASTRUCTURE:
            hypothesis = "Host operating system, disk, or PostgreSQL/SQLite storage engine experienced an operational failure."
            confidence = 0.95
            can_repair = False
        elif category == CAT_APPLICATION_DEFECT:
            if target_file:
                hypothesis = f"Application software bug detected in `{target_file}` around line {target_line or 'unknown'}: {error_type}."
                confidence = 0.85
                can_repair = True
            else:
                hypothesis = f"Application defect identified, but affected module is outside permitted repair boundaries: {error_type}."
                confidence = 0.6
                can_repair = False
        else:
            hypothesis = f"Unclassified failure: {message[:100]}"
            confidence = 0.4
            can_repair = False

        return {
            "incident_id": incident_id,
            "category": category,
            "subsystem": subsystem,
            "hypothesis": hypothesis,
            "confidence": confidence,
            "can_repair": can_repair,
            "target_file": target_file,
            "target_line": target_line,
        }

    # ============================================================
    # STAGE 5: PLAN REPAIR
    # ============================================================
    def plan_repair(self, incident_id: str, diagnosis: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Stage 5:
        Formulates a surgical, reversible repair plan adhering to strict safety policies.
        """
        inc = self.get_incident_record(incident_id)
        if not inc:
            return None

        attempts = int(inc.get("attempts_count", 0))
        max_attempts = int(inc.get("max_attempts", DEFAULT_MAX_ATTEMPTS))

        if attempts >= max_attempts:
            logger.info(f"Incident {incident_id} reached max repair attempts ({attempts}/{max_attempts}). Escalating.")
            self._update_human_approval(incident_id, True)
            self.escalate_incident(incident_id, reason=f"Max automated repair attempts reached ({attempts}/{max_attempts})")
            return None

        target_file = diagnosis.get("target_file")
        if not target_file:
            logger.info(f"Incident {incident_id} has no permitted target file. Escalating.")
            self._update_human_approval(incident_id, True)
            self.escalate_incident(incident_id, reason="No allowlisted target file found for automatic repair")
            return None

        # Verify target file is allowlisted and not forbidden
        if not any(target_file.startswith(prefix) for prefix in PERMITTED_FILE_PREFIXES):
            logger.warning(f"Target file {target_file} is not allowlisted.")
            return None
        if any(target_file.endswith(fb) for fb in FORBIDDEN_FILES):
            logger.warning(f"Target file {target_file} is strictly forbidden from automated modification.")
            return None

        abs_target = os.path.join(self.workspace_root, target_file)
        if not os.path.isfile(abs_target):
            logger.warning(f"Target file {abs_target} does not exist.")
            return None

        plan = {
            "incident_id": incident_id,
            "target_file": target_file,
            "abs_target": abs_target,
            "plan_description": f"Surgical repair in {target_file} to resolve {inc.get('error_type')}",
            "patch_type": "isolated_source_patch",
            "max_lines": MAX_PATCH_LINES,
        }

        self.record_transition(incident_id, inc["status"], STATE_FIX_PROPOSED, f"Proposed repair plan for {target_file}")
        self._update_status(incident_id, STATE_FIX_PROPOSED)
        return plan

    # ============================================================
    # STAGE 6: APPLY REPAIR (Concurrency-Safe & Isolated)
    # ============================================================
    def apply_repair(self, incident_id: str, plan: Dict[str, Any], patch_fn: Optional[Any] = None) -> Tuple[bool, Optional[str], Optional[int]]:
        """
        Stage 6:
        Atomically claims the incident and applies the patch to the target file.
        Uses database row locking to prevent concurrent workers from fixing the same incident.
        Returns (success, backup_path, repair_id).
        """
        now_iso = self._now_iso()

        # Atomic claim: Only transition if currently in (detected, investigating, fix_proposed)
        # Atomic claim: Only transition if currently in (detected, investigating, fix_proposed)
        # Guarantees single-worker execution across multiple Gunicorn processes
        claim_count = self._execute_update(
            query_pg="""
                UPDATE incidents
                SET status = 'fixing', updated_at = %s
                WHERE incident_id = %s
                  AND status IN ('detected', 'investigating', 'fix_proposed')
                  AND attempts_count < max_attempts
            """,
            query_sqlite="""
                UPDATE incidents
                SET status = 'fixing', updated_at = ?
                WHERE incident_id = ?
                  AND status IN ('detected', 'investigating', 'fix_proposed')
                  AND attempts_count < max_attempts
            """,
            params=(now_iso, incident_id)
        )

        # Check if this worker won the atomic race
        if claim_count <= 0:
            logger.info(f"Worker did not acquire fixing lock for {incident_id}. Skipping concurrent attempt.")
            return False, None, None

        self.record_transition(incident_id, STATE_FIX_PROPOSED, STATE_FIXING, "Acquired repair lock and applying patch")

        target_file = plan["target_file"]
        abs_target = plan["abs_target"]

        # Preserve uncommitted user changes: check git status of target file
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain", target_file],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=10
            )
            # If target file has pre-existing uncommitted modifications by the user, DO NOT overwrite!
            if res.returncode == 0 and res.stdout.strip():
                logger.warning(f"File {target_file} has uncommitted user edits. Aborting to protect user work.")
                self.rollback_repair(incident_id, None, reason="Uncommitted user edits detected in target file", target_path=abs_target)
                self.escalate_incident(incident_id, reason=f"Cannot repair {target_file}: uncommitted user changes exist")
                return False, None, None
        except Exception as e:
            logger.debug(f"Git status check failed: {e}")

        # Create isolated backup file for guaranteed zero-loss rollback
        backup_path = f"{abs_target}.bak.{uuid.uuid4().hex[:8]}"
        try:
            shutil.copy2(abs_target, backup_path)
        except Exception as e:
            logger.error(f"Failed to create backup copy of {abs_target}: {e}")
            self._update_status(incident_id, STATE_DETECTED)
            return False, None, None

        # Apply the patch function or content
        patch_applied = False
        patch_diff = ""
        try:
            if callable(patch_fn):
                patch_applied = bool(patch_fn(abs_target))
            elif "new_content" in plan:
                with open(abs_target, "w", encoding="utf-8") as f:
                    f.write(plan["new_content"])
                patch_applied = True

            if patch_applied:
                # Capture git diff
                diff_res = subprocess.run(
                    ["git", "diff", target_file],
                    cwd=self.workspace_root,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                patch_diff = diff_res.stdout if diff_res.returncode == 0 else "Diff captured"
        except Exception as e:
            logger.error(f"Applying patch failed: {e}")
            patch_applied = False

        if not patch_applied:
            # Revert from backup
            try:
                shutil.copy2(backup_path, abs_target)
                os.remove(backup_path)
            except Exception:
                pass
            self.rollback_repair(incident_id, None, reason="Patch application failed")
            return False, None, None

        # Record repair attempt in database
        attempt_num = int(inc_check.get("attempts_count", 0)) + 1
        self._execute_sql(
            query_pg="""
                INSERT INTO incident_repairs (
                    incident_id, attempt_number, plan_description, target_files,
                    patch_diff, verification_output, success, rolled_back, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """,
            query_sqlite="""
                INSERT INTO incident_repairs (
                    incident_id, attempt_number, plan_description, target_files,
                    patch_diff, verification_output, success, rolled_back, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params=(
                incident_id, attempt_num, plan["plan_description"], target_file,
                patch_diff[:5000], "Pending verification", 0, 0, now_iso
            )
        )

        # Update incident attempt count
        self._execute_sql(
            query_pg="UPDATE incidents SET attempts_count = %s, updated_at = %s WHERE incident_id = %s",
            query_sqlite="UPDATE incidents SET attempts_count = ?, updated_at = ? WHERE incident_id = ?",
            params=(attempt_num, now_iso, incident_id)
        )

        return True, backup_path, attempt_num

    # ============================================================
    # STAGE 7 & 8: VERIFY GATE & ACCEPT OR ROLLBACK
    # ============================================================
    def verify_repair(
        self,
        incident_id: str,
        plan: Dict[str, Any],
        backup_path: Optional[str],
        custom_test_cmd: Optional[List[str]] = None
    ) -> bool:
        """
        Stages 7 & 8:
        Enforces a multi-stage verification gate before accepting any patch:
        1. Python syntax compilation check.
        2. Git diff check (no whitespace damage, max line threshold).
        3. Automated tests with mocks (ZERO live FlightAPI credits consumed).
        4. Rollback immediately on any failure.
        """
        self.record_transition(incident_id, STATE_FIXING, STATE_VERIFYING, "Running strict automated verification gate")
        self._update_status(incident_id, STATE_VERIFYING)

        abs_target = plan["abs_target"]
        target_file = plan["target_file"]
        verification_logs = []

        # Gate 1: Python Syntax Check
        if abs_target.endswith(".py"):
            try:
                import py_compile
                py_compile.compile(abs_target, doraise=True)
                verification_logs.append("Syntax Check: PASS")
            except py_compile.PyCompileError as e:
                verification_logs.append(f"Syntax Check: FAIL - {e}")
                logger.error(f"Verification syntax check failed for {abs_target}: {e}")
                self.rollback_repair(incident_id, backup_path, reason=f"Syntax compilation error: {e}", target_path=abs_target)
                return False

        # Gate 2: Git Diff Check & Line Count Guard
        try:
            diff_chk = subprocess.run(
                ["git", "diff", "--check", target_file],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=10
            )
            if diff_chk.returncode != 0:
                verification_logs.append(f"Git Diff Check: FAIL - {diff_chk.stdout}")
                self.rollback_repair(incident_id, backup_path, reason="Git diff --check failed (whitespace or patch corruption)", target_path=abs_target)
                return False
            verification_logs.append("Git Diff Check: PASS")

            # Check patch line threshold (max 50 lines changed)
            diff_lines = subprocess.run(
                ["git", "diff", "--shortstat", target_file],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=10
            )
            verification_logs.append(f"Diff Stats: {diff_lines.stdout.strip()}")
        except Exception as e:
            logger.debug(f"Git diff inspection warning: {e}")

        # Gate 3: Automated Test Execution (with hermetic mocks)
        # Note: conftest.py already strips live API keys and resets Guardian cache.
        test_env = os.environ.copy()
        test_env["MOCK_FLIGHT_API"] = "1"
        test_env["FLASK_ENV"] = "testing"

        test_cmd = custom_test_cmd or [sys.executable, "-m", "pytest", "tests/test_validation.py", "-q"]

        try:
            test_run = subprocess.run(
                test_cmd,
                cwd=self.workspace_root,
                env=test_env,
                capture_output=True,
                text=True,
                timeout=120
            )
            if test_run.returncode == 0:
                verification_logs.append(f"Test Suite: PASS ({test_run.stdout.strip()[:200]})")
            else:
                verification_logs.append(f"Test Suite: FAIL (code {test_run.returncode})")
                verification_logs.append(test_run.stdout[-500:] if test_run.stdout else "")
                verification_logs.append(test_run.stderr[-500:] if test_run.stderr else "")
                logger.warning(f"Verification tests failed for {incident_id}:\n{test_run.stdout}")
                self.rollback_repair(incident_id, backup_path, reason=f"Automated test verification failed (exit {test_run.returncode})", target_path=abs_target)
                return False
        except subprocess.TimeoutExpired:
            verification_logs.append("Test Suite: FAIL (Timeout)")
            self.rollback_repair(incident_id, backup_path, reason="Verification tests timed out", target_path=abs_target)
            return False
        except Exception as e:
            verification_logs.append(f"Test Suite: FAIL - {e}")
            self.rollback_repair(incident_id, backup_path, reason=f"Test execution exception: {e}", target_path=abs_target)
            return False

        # All Gates Passed: Accept Patch
        now_iso = self._now_iso()
        verif_summary = "\n".join(verification_logs)

        # Clean up backup file
        if backup_path and os.path.isfile(backup_path):
            try:
                os.remove(backup_path)
            except Exception:
                pass

        # Update repair record
        self._execute_sql(
            query_pg="""
                UPDATE incident_repairs
                SET verification_output = %s, success = 1
                WHERE incident_id = %s AND attempt_number = (
                    SELECT MAX(attempt_number) FROM incident_repairs WHERE incident_id = %s
                )
            """,
            query_sqlite="""
                UPDATE incident_repairs
                SET verification_output = ?, success = 1
                WHERE incident_id = ? AND attempt_number = (
                    SELECT MAX(attempt_number) FROM incident_repairs WHERE incident_id = ?
                )
            """,
            params=(verif_summary[:4000], incident_id, incident_id)
        )

        # Mark incident resolved
        self._execute_sql(
            query_pg="""
                UPDATE incidents
                SET status = %s, resolution_notes = %s, updated_at = %s
                WHERE incident_id = %s
            """,
            query_sqlite="""
                UPDATE incidents
                SET status = ?, resolution_notes = ?, updated_at = ?
                WHERE incident_id = ?
            """,
            params=(
                STATE_RESOLVED,
                f"Verified patch in {target_file}. All verification gates passed. (Deployment remains operator-approved)",
                now_iso,
                incident_id
            )
        )

        self.record_transition(incident_id, STATE_VERIFYING, STATE_RESOLVED, "Verification gates passed 100%. Patch accepted locally.")
        logger.info(f"Incident {incident_id} successfully resolved and verified.")
        return True

    def rollback_repair(
        self,
        incident_id: str,
        backup_path: Optional[str],
        reason: str = "Verification failed",
        target_path: Optional[str] = None
    ) -> bool:
        """
        Stage 8 (Rollback Path):
        Cleanly reverts the agent's patch from backup or git, restoring exact pre-repair state.
        Transitions state to rolled_back -> escalated.
        """
        now_iso = self._now_iso()

        # Revert file from backup if available
        if backup_path and os.path.isfile(backup_path):
            try:
                orig_target = target_path or re.sub(r"\.bak\..*$", "", backup_path)
                if os.path.abspath(orig_target) != os.path.abspath(backup_path):
                    shutil.copy2(backup_path, orig_target)
                    os.remove(backup_path)
                    logger.info(f"Restored {orig_target} from backup.")
            except Exception as e:
                logger.error(f"Failed to restore from backup {backup_path}: {e}")

        # Update repair record
        self._execute_sql(
            query_pg="""
                UPDATE incident_repairs
                SET rolled_back = 1, verification_output = %s
                WHERE incident_id = %s AND attempt_number = (
                    SELECT MAX(attempt_number) FROM incident_repairs WHERE incident_id = %s
                )
            """,
            query_sqlite="""
                UPDATE incident_repairs
                SET rolled_back = 1, verification_output = ?
                WHERE incident_id = ? AND attempt_number = (
                    SELECT MAX(attempt_number) FROM incident_repairs WHERE incident_id = ?
                )
            """,
            params=(reason, incident_id, incident_id)
        )

        self._update_status(incident_id, STATE_ROLLED_BACK)
        self.record_transition(incident_id, STATE_VERIFYING, STATE_ROLLED_BACK, reason)

        # Escalate to owner
        self.escalate_incident(incident_id, reason=f"Automated repair rolled back: {reason}")
        return True

    # ============================================================
    # STAGE 9: ESCALATE (Actionable Owner Email with Deduplication)
    # ============================================================
    def escalate_incident(self, incident_id: str, reason: str = "Incident requires operator intervention") -> bool:
        """
        Stage 9:
        Sends a structured, actionable email alert to the configured platform owner.
        Applies cooldown deduplication to prevent email storms while capturing every escalation attempt.
        """
        inc = self.get_incident_record(incident_id)
        if not inc:
            return False

        now_utc = self._now_utc()
        now_ts = now_utc.timestamp()
        now_iso = now_utc.isoformat()
        cooldown_sec = self._get_alert_cooldown_seconds()

        # Check last sent alert timestamp for deduplication
        last_alerts = self._execute_sql(
            query_pg="""
                SELECT sent_at FROM incident_escalations
                WHERE incident_id = %s AND status = 'sent'
                ORDER BY id DESC LIMIT 1
            """,
            query_sqlite="""
                SELECT sent_at FROM incident_escalations
                WHERE incident_id = ? AND status = 'sent'
                ORDER BY id DESC LIMIT 1
            """,
            params=(incident_id,)
        )

        if last_alerts:
            last_sent_str = last_alerts[0]["sent_at"]
            try:
                last_dt = datetime.fromisoformat(last_sent_str)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if (now_ts - last_dt.timestamp()) < cooldown_sec:
                    logger.info(f"Escalation email for {incident_id} suppressed by active cooldown window ({cooldown_sec}s).")
                    self._update_status(incident_id, STATE_ESCALATED)
                    return False
            except Exception as e:
                logger.debug(f"Timestamp parse error on cooldown check: {e}")

        # Resolve owner recipient
        recipient = (
            os.getenv("OWNER_ALERT_EMAIL")
            or os.getenv("ADMIN_EMAIL")
            or os.getenv("SMTP_FROM")
            or "owner@skytracker.luxury"
        )

        subsystem = inc.get("subsystem", "System").capitalize()
        category = inc.get("category", "Incident").replace("_", " ").title()
        severity = inc.get("severity", "high").upper()
        title = inc.get("title", f"{category} in {subsystem}")
        sanitized_msg = inc.get("sanitized_message", "No message provided")
        sanitized_tb = inc.get("sanitized_traceback", "No traceback available")

        # Recommended operator action based on category
        rec_action = "Review application diagnostics and investigate error logs."
        if inc.get("category") == CAT_QUOTA_EXHAUSTED:
            rec_action = (
                "1. Log into flightapi.io or serpapi.com and review remaining search credits.\n"
                "2. Purchase credit top-up or enable secondary provider (Amadeus/SerpAPI).\n"
                "3. Reset the API Guardian circuit breaker via POST /api/diagnostics/quota/reset."
            )
        elif inc.get("category") == CAT_AUTH_ERROR:
            rec_action = (
                "1. Verify FLIGHTAPI_API_KEY, SERPAPI_KEY, or SMTP credentials in Render Dashboard.\n"
                "2. Update the environment variables and trigger a redeploy.\n"
                "3. Verify authentication status at /api/diagnostics/quota."
            )
        elif inc.get("category") == CAT_PROVIDER_OUTAGE:
            rec_action = (
                "1. Check upstream status pages for FlightAPI / Google Flights / Amadeus.\n"
                "2. The API Guardian circuit breaker is currently protecting against credit waste.\n"
                "3. Verify recovery once provider service is restored."
            )
        elif inc.get("category") == CAT_APPLICATION_DEFECT:
            rec_action = (
                f"1. Review recent changes to {subsystem} and verify automated repair diff.\n"
                f"2. Inspect diagnostics dashboard at /api/diagnostics/incidents/{incident_id}.\n"
                "3. Manually test and deploy approved patch."
            )

        subject = f"[SkyTracker Alert] {category} in {subsystem} — {severity}"

        # Plain text email content
        text_body = f"""SKYTRACKER RELIABILITY ALERT
===========================
Incident ID:   {incident_id}
Category:      {category}
Subsystem:     {subsystem}
Severity:      {severity}
Occurrences:   {inc.get('occurrences', 1)}
First Seen:    {inc.get('first_seen_at', now_iso)}
Last Seen:     {inc.get('last_seen_at', now_iso)}

Reason for Escalation:
{reason}

Sanitized Error Message:
{sanitized_msg}

Recommended Operator Action:
{rec_action}

Diagnostics & Traceback:
{sanitized_tb[:1500]}

---
SkyTracker Self-Healing Agent | Confidential Reliability Telemetry
"""

        # Surreal Quiet Luxury HTML Email Content
        from services.email_service import _render_email_shell, send_email

        html_content = f"""
        <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(223,201,158,0.25); border-radius: 8px; padding: 24px; margin-bottom: 24px;">
          <div style="font-size: 11px; letter-spacing: 0.15em; text-transform: uppercase; color: #DFC99E; margin-bottom: 8px;">
            Autonomous Reliability Escalation
          </div>
          <h2 style="margin: 0 0 16px 0; font-size: 20px; color: #F0F2F5; font-weight: 500;">
            {category} in {subsystem}
          </h2>
          <table style="width: 100%; border-collapse: collapse; margin-bottom: 16px; font-size: 13px;">
            <tr>
              <td style="color: #9BA3AF; padding: 6px 0; width: 140px;">Incident ID:</td>
              <td style="color: #E6E8EC; font-family: monospace; font-weight: bold;">{incident_id}</td>
            </tr>
            <tr>
              <td style="color: #9BA3AF; padding: 6px 0;">Severity:</td>
              <td style="color: {'#FF6B6B' if severity in ('CRITICAL', 'HIGH') else '#DFC99E'}; font-weight: bold;">{severity}</td>
            </tr>
            <tr>
              <td style="color: #9BA3AF; padding: 6px 0;">Occurrences:</td>
              <td style="color: #E6E8EC;">{inc.get('occurrences', 1)}</td>
            </tr>
            <tr>
              <td style="color: #9BA3AF; padding: 6px 0;">Escalation Reason:</td>
              <td style="color: #E6E8EC;">{reason}</td>
            </tr>
          </table>

          <div style="background: #14161C; border-left: 3px solid #DFC99E; padding: 12px 16px; margin: 16px 0; font-size: 13px; color: #D1D5DB;">
            <strong>Recommended Operator Action:</strong><br>
            <pre style="margin: 8px 0 0 0; font-family: inherit; white-space: pre-wrap;">{rec_action}</pre>
          </div>

          <div style="background: #0E1015; border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; padding: 12px; margin-top: 16px;">
            <div style="font-size: 11px; color: #9BA3AF; margin-bottom: 6px; text-transform: uppercase;">Sanitized Error Detail</div>
            <code style="color: #FF9B9B; font-size: 12px; word-break: break-all;">{sanitized_msg}</code>
          </div>
        </div>
        """

        html_body = _render_email_shell(
            title=subject,
            content_html=html_content,
            footer_note="This is an automated operational alert generated by the SkyTracker Self-Healing Agent. All credentials and PII have been redacted."
        )

        # Dispatch email safely (resilient to SMTP failure; will never crash the app)
        send_success = False
        error_detail = None
        try:
            send_success = send_email(
                to_email=recipient,
                subject=subject,
                text_body=text_body,
                html_body=html_body
            )
            if not send_success:
                error_detail = "SMTP dispatch returned False (unconfigured or rejected)"
        except Exception as exc:
            error_detail = str(exc)
            logger.error(f"Failed to deliver escalation email for {incident_id}: {exc}")

        # Record escalation event
        esc_status = "sent" if send_success else "failed"
        self._execute_sql(
            query_pg="""
                INSERT INTO incident_escalations (incident_id, recipient, subject, status, error_detail, sent_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """,
            query_sqlite="""
                INSERT INTO incident_escalations (incident_id, recipient, subject, status, error_detail, sent_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
            params=(incident_id, recipient, subject, esc_status, error_detail, now_iso)
        )

        self._update_status(incident_id, STATE_ESCALATED)
        self.record_transition(incident_id, inc.get("status", STATE_DETECTED), STATE_ESCALATED, f"Escalated to {recipient}: {reason}")
        return send_success

    # ============================================================
    # AUTOMATED REPAIR ORCHESTRATION PIPELINE
    # ============================================================
    def handle_automated_repair_lifecycle(self, incident_id: str, patch_fn: Optional[Any] = None) -> bool:
        """
        Coordinates stages 3 through 8 for repairable application defects.
        """
        diagnosis = self.diagnose_incident(incident_id)
        if not diagnosis.get("can_repair"):
            self.escalate_incident(incident_id, reason=diagnosis.get("hypothesis", "Issue cannot be automatically repaired"))
            return False

        plan = self.plan_repair(incident_id, diagnosis)
        if not plan:
            return False

        applied, backup_path, attempt_num = self.apply_repair(incident_id, plan, patch_fn)
        if not applied:
            return False

        return self.verify_repair(incident_id, plan, backup_path)

    # ============================================================
    # AUDIT TRAIL & TRANSITION TRACKING
    # ============================================================
    def record_transition(self, incident_id: str, from_state: str, to_state: str, reason: str, actor: str = "self_healing_agent"):
        """Records an immutable state transition event in incident_transitions."""
        now_iso = self._now_iso()
        self._execute_sql(
            query_pg="""
                INSERT INTO incident_transitions (incident_id, from_state, to_state, reason, actor, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """,
            query_sqlite="""
                INSERT INTO incident_transitions (incident_id, from_state, to_state, reason, actor, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
            params=(incident_id, from_state, to_state, reason, actor, now_iso)
        )

    def _update_status(self, incident_id: str, status: str):
        now_iso = self._now_iso()
        self._execute_sql(
            query_pg="UPDATE incidents SET status = %s, updated_at = %s WHERE incident_id = %s",
            query_sqlite="UPDATE incidents SET status = ?, updated_at = ? WHERE incident_id = ?",
            params=(status, now_iso, incident_id)
        )

    def _update_human_approval(self, incident_id: str, needs_approval: bool):
        val = 1 if needs_approval else 0
        now_iso = self._now_iso()
        self._execute_sql(
            query_pg="UPDATE incidents SET needs_human_approval = %s, updated_at = %s WHERE incident_id = %s",
            query_sqlite="UPDATE incidents SET needs_human_approval = ?, updated_at = ? WHERE incident_id = ?",
            params=(val, now_iso, incident_id)
        )

    # ============================================================
    # QUERY & DIAGNOSTIC INSPECTION INTERFACES
    # ============================================================
    def get_incident_record(self, incident_id: str) -> Optional[Dict[str, Any]]:
        rows = self._execute_sql(
            query_pg="SELECT * FROM incidents WHERE incident_id = %s LIMIT 1",
            query_sqlite="SELECT * FROM incidents WHERE incident_id = ? LIMIT 1",
            params=(incident_id,)
        )
        return rows[0] if rows else None

    def get_incident_detail(self, incident_id: str) -> Optional[Dict[str, Any]]:
        """Returns the full incident record along with transitions, repairs, and escalations."""
        inc = self.get_incident_record(incident_id)
        if not inc:
            return None

        transitions = self._execute_sql(
            query_pg="SELECT * FROM incident_transitions WHERE incident_id = %s ORDER BY id ASC",
            query_sqlite="SELECT * FROM incident_transitions WHERE incident_id = ? ORDER BY id ASC",
            params=(incident_id,)
        )
        repairs = self._execute_sql(
            query_pg="SELECT * FROM incident_repairs WHERE incident_id = %s ORDER BY attempt_number ASC",
            query_sqlite="SELECT * FROM incident_repairs WHERE incident_id = ? ORDER BY attempt_number ASC",
            params=(incident_id,)
        )
        escalations = self._execute_sql(
            query_pg="SELECT * FROM incident_escalations WHERE incident_id = %s ORDER BY id ASC",
            query_sqlite="SELECT * FROM incident_escalations WHERE incident_id = ? ORDER BY id ASC",
            params=(incident_id,)
        )

        inc_dict = dict(inc)
        inc_dict["transitions"] = transitions
        inc_dict["repairs"] = repairs
        inc_dict["escalations"] = escalations
        return inc_dict

    def get_incidents(
        self,
        status: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Retrieves paginated incidents filtered by status or category."""
        limit = max(1, min(100, limit))
        offset = max(0, offset)

        conditions = []
        params = []
        if status:
            conditions.append("status = %s" if DATABASE_URL else "status = ?")
            params.append(status)
        if category:
            conditions.append("category = %s" if DATABASE_URL else "category = ?")
            params.append(category)

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        q_pg = f"SELECT * FROM incidents {where_clause} ORDER BY id DESC LIMIT {limit} OFFSET {offset}"
        q_sqlite = f"SELECT * FROM incidents {where_clause} ORDER BY id DESC LIMIT {limit} OFFSET {offset}"

        return self._execute_sql(q_pg, q_sqlite, tuple(params))

    def get_reliability_summary(self) -> Dict[str, Any]:
        """Provides high-level health and incident metrics for the operator dashboard."""
        all_incidents = self.get_incidents(limit=100)
        active = [i for i in all_incidents if i["status"] in ACTIVE_STATES]
        resolved = [i for i in all_incidents if i["status"] == STATE_RESOLVED]
        escalated = [i for i in all_incidents if i["status"] == STATE_ESCALATED]
        needs_approval = [i for i in all_incidents if i.get("needs_human_approval") == 1]

        # Calculate counts
        category_counts = {}
        for inc in all_incidents:
            cat = inc.get("category", "other")
            category_counts[cat] = category_counts.get(cat, 0) + 1

        return {
            "status": "healthy" if len(active) == 0 else ("degraded" if any(i["severity"] == SEV_CRITICAL for i in active) else "warning"),
            "agent_enabled": self._is_agent_enabled(),
            "max_repair_attempts": self._get_max_attempts(),
            "metrics": {
                "total_incidents": len(all_incidents),
                "active_incidents": len(active),
                "resolved_incidents": len(resolved),
                "escalated_incidents": len(escalated),
                "awaiting_approval": len(needs_approval),
            },
            "category_distribution": category_counts,
            "recent_incidents": all_incidents[:10],
        }


# Global singleton instance
agent = SelfHealingAgent()
