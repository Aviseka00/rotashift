from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_live(client: TestClient):
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_meta_shifts_no_db(client: TestClient):
    r = client.get("/api/meta/shifts")
    assert r.status_code == 200
    data = r.json()
    assert "shifts" in data and "A" in data["shifts"]


def test_meta_seed_departments(client: TestClient):
    r = client.get("/api/meta/seed-departments")
    assert r.status_code == 200
    names = r.json().get("default_department_names") or []
    assert isinstance(names, list)
    assert "rota" in names


def test_index_html(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in (r.headers.get("content-type") or "")


def test_head_index(client: TestClient):
    r = client.head("/")
    assert r.status_code == 200
