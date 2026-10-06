import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "flights.db")


def db():
    """Create and return a SQLite database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create required database tables if they don't exist."""

    conn = db()

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
            FOREIGN KEY (tracked_id) REFERENCES tracked_flights(id)
        )
    """)

    conn.commit()
    conn.close()
