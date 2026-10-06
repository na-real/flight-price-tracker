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


def test_db_returns_connection(tmp_path, monkeypatch):
    test_database = tmp_path / "test_flights.db"

    monkeypatch.setattr(db, "DB_PATH", str(test_database))

    connection = db.db()

    assert connection is not None

    connection.close()