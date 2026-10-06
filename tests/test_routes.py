import pytest

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True

    with app.test_client() as client:
        yield client


def test_airports_route(client):
    response = client.get("/api/airports?q=delhi")

    assert response.status_code == 200

    data = response.get_json()

    assert isinstance(data, list)
    assert len(data) > 0


def test_search_route(client, monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)

    response = client.post(
        "/api/search",
        json={
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": "2026-11-15",
        },
    )

    assert response.status_code == 200

    data = response.get_json()

    assert isinstance(data, dict)
    assert "results" in data
    assert "search_airports" in data

    results = data["results"]

    assert isinstance(results, list)
    assert len(results) > 0

    assert "price" in results[0]
    assert "airline" in results[0]


def test_track_route(client, monkeypatch, tmp_path):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)

    import db

    test_database = tmp_path / "test_flights.db"

    monkeypatch.setattr(db, "DB_PATH", str(test_database))

    db.init_db()

    response = client.post(
        "/api/track",
        json={
            "departure": "DEL",
            "arrival": "BOM",
            "outbound_date": "2026-11-15",
            "target_price": "5000",
            "email": "test@example.com",
        },
    )

    assert response.status_code == 200

    data = response.get_json()

    assert "message" in data