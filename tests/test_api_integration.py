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


def test_admin_can_bulk_delete_tasks(client: TestClient, require_mongo, admin_headers: dict[str, str]):
    departments = client.get("/api/departments").json()["departments"]
    department_id = departments[0]["id"]
    task_ids = []
    for suffix in ("one", "two"):
        created = client.post(
            "/api/tasks",
            json={"title": f"Bulk delete {suffix}", "department_id": department_id},
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text
        task_ids.append(created.json()["id"])

    deleted = client.post(
        "/api/tasks/bulk-delete",
        json={"task_ids": task_ids, "department_id": department_id},
        headers=admin_headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] == 2


def test_manager_can_request_leave_and_shift_change_for_admin_approval(
    client: TestClient, require_mongo, admin_headers: dict[str, str], unique_employee_id: str
):
    department_id = client.get("/api/departments").json()["departments"][0]["id"]
    password = "manager-request-pass-9x"
    created = client.post(
        "/api/users",
        json={
            "employee_id": unique_employee_id,
            "password": password,
            "full_name": "Manager Request QA",
            "department_id": department_id,
            "role": "manager",
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    login = client.post("/api/auth/login", json={"employee_id": unique_employee_id, "password": password})
    assert login.status_code == 200, login.text
    manager_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    today = date.today().isoformat()

    leave = client.post(
        "/api/requests/leave",
        json={"start_date": today, "end_date": today, "reason": "Manager leave"},
        headers=manager_headers,
    )
    shift = client.post(
        "/api/requests/shift-change",
        json={"date": today, "from_shift": "A", "to_shift": "B", "reason": "Manager swap"},
        headers=manager_headers,
    )
    assert leave.status_code == 200, leave.text
    assert shift.status_code == 200, shift.text
    own_leave = client.get("/api/requests/leave", headers=manager_headers).json()["requests"]
    own_shift = client.get("/api/requests/shift-change", headers=manager_headers).json()["requests"]
    assert any(item["id"] == leave.json()["id"] and item["status"] == "pending" for item in own_leave)
    assert any(item["id"] == shift.json()["id"] and item["status"] == "pending" for item in own_shift)

    approved = client.patch(
        f"/api/requests/leave/{leave.json()['id']}/decide",
        json={"status": "approved"},
        headers=admin_headers,
    )
    assert approved.status_code == 200, approved.text


def test_register_requires_admin_approval(
    client: TestClient, require_mongo, unique_employee_id: str, admin_headers: dict[str, str]
):
    reg = {
        "employee_id": unique_employee_id,
        "password": "pytest-pass-9x",
        "full_name": "QA Flow",
        "department_name": "rota",
        "role": "employee",
    }
    r1 = client.post("/api/auth/register", json=reg)
    assert r1.status_code == 202, r1.text
    assert r1.json().get("pending") is True

    before_approval = client.post(
        "/api/auth/login",
        json={"employee_id": unique_employee_id, "password": "pytest-pass-9x"},
    )
    assert before_approval.status_code == 400

    approval = client.post(
        f"/api/auth/registration-requests/{r1.json()['request_id']}/approve",
        headers=admin_headers,
    )
    assert approval.status_code == 200, approval.text

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


def test_user_can_change_own_password(
    client: TestClient, require_mongo, unique_employee_id: str, admin_headers: dict[str, str]
):
    old_password = "pytest-old-pass-9x"
    new_password = "pytest-new-pass-8z"
    registration = client.post(
        "/api/auth/register",
        json={
            "employee_id": unique_employee_id,
            "password": old_password,
            "full_name": "Password QA",
            "department_name": "rota",
            "role": "employee",
        },
    )
    assert registration.status_code == 202, registration.text
    approval = client.post(
        f"/api/auth/registration-requests/{registration.json()['request_id']}/approve",
        headers=admin_headers,
    )
    assert approval.status_code == 200, approval.text
    login = client.post("/api/auth/login", json={"employee_id": unique_employee_id, "password": old_password})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    changed = client.post(
        "/api/auth/change-password",
        json={"current_password": old_password, "new_password": new_password},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert changed.status_code == 200, changed.text

    old_login = client.post("/api/auth/login", json={"employee_id": unique_employee_id, "password": old_password})
    assert old_login.status_code == 400
    new_login = client.post("/api/auth/login", json={"employee_id": unique_employee_id, "password": new_password})
    assert new_login.status_code == 200, new_login.text


def test_free_meet_directory_uses_saved_gmail_and_department_scope(
    client: TestClient, require_mongo, unique_employee_id: str, admin_headers: dict[str, str]
):
    department_id = client.get("/api/departments").json()["departments"][0]["id"]
    password = "meet-directory-pass-9x"
    gmail = f"{unique_employee_id.lower()}@gmail.com"
    created = client.post(
        "/api/users",
        json={
            "employee_id": unique_employee_id,
            "password": password,
            "full_name": "Meet Directory QA",
            "department_id": department_id,
            "role": "employee",
            "gmail": gmail,
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    login = client.post("/api/auth/login", json={"employee_id": unique_employee_id, "password": password})
    assert login.status_code == 200, login.text
    employee_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me = client.get("/api/auth/me", headers=employee_headers)
    assert me.status_code == 200
    assert me.json()["gmail"] == gmail

    directory = client.get("/api/users/meet-directory", headers=employee_headers)
    assert directory.status_code == 200, directory.text
    assert any(row["employee_id"] == unique_employee_id and row["gmail"] == gmail for row in directory.json()["users"])

    updated = client.patch("/api/users/me/gmail", json={"gmail": "updated.qa@gmail.com"}, headers=employee_headers)
    assert updated.status_code == 200, updated.text
    assert updated.json()["gmail"] == "updated.qa@gmail.com"


def test_meet_gmail_rejects_non_gmail_address(client: TestClient, require_mongo, auth_headers: dict[str, str]):
    response = client.patch(
        "/api/users/me/gmail", json={"gmail": "someone@example.com"}, headers=auth_headers
    )
    assert response.status_code == 422


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


def test_department_calendar_is_admin_only(client: TestClient, require_mongo, auth_headers: dict[str, str]):
    today = date.today().isoformat()
    response = client.get(
        "/api/shifts/calendar",
        params={"start": today, "end": today},
        headers=auth_headers,
    )
    assert response.status_code == 403


def test_local_assistant_answers_authenticated_user(client: TestClient, require_mongo, auth_headers: dict[str, str]):
    response = client.post(
        "/api/assistant/query",
        json={"message": "Show my tasks"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["intent"] == "tasks"
    assert "task" in data["answer"].lower()
