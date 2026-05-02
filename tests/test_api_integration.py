from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient


def test_health_ready_when_mongo(client: TestClient, require_mongo):
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ready"


def test_departments_list(client: TestClient, require_mongo):
    r = client.get("/api/departments")
    assert r.status_code == 200
    depts = r.json().get("departments") or []
    assert len(depts) >= 1
    assert any(d.get("name") == "rota" for d in depts)


def test_requests_leave_unauthenticated(client: TestClient, require_mongo):
    r = client.get("/api/requests/leave")
    assert r.status_code == 401


def test_tasks_health(client: TestClient, require_mongo):
    r = client.get("/api/tasks/health")
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_register_login_me(client: TestClient, require_mongo, unique_employee_id: str):
    reg = {
        "employee_id": unique_employee_id,
        "password": "pytest-pass-9x",
        "full_name": "QA Flow",
        "department_name": "rota",
        "role": "employee",
    }
    r1 = client.post("/api/auth/register", json=reg)
    assert r1.status_code == 200, r1.text

    r2 = client.post(
        "/api/auth/login",
        json={"employee_id": unique_employee_id, "password": "pytest-pass-9x"},
    )
    assert r2.status_code == 200, r2.text
    token = r2.json()["access_token"]

    r3 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r3.status_code == 200
    me = r3.json()
    assert me.get("employee_id") == unique_employee_id
    assert me.get("role") == "employee"


def test_shifts_table_employee(client: TestClient, require_mongo, auth_headers: dict[str, str]):
    today = date.today()
    start = (today - timedelta(days=today.weekday())).isoformat()
    end = (today + timedelta(days=13)).isoformat()
    r = client.get(
        "/api/shifts/table",
        params={"start": start, "end": end},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "dates" in data and "rows" in data
    assert isinstance(data["dates"], list)
