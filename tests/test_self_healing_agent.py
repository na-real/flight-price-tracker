"""
Tests for SkyTracker Self-Healing AI Bug-Fixing Agent
====================================================
Comprehensive test coverage for all 16 prompt scenarios:
1. Repeated Flask exception creates one incident with incremented occurrences count.
2. Expected validation errors (400) are not treated as bugs.
3. Provider quota exhaustion is classified as an external/quota incident without code changes.
4. A repair proposal is validated against safety boundaries before being applied.
5. Repair attempts preserve uncommitted user changes.
6. Passing tests permit a patch to be marked verified (without triggering deployment).
7. Failed tests cause the agent's patch to be rolled back cleanly.
8. Unresolved incidents trigger an owner escalation email.
9. Email delivery failure is recorded in the database without crashing the application.
10. Duplicate incidents do not generate repeated emails during cooldown window.
11. Repair-attempt limits prevent infinite loops.
12. Concurrent workers do not repair the same incident simultaneously.
13. Secrets, API keys, passwords, and customer emails are thoroughly redacted.
14. Unauthorized users cannot access administrative incident diagnostics or retry operations.
15. The agent cannot bypass API Guardian budget controls or circuit breakers.
16. End-to-end integration with existing routes and error handlers.
"""

import os
import re
import sys
import json
import uuid
import pytest
from unittest.mock import patch, MagicMock

from app import app
from db import db, init_db, DATABASE_URL
from services.self_healing_agent import (
    agent,
    SelfHealingAgent,
    SUBSYSTEM_WEB,
    SUBSYSTEM_SEARCH,
    SUBSYSTEM_PROVIDER,
    SUBSYSTEM_GUARDIAN,
    SUBSYSTEM_CHECKER,
    SUBSYSTEM_FRONTEND,
    SUBSYSTEM_VALIDATION,
    CAT_APPLICATION_DEFECT,
    CAT_PROVIDER_OUTAGE,
    CAT_QUOTA_EXHAUSTED,
    CAT_AUTH_ERROR,
    CAT_INVALID_INPUT,
    CAT_INFRASTRUCTURE,
    STATE_DETECTED,
    STATE_INVESTIGATING,
    STATE_FIX_PROPOSED,
    STATE_FIXING,
    STATE_VERIFYING,
    STATE_RESOLVED,
    STATE_ESCALATED,
    STATE_ROLLED_BACK,
    SEV_CRITICAL,
    SEV_HIGH,
    SEV_MEDIUM,
    SEV_LOW,
    sanitize_evidence_text,
)
from services.api_guardian import guardian, PROVIDER_SERPAPI, CALLER_USER_SEARCH


@pytest.fixture(autouse=True)
def setup_incident_db():
    """Ensure database schema is initialized and clean between tests."""
    init_db()
    conn = db()
    try:
        conn.execute("DELETE FROM incident_escalations")
        conn.execute("DELETE FROM incident_repairs")
        conn.execute("DELETE FROM incident_transitions")
        conn.execute("DELETE FROM incidents")
        if hasattr(conn, "commit"):
            conn.commit()
    finally:
        conn.close()
    yield


# ============================================================
# TEST 1: REPEATED FLASK EXCEPTION CREATES ONE DEDUPED INCIDENT
# ============================================================
def test_repeated_flask_exception_creates_single_deduplicated_incident():
    """Verify identical errors group into the same incident with incremented occurrences count."""
    err = RuntimeError("Unexpected null pointer in flight normalization")
    tb = 'File "services/flight_api.py", line 120, in normalize\n  res["price"] += 1'

    inc1 = agent.detect_incident(
        subsystem=SUBSYSTEM_SEARCH,
        error=err,
        status_code=500,
        traceback_str=tb
    )
    assert inc1 is not None
    inc_id = inc1["incident_id"]
    assert inc1["occurrences"] == 1

    # Second occurrence of the exact same error
    inc2 = agent.detect_incident(
        subsystem=SUBSYSTEM_SEARCH,
        error=err,
        status_code=500,
        traceback_str=tb
    )
    assert inc2 is not None
    assert inc2["incident_id"] == inc_id
    assert inc2["occurrences"] == 2

    # Verify only 1 incident row exists in database
    incidents = agent.get_incidents()
    assert len(incidents) == 1
    assert incidents[0]["occurrences"] == 2


# ============================================================
# TEST 2: EXPECTED VALIDATION ERRORS (400) NOT TREATED AS BUGS
# ============================================================
def test_expected_validation_errors_are_not_treated_as_bugs():
    """Verify normal 4xx user input errors do not create software defect incidents."""
    # 400 Bad Request
    res_400 = agent.detect_incident(
        subsystem=SUBSYSTEM_VALIDATION,
        error="Invalid IATA code: XYZ123",
        status_code=400,
        traceback_str=""
    )
    assert res_400 is None

    # 422 Unprocessable Entity
    res_422 = agent.detect_incident(
        subsystem=SUBSYSTEM_VALIDATION,
        error="Return date cannot precede departure date",
        status_code=422,
        traceback_str=""
    )
    assert res_422 is None

    # No incidents created
    assert len(agent.get_incidents()) == 0


# ============================================================
# TEST 3: PROVIDER QUOTA EXHAUSTION CLASSIFIED AS EXTERNAL/QUOTA
# ============================================================
def test_provider_quota_exhaustion_classified_without_code_changes(monkeypatch):
    """Verify 429 quota exhaustion is classified as external/quota and never triggers code repair."""
    email_mock = MagicMock(return_value=True)
    monkeypatch.setattr("services.email_service.send_email", email_mock)

    quota_err = "HTTP 429 Client Error: Monthly search limit reached for FlightAPI plan"
    inc = agent.detect_incident(
        subsystem=SUBSYSTEM_PROVIDER,
        error=quota_err,
        status_code=429,
        traceback_str='File "services/providers/flightapi_provider.py", line 85'
    )

    assert inc is not None
    assert inc["category"] == CAT_QUOTA_EXHAUSTED
    assert inc["severity"] in (SEV_HIGH, SEV_CRITICAL)
    assert inc["needs_human_approval"] == 1
    assert inc["status"] == STATE_ESCALATED

    # Verify repair was NOT attempted (no repairs recorded)
    detail = agent.get_incident_detail(inc["incident_id"])
    assert len(detail["repairs"]) == 0

    # Verify escalation email was dispatched
    assert email_mock.called


# ============================================================
# TEST 4: REPAIR PROPOSAL VALIDATED BEFORE APPLYING
# ============================================================
def test_repair_proposal_validated_against_safety_policy():
    """Verify forbidden files (.env, db.py, security.py) and non-allowlisted files cannot be planned for repair."""
    # Forbidden file: .env
    forbidden_diagnosis = {
        "can_repair": True,
        "target_file": ".env",
        "target_line": 5
    }
    inc = agent.detect_incident(
        subsystem=SUBSYSTEM_WEB,
        error=KeyError("SECRET_KEY"),
        status_code=500,
        traceback_str='File ".env", line 5'
    )
    plan = agent.plan_repair(inc["incident_id"], forbidden_diagnosis)
    assert plan is None

    # Forbidden file: security.py
    security_diagnosis = {
        "can_repair": True,
        "target_file": "security.py",
        "target_line": 20
    }
    plan_sec = agent.plan_repair(inc["incident_id"], security_diagnosis)
    assert plan_sec is None


# ============================================================
# TEST 5: REPAIR ATTEMPTS PRESERVE UNCOMMITTED USER CHANGES
# ============================================================
def test_repair_attempts_preserve_uncommitted_user_changes(monkeypatch):
    """Verify the agent will not overwrite files with uncommitted user modifications."""
    # Mock subprocess.run to simulate git status reporting uncommitted edits
    orig_run = agent._execute_sql

    def mock_run_cmd(*args, **kwargs):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = " M validation.py\n"
        return mock_res

    monkeypatch.setattr("subprocess.run", mock_run_cmd)

    inc = agent.detect_incident(
        subsystem=SUBSYSTEM_VALIDATION,
        error=ValueError("broken regex in validation"),
        status_code=500,
        traceback_str='File "validation.py", line 40'
    )

    plan = {
        "target_file": "validation.py",
        "abs_target": os.path.join(agent.workspace_root, "validation.py"),
        "plan_description": "fix regex",
        "new_content": "# modified"
    }

    applied, backup_path, attempt_num = agent.apply_repair(inc["incident_id"], plan)
    assert applied is False
    assert backup_path is None

    # Incident should be transitioned to rolled_back / escalated
    detail = agent.get_incident_detail(inc["incident_id"])
    assert detail["status"] in (STATE_ROLLED_BACK, STATE_ESCALATED)


# ============================================================
# TEST 6: PASSING TESTS PERMIT PATCH MARKED VERIFIED (NO DEPLOY)
# ============================================================
def test_passing_tests_permit_patch_marked_verified_locally():
    """Verify a successfully tested patch transitions to resolved, without pushing or deploying."""
    test_file = os.path.join(agent.workspace_root, "validation.py")
    backup_file = f"{test_file}.bak.test"

    # Create dummy backup
    with open(backup_file, "w", encoding="utf-8") as f:
        f.write("# backup")

    inc = agent.detect_incident(
        subsystem=SUBSYSTEM_VALIDATION,
        error=TypeError("Minor validation type error"),
        status_code=500,
        traceback_str='File "validation.py", line 25'
    )

    plan = {
        "target_file": "validation.py",
        "abs_target": test_file,
        "plan_description": "Safe validation type cast"
    }

    # Custom test command that succeeds (exit code 0)
    success_cmd = [sys.executable, "-c", "import sys; sys.exit(0)"]

    verified = agent.verify_repair(inc["incident_id"], plan, backup_file, custom_test_cmd=success_cmd)
    assert verified is True

    detail = agent.get_incident_detail(inc["incident_id"])
    assert detail["status"] == STATE_RESOLVED
    assert "Deployment remains operator-approved" in detail["resolution_notes"]


# ============================================================
# TEST 7: FAILED TESTS CAUSE AUTOMATIC PATCH ROLLBACK
# ============================================================
def test_failed_tests_cause_automatic_patch_rollback():
    """Verify failing tests cleanly roll back modified files and record rolled_back state."""
    target_file = os.path.join(agent.workspace_root, "validation.py")
    backup_file = f"{target_file}.bak.rollbacktest"

    # Read original content
    with open(target_file, "r", encoding="utf-8") as f:
        original_content = f.read()

    # Create backup with original content
    with open(backup_file, "w", encoding="utf-8") as f:
        f.write(original_content)

    try:
        # Corrupt target file to simulate bad patch
        with open(target_file, "w", encoding="utf-8") as f:
            f.write("# Corrupted test code\n")

        inc = agent.detect_incident(
            subsystem=SUBSYSTEM_VALIDATION,
            error=RuntimeError("Validation defect"),
            status_code=500,
            traceback_str='File "validation.py", line 50'
        )

        plan = {
            "target_file": "validation.py",
            "abs_target": target_file,
            "plan_description": "Faulty validation fix"
        }

        # Verification fails due to failing test command
        fail_cmd = [sys.executable, "-c", "import sys; sys.exit(1)"]
        verified = agent.verify_repair(inc["incident_id"], plan, backup_file, custom_test_cmd=fail_cmd)
        assert verified is False

        # Check that file was restored to original content
        with open(target_file, "r", encoding="utf-8") as f:
            restored_content = f.read()
        assert restored_content == original_content

        detail = agent.get_incident_detail(inc["incident_id"])
        assert detail["status"] in (STATE_ROLLED_BACK, STATE_ESCALATED)
    finally:
        # Safeguard restoration
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(original_content)
        if os.path.exists(backup_file):
            try:
                os.remove(backup_file)
            except Exception:
                pass


# ============================================================
# TEST 8: UNRESOLVED INCIDENTS TRIGGER OWNER ESCALATION EMAIL
# ============================================================
def test_unresolved_incidents_trigger_owner_escalation_email(monkeypatch):
    """Verify unresolved incidents dispatch an actionable email alert."""
    dispatched_emails = []

    def mock_send(to_email, subject, text_body, html_body=None):
        dispatched_emails.append({
            "to": to_email,
            "subject": subject,
            "text": text_body,
            "html": html_body
        })
        return True

    monkeypatch.setattr("services.email_service.send_email", mock_send)
    monkeypatch.setenv("OWNER_ALERT_EMAIL", "principal_sre@skytracker.luxury")

    inc = agent.detect_incident(
        subsystem=SUBSYSTEM_SEARCH,
        error=ZeroDivisionError("division by zero in fare weight calculation"),
        status_code=500,
        traceback_str='File "services/flight_api.py", line 95'
    )

    # Manually escalate
    agent.escalate_incident(inc["incident_id"], reason="Manual escalation for complex defect")

    assert len(dispatched_emails) >= 1
    email = dispatched_emails[-1]
    assert email["to"] == "principal_sre@skytracker.luxury"
    assert "[SkyTracker Alert]" in email["subject"]
    assert inc["incident_id"] in email["text"]
    assert "Recommended Operator Action" in email["text"]


# ============================================================
# TEST 9: EMAIL FAILURE RECORDED WITHOUT CRASHING APP
# ============================================================
def test_email_failure_recorded_without_crashing(monkeypatch):
    """Verify SMTP exceptions or failures are logged and recorded in database without raising."""
    def mock_send_fail(to_email, subject, text_body, html_body=None):
        raise ConnectionRefusedError("SMTP server down: port 587 unreachable")

    monkeypatch.setattr("services.email_service.send_email", mock_send_fail)

    inc = agent.detect_incident(
        subsystem=SUBSYSTEM_WEB,
        error=RuntimeError("Web crash"),
        status_code=500
    )

    # Should not raise exception
    res = agent.escalate_incident(inc["incident_id"], reason="Testing resilient failure")
    assert res is False

    # Check database recorded the escalation failure
    detail = agent.get_incident_detail(inc["incident_id"])
    assert len(detail["escalations"]) >= 1
    esc = detail["escalations"][-1]
    assert esc["status"] == "failed"
    assert "SMTP server down" in esc["error_detail"]


# ============================================================
# TEST 10: DUPLICATE INCIDENTS DO NOT SPAM EMAILS DURING COOLDOWN
# ============================================================
def test_duplicate_incidents_do_not_spam_emails_during_cooldown(monkeypatch):
    """Verify cooldown period suppresses duplicate emails for the same incident."""
    send_count = 0

    def mock_send(*args, **kwargs):
        nonlocal send_count
        send_count += 1
        return True

    monkeypatch.setattr("services.email_service.send_email", mock_send)
    monkeypatch.setenv("INCIDENT_ALERT_COOLDOWN_SECONDS", "3600")

    inc = agent.detect_incident(
        subsystem=SUBSYSTEM_PROVIDER,
        error="FlightAPI Search limit exhausted",
        status_code=429
    )
    inc_id = inc["incident_id"]

    # First alert should send
    assert send_count == 1

    # Second immediate escalation attempt for the same incident
    res2 = agent.escalate_incident(inc_id, reason="Repeated event")
    assert res2 is False  # Suppressed by cooldown!
    assert send_count == 1  # No second email sent!


# ============================================================
# TEST 11: REPAIR-ATTEMPT LIMITS PREVENT INFINITE LOOPS
# ============================================================
def test_repair_attempt_limits_prevent_infinite_loops(monkeypatch):
    """Verify agent stops attempting repairs once max_attempts (default 2) is reached."""
    monkeypatch.setenv("SELF_HEALING_MAX_ATTEMPTS", "2")

    inc = agent.detect_incident(
        subsystem=SUBSYSTEM_VALIDATION,
        error=ValueError("Recurring error"),
        status_code=500,
        traceback_str='File "validation.py", line 10'
    )
    inc_id = inc["incident_id"]

    diagnosis = {
        "can_repair": True,
        "target_file": "validation.py",
        "target_line": 10
    }

    # Simulate 2 prior failed attempts
    conn = db()
    try:
        if DATABASE_URL:
            conn.execute("UPDATE incidents SET attempts_count = 2 WHERE incident_id = %s", (inc_id,))
        else:
            conn.execute("UPDATE incidents SET attempts_count = 2 WHERE incident_id = ?", (inc_id,))
        if hasattr(conn, "commit"):
            conn.commit()
    finally:
        conn.close()

    # Plan should be rejected because attempts >= max_attempts
    plan = agent.plan_repair(inc_id, diagnosis)
    assert plan is None

    detail = agent.get_incident_detail(inc_id)
    assert detail["needs_human_approval"] == 1
    assert detail["status"] == STATE_ESCALATED


# ============================================================
# TEST 12: CONCURRENT WORKERS DO NOT REPAIR SIMULTANEOUSLY
# ============================================================
def test_concurrent_workers_atomic_repair_locking():
    """Verify only one worker can acquire fixing status; concurrent attempts are rejected."""
    inc = agent.detect_incident(
        subsystem=SUBSYSTEM_VALIDATION,
        error=ValueError("Race condition test error"),
        status_code=500,
        traceback_str='File "validation.py", line 15'
    )
    inc_id = inc["incident_id"]

    plan = {
        "target_file": "validation.py",
        "abs_target": os.path.join(agent.workspace_root, "validation.py"),
        "plan_description": "Concurrent fix test",
        "new_content": "# dummy"
    }

    # Worker 1 transitions status to 'fixing'
    agent._update_status(inc_id, STATE_FIXING)

    # Worker 2 attempts to apply repair while Worker 1 is already fixing
    applied, backup, attempt = agent.apply_repair(inc_id, plan)
    assert applied is False
    assert backup is None
    assert attempt is None


# ============================================================
# TEST 13: SECRETS, API KEYS, PASSWORDS, AND EMAILS REDACTED
# ============================================================
def test_evidence_sanitization_scrubs_all_credentials_and_pii(monkeypatch):
    """Verify keys, passwords, bearer tokens, and customer emails are thoroughly sanitized."""
    monkeypatch.setenv("SECRET_KEY", "super-secret-crypto-key-123456789")
    monkeypatch.setenv("FLIGHTAPI_API_KEY", "live-flightapi-key-abcde")

    dirty_log = (
        "Error in https://api.flightapi.io/onewaytrip/live-flightapi-key-abcde/DEL/BOM/2026-11-01 "
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token "
        "password='MySecretPassword123' "
        "Customer email: john.doe@luxurytravel.com "
        "SECRET_KEY=super-secret-crypto-key-123456789"
    )

    clean = sanitize_evidence_text(dirty_log)

    assert "live-flightapi-key-abcde" not in clean
    assert "[REDACTED]" in clean
    assert "MySecretPassword123" not in clean
    assert "super-secret-crypto-key-123456789" not in clean
    assert "john.doe@luxurytravel.com" not in clean
    assert "jo***@luxurytravel.com" in clean or "[EMAIL_REDACTED]" in clean


# ============================================================
# TEST 14: UNAUTHORIZED USERS CANNOT ACCESS OPERATOR ENDPOINTS
# ============================================================
def test_unauthorized_access_to_diagnostics_endpoints_rejected(client, monkeypatch):
    """Verify operator incident endpoints reject requests lacking valid admin authentication."""
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "secure-sre-admin-token-xyz")

    # Unauthenticated GET
    res = client.get("/api/diagnostics/incidents")
    assert res.status_code == 401

    # Wrong token
    res_bad = client.get("/api/diagnostics/incidents", headers={"Authorization": "Bearer wrong-token"})
    assert res_bad.status_code == 401

    # Valid token
    res_ok = client.get("/api/diagnostics/incidents", headers={"Authorization": "Bearer secure-sre-admin-token-xyz"})
    assert res_ok.status_code == 200
    data = res_ok.get_json()
    assert data["status"] == "success"
    assert "reliability_summary" in data


# ============================================================
# TEST 15: AGENT CANNOT BYPASS API GUARDIAN CONTROLS
# ============================================================
def test_agent_cannot_bypass_guardian_budget_controls(monkeypatch):
    """Verify that when API Guardian blocks a request, it is respected and not circumvented."""
    monkeypatch.setenv("SERPAPI_MONTHLY_BUDGET", "0")  # Hard exhausted

    # Check budget via guardian
    allowed, reason, stats = guardian.check_budget(PROVIDER_SERPAPI, CALLER_USER_SEARCH)
    assert allowed is False

    # Classification must identify this as quota/guardian protection, not an application bug to rewrite
    res = agent.classify_failure(
        subsystem=SUBSYSTEM_SEARCH,
        error=f"Request blocked by API Guardian: {reason}",
        status_code=429
    )
    assert res["category"] == CAT_QUOTA_EXHAUSTED
    assert res["can_auto_repair"] is False


# ============================================================
# TEST 16: INTEGRATION WITH FLASK 500 AND TELEMETRY ENDPOINTS
# ============================================================
def test_flask_telemetry_and_error_handling_integration(client, monkeypatch):
    """Verify frontend telemetry endpoint records client errors safely."""
    telemetry_payload = {
        "message": "Uncaught TypeError: Cannot read property of undefined in search_box.js",
        "source": "https://skytracker.luxury/static/js/app.js",
        "lineno": 142,
        "colno": 12,
        "stack": "TypeError: Cannot read property of undefined\n  at initSearch (app.js:142:12)"
    }

    res = client.post("/api/telemetry/errors", json=telemetry_payload)
    assert res.status_code == 200
    assert res.get_json()["status"] == "recorded"

    # Verify incident was created
    incidents = agent.get_incidents(category=CAT_APPLICATION_DEFECT)
    assert len(incidents) >= 1
    assert incidents[0]["subsystem"] == SUBSYSTEM_FRONTEND
    # Verify client telemetry can NEVER authorize auto repair
    assert incidents[0]["needs_human_approval"] == 1


# ============================================================
# TEST 17: CLIENT TELEMETRY CAN NEVER AUTHORIZE AUTO REPAIR
# ============================================================
def test_client_telemetry_cannot_authorize_code_modifications(client):
    """Confirm client telemetry cannot authorize code changes or trigger auto repair."""
    telemetry_payload = {
        "message": "Uncaught SyntaxError in client bundle",
        "source": "https://skytracker.luxury/static/js/app.js",
        "lineno": 10,
        "colno": 5,
        "stack": "SyntaxError: Unexpected token <"
    }

    res = client.post("/api/telemetry/errors", json=telemetry_payload)
    assert res.status_code == 200

    # Classification check
    classification = agent.classify_failure(
        subsystem=SUBSYSTEM_FRONTEND,
        error="Client JS Error",
        status_code=200,
        context={"client_telemetry": True}
    )
    assert classification["can_auto_repair"] is False


# ============================================================
# TEST 18: FORBIDDEN FILES CANNOT BE TARGETED FOR REPAIR
# ============================================================
def test_forbidden_files_cannot_be_targeted_for_repair():
    """Verify security, auth, database, guardian, and app files are strictly forbidden from repair."""
    from services.self_healing_agent import FORBIDDEN_FILES

    strictly_forbidden = [
        "services/api_guardian.py",
        "app.py",
        "security.py",
        "db.py",
        ".env",
        ".env.example",
        "render.yaml",
        "Dockerfile",
        "Procfile",
        "conftest.py",
        "pytest.ini"
    ]

    for forbidden_file in strictly_forbidden:
        assert forbidden_file in FORBIDDEN_FILES

        # Attempting to plan a repair targeting this file must return None
        plan = agent.plan_repair(
            incident_id="inc_forbidden_test",
            diagnosis={
                "target_file": forbidden_file,
                "can_repair": True
            }
        )
        assert plan is None


# ============================================================
# TEST 19: ATOMIC REPAIR CLAIM REJECTS WHEN MAX ATTEMPTS REACHED
# ============================================================
def test_attempts_count_atomic_rejection_at_max():
    """Verify database atomic claim rejects workers when attempts_count >= max_attempts."""
    test_inc_id = f"inc_{uuid.uuid4().hex[:12]}"
    agent._execute_sql(
        query_pg="""
            INSERT INTO incidents (incident_id, fingerprint, title, subsystem, category, severity, status, attempts_count, max_attempts, created_at, updated_at)
            VALUES (%s, 'fp_atomic_max', 'Test Max Attempts', 'web', 'application_defect', 'medium', 'detected', 2, 2, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """,
        query_sqlite="""
            INSERT INTO incidents (incident_id, fingerprint, title, subsystem, category, severity, status, attempts_count, max_attempts, created_at, updated_at)
            VALUES (?, 'fp_atomic_max', 'Test Max Attempts', 'web', 'application_defect', 'medium', 'detected', 2, 2, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """,
        params=(test_inc_id,)
    )

    plan = {
        "incident_id": test_inc_id,
        "target_file": "validation.py",
        "abs_target": os.path.abspath("validation.py"),
        "plan_description": "Test patch",
        "patch_type": "isolated_source_patch"
    }

    # apply_repair must be rejected atomically by SQL condition attempts_count < max_attempts
    success, backup, repair_id = agent.apply_repair(test_inc_id, plan)
    assert success is False
    assert backup is None
    assert repair_id is None


# ============================================================
# TEST 20: PRODUCTION MODE STRICTLY DISABLES AGENT BY DEFAULT
# ============================================================
def test_production_mode_disables_agent_by_default(monkeypatch):
    """Verify that in production environments, the agent is disabled unless explicitly enabled."""
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("ENABLE_SELF_HEALING_AGENT", raising=False)

    test_agent = SelfHealingAgent()
    assert test_agent._is_agent_enabled() is False

    # Explicit override enables it
    monkeypatch.setenv("ENABLE_SELF_HEALING_AGENT", "true")
    assert test_agent._is_agent_enabled() is True
