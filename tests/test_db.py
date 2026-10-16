import sqlite3

import db


def test_init_db_creates_tables(tmp_path, monkeypatch):
    test_database = tmp_path / "test_flights.db"

    monkeypatch.setattr(db, "DB_PATH", str(test_database))

    db.init_db()

    conn = sqlite3.connect(test_database)

    tables = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
    """).fetchall()

    table_names = {table[0] for table in tables}

    conn.close()

    assert "tracked_flights" in table_names
    assert "price_history" in table_names
    assert "users" in table_names


def test_db_returns_connection(tmp_path, monkeypatch):
    test_database = tmp_path / "test_flights.db"

    monkeypatch.setattr(db, "DB_PATH", str(test_database))

    connection = db.db()

    assert connection is not None

    connection.close()


def test_production_database_url_required(monkeypatch):
    """Ensure missing DATABASE_URL in production raises RuntimeError and rejects SQLite fallback."""
    import pytest

    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="Silent fallback to SQLite is prohibited"):
        db.db()


def test_production_database_url_placeholder_rejected(monkeypatch):
    """Ensure placeholder DATABASE_URL in production raises RuntimeError."""
    import pytest

    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://username:password@hostname:5432/dbname")

    with pytest.raises(RuntimeError, match="unconfigured placeholder"):
        db.db()


def test_production_init_db_requires_database_url(monkeypatch):
    """Ensure init_db in production requires DATABASE_URL."""
    import pytest

    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="Silent fallback to SQLite is prohibited"):
        db.init_db()