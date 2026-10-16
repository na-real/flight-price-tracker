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
    conn = sqlite3.connect(DB_PATH)
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
                target_alert_sent INTEGER DEFAULT 0
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
                target_alert_sent INTEGER DEFAULT 0
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

    conn.commit()
    conn.close()