import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
DB_PATH = os.getenv("DB_PATH", "flights.db")


def _is_production():
    """Determine if running in a production environment."""
    return bool(
        os.getenv("FLASK_ENV") == "production"
        or os.getenv("RENDER")
        or os.getenv("ENV") == "production"
        or os.getenv("ENVIRONMENT") == "production"
    )


def db():
    """Create and return a database connection."""
    database_url = os.getenv("DATABASE_URL")

    # Production database enforcement: prevent silent SQLite fallback
    if _is_production():
        if not database_url or not database_url.strip():
            raise RuntimeError(
                "CRITICAL: DATABASE_URL environment variable must be set in production. "
                "Silent fallback to SQLite is prohibited."
            )
        clean_url = database_url.strip().lower()
        if "username:password@hostname" in clean_url or "user:password@hostname" in clean_url:
            raise RuntimeError(
                "CRITICAL: DATABASE_URL is configured with an unconfigured placeholder. "
                "A valid PostgreSQL connection string is required in production."
            )

    # Production: PostgreSQL
    if database_url:
        import importlib
        try:
            psycopg = importlib.import_module("psycopg")
            dict_row = importlib.import_module("psycopg.rows").dict_row
        except ImportError as err:
            raise RuntimeError(
                f"PostgreSQL driver 'psycopg' is required when DATABASE_URL is configured: {err}"
            )

        conn = psycopg.connect(
            database_url.strip(),
            sslmode="require",
            row_factory=dict_row
        )
        return conn

    # Local development: SQLite
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create required database tables if they don't exist."""
    database_url = os.getenv("DATABASE_URL")
    if _is_production() and (not database_url or not database_url.strip()):
        raise RuntimeError(
            "CRITICAL: DATABASE_URL environment variable must be set in production. "
            "Silent fallback to SQLite is prohibited."
        )

    conn = db()

    if database_url:
        # PostgreSQL
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked_flights (
                id BIGSERIAL PRIMARY KEY,
                departure TEXT NOT NULL,
                arrival TEXT NOT NULL,
                outbound_date TEXT NOT NULL,
                return_date TEXT,
                target_price DOUBLE PRECISION NOT NULL,
                email TEXT NOT NULL,
                lowest_price DOUBLE PRECISION,
                last_price DOUBLE PRECISION,
                created_at TEXT NOT NULL,
                target_alert_sent INTEGER DEFAULT 0,
                last_interrupted_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id BIGSERIAL PRIMARY KEY,
                tracked_id BIGINT NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                checked_at TEXT NOT NULL,
                FOREIGN KEY (tracked_id)
                    REFERENCES tracked_flights(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS contact_messages (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                subject TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                is_live INTEGER DEFAULT 1
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_quota_logs (
                id BIGSERIAL PRIMARY KEY,
                provider_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                caller_type TEXT NOT NULL,
                cache_hit INTEGER DEFAULT 0,
                status_code INTEGER DEFAULT 200,
                error_category TEXT,
                cost INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_circuit_state (
                provider_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                failure_count INTEGER DEFAULT 0,
                last_failure_at TEXT,
                opened_at TEXT,
                cooldown_until TEXT,
                reason TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS cron_locks (
                job_name TEXT PRIMARY KEY,
                locked_at TEXT NOT NULL,
                locked_by TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_reservations (
                reservation_id TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                caller_type TEXT NOT NULL,
                priority INTEGER DEFAULT 2,
                cost_units INTEGER DEFAULT 1,
                billing_unit TEXT DEFAULT 'credits',
                state TEXT NOT NULL,
                idempotency_key TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                metadata_json TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_alerts_log (
                id BIGSERIAL PRIMARY KEY,
                alert_type TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                details_json TEXT,
                sent_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_operator_audit (
                id BIGSERIAL PRIMARY KEY,
                operator_id TEXT NOT NULL,
                action TEXT NOT NULL,
                provider_id TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id BIGSERIAL PRIMARY KEY,
                incident_id TEXT UNIQUE NOT NULL,
                fingerprint TEXT NOT NULL,
                title TEXT NOT NULL,
                subsystem TEXT NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                error_type TEXT,
                sanitized_message TEXT,
                sanitized_traceback TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                occurrences INTEGER DEFAULT 1,
                attempts_count INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 2,
                needs_human_approval INTEGER DEFAULT 0,
                resolution_notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS incident_transitions (
                id BIGSERIAL PRIMARY KEY,
                incident_id TEXT NOT NULL,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                reason TEXT,
                actor TEXT DEFAULT 'self_healing_agent',
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS incident_repairs (
                id BIGSERIAL PRIMARY KEY,
                incident_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                plan_description TEXT,
                target_files TEXT,
                patch_diff TEXT,
                verification_output TEXT,
                success INTEGER DEFAULT 0,
                rolled_back INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS incident_escalations (
                id BIGSERIAL PRIMARY KEY,
                incident_id TEXT NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                status TEXT NOT NULL,
                error_detail TEXT,
                sent_at TEXT NOT NULL
            )
        """)

    else:
        # SQLite
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked_flights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                departure TEXT NOT NULL,
                arrival TEXT NOT NULL,
                outbound_date TEXT NOT NULL,
                return_date TEXT,
                target_price REAL NOT NULL,
                email TEXT NOT NULL,
                lowest_price REAL,
                last_price REAL,
                created_at TEXT NOT NULL,
                target_alert_sent INTEGER DEFAULT 0,
                last_interrupted_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracked_id INTEGER NOT NULL,
                price REAL NOT NULL,
                checked_at TEXT NOT NULL,
                FOREIGN KEY (tracked_id)
                    REFERENCES tracked_flights(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS contact_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                subject TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                is_live INTEGER DEFAULT 1
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_quota_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                caller_type TEXT NOT NULL,
                cache_hit INTEGER DEFAULT 0,
                status_code INTEGER DEFAULT 200,
                error_category TEXT,
                cost INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_circuit_state (
                provider_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                failure_count INTEGER DEFAULT 0,
                last_failure_at TEXT,
                opened_at TEXT,
                cooldown_until TEXT,
                reason TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS cron_locks (
                job_name TEXT PRIMARY KEY,
                locked_at TEXT NOT NULL,
                locked_by TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_reservations (
                reservation_id TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                caller_type TEXT NOT NULL,
                priority INTEGER DEFAULT 2,
                cost_units INTEGER DEFAULT 1,
                billing_unit TEXT DEFAULT 'credits',
                state TEXT NOT NULL,
                idempotency_key TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                metadata_json TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_alerts_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                details_json TEXT,
                sent_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_operator_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operator_id TEXT NOT NULL,
                action TEXT NOT NULL,
                provider_id TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT UNIQUE NOT NULL,
                fingerprint TEXT NOT NULL,
                title TEXT NOT NULL,
                subsystem TEXT NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                error_type TEXT,
                sanitized_message TEXT,
                sanitized_traceback TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                occurrences INTEGER DEFAULT 1,
                attempts_count INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 2,
                needs_human_approval INTEGER DEFAULT 0,
                resolution_notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS incident_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                reason TEXT,
                actor TEXT DEFAULT 'self_healing_agent',
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS incident_repairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                plan_description TEXT,
                target_files TEXT,
                patch_diff TEXT,
                verification_output TEXT,
                success INTEGER DEFAULT 0,
                rolled_back INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS incident_escalations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                status TEXT NOT NULL,
                error_detail TEXT,
                sent_at TEXT NOT NULL
            )
        """)

    # Indices for high-frequency quota, reservation, and incident lookups
    for idx_stmt in (
        "CREATE INDEX IF NOT EXISTS idx_res_prov_state ON api_reservations(provider_id, state)",
        "CREATE INDEX IF NOT EXISTS idx_res_idemp ON api_reservations(idempotency_key)",
        "CREATE INDEX IF NOT EXISTS idx_quota_logs_prov_time ON api_quota_logs(provider_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_incidents_fp_status ON incidents(fingerprint, status)",
        "CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status)",
        "CREATE INDEX IF NOT EXISTS idx_inc_trans_inc_id ON incident_transitions(incident_id)",
        "CREATE INDEX IF NOT EXISTS idx_inc_repairs_inc_id ON incident_repairs(incident_id)",
        "CREATE INDEX IF NOT EXISTS idx_inc_esc_inc_time ON incident_escalations(incident_id, sent_at)",
    ):
        try:
            conn.execute(idx_stmt)
        except Exception:
            pass

    # Backward-compatible migrations for tracked_flights columns
    for col in ("last_attempted_at", "last_successful_at", "last_interrupted_at"):
        try:
            conn.execute(f"ALTER TABLE tracked_flights ADD COLUMN {col} TEXT")
        except Exception:
            pass

    conn.commit()
    conn.close()