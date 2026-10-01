from __future__ import annotations

from datetime import date, timedelta
import uuid

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

    manager_leave_decide = client.patch(
        f"/api/requests/leave/{leave.json()['id']}/decide",
        json={"status": "approved"},
        headers=manager_headers,
    )
    manager_shift_decide = client.patch(
        f"/api/requests/shift-change/{shift.json()['id']}/decide",
        json={"status": "approved"},
        headers=manager_headers,
    )
    assert manager_leave_decide.status_code == 403
    assert manager_shift_decide.status_code == 403

    approved = client.patch(
        f"/api/requests/leave/{leave.json()['id']}/decide",
        json={"status": "approved"},
        headers=admin_headers,
    )
    assert approved.status_code == 200, approved.text


def test_user_can_request_swap_from_wo_or_leave(
    client: TestClient, require_mongo, admin_headers: dict[str, str], unique_employee_id: str
):
    department_id = client.get("/api/departments").json()["departments"][0]["id"]
    password = "swap-wo-leave-pass-9x"
    created = client.post(
        "/api/users",
        json={
            "employee_id": unique_employee_id,
            "password": password,
            "full_name": "Swap WO Leave QA",
            "department_id": department_id,
            "role": "employee",
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    login = client.post("/api/auth/login", json={"employee_id": unique_employee_id, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    today = date.today().isoformat()

    from_wo = client.post(
        "/api/requests/shift-change",
        json={"date": today, "from_shift": "WO", "to_shift": "A", "reason": "Work on week off"},
        headers=headers,
    )
    from_leave = client.post(
        "/api/requests/shift-change",
        json={"date": today, "from_shift": "L", "to_shift": "G", "reason": "Cancel leave for duty"},
        headers=headers,
    )
    assert from_wo.status_code == 200, from_wo.text
    assert from_leave.status_code == 200, from_leave.text


def test_cannot_swap_allocated_duty_to_week_off(
    client: TestClient, require_mongo, admin_headers: dict[str, str], unique_employee_id: str
):
    department_id = client.get("/api/departments").json()["departments"][0]["id"]
    password = "no-wo-on-duty-pass-9x"
    created = client.post(
        "/api/users",
        json={
            "employee_id": unique_employee_id,
            "password": password,
            "full_name": "No WO On Duty QA",
            "department_id": department_id,
            "role": "employee",
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    login = client.post("/api/auth/login", json={"employee_id": unique_employee_id, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    today = date.today().isoformat()
    bulk = client.post(
        "/api/shifts/bulk",
        json={
            "department_id": department_id,
            "assignments": [{"employee_id": unique_employee_id, "date": today, "shift_code": "G"}],
        },
        headers=admin_headers,
    )
    assert bulk.status_code == 200, bulk.text
    blocked = client.post(
        "/api/requests/shift-change",
        json={"date": today, "from_shift": "G", "to_shift": "WO", "reason": "Want week off"},
        headers=headers,
    )
    assert blocked.status_code == 400, blocked.text
    assert "Week off" in blocked.json()["detail"]
    assert "comp-off" in blocked.json()["detail"].lower()


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
    assert data.get("source") == "local"


def test_assistant_comp_off_stays_local(client: TestClient, require_mongo, auth_headers: dict[str, str]):
    response = client.post(
        "/api/assistant/query",
        json={"message": "How many comp-off days do I have?"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source"] == "local"
    assert "comp-off" in data["answer"].lower()


def test_assistant_answers_colleague_shift_by_first_name(
    client: TestClient, require_mongo, admin_headers: dict[str, str], unique_employee_id: str
):
    departments = client.get("/api/departments").json()["departments"]
    department_id = next((d["id"] for d in departments if d.get("name") == "rota"), departments[0]["id"])
    colleague_id = f"S{uuid.uuid4().hex[:10].upper()}"
    asker_id = unique_employee_id
    today = date.today().isoformat()
    created_colleague = client.post(
        "/api/users",
        json={
            "employee_id": colleague_id,
            "password": "pytest-pass-9x",
            "full_name": "Smruti Askrota",
            "department_id": department_id,
            "role": "employee",
        },
        headers=admin_headers,
    )
    assert created_colleague.status_code == 200, created_colleague.text
    created_asker = client.post(
        "/api/users",
        json={
            "employee_id": asker_id,
            "password": "pytest-pass-9x",
            "full_name": "Asker Colleague",
            "department_id": department_id,
            "role": "employee",
        },
        headers=admin_headers,
    )
    assert created_asker.status_code == 200, created_asker.text
    assigned = client.post(
        "/api/shifts/bulk",
        json={
            "department_id": department_id,
            "assignments": [{"employee_id": colleague_id, "date": today, "shift_code": "A"}],
        },
        headers=admin_headers,
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json().get("upserted") == 1, assigned.text
    login = client.post("/api/auth/login", json={"employee_id": asker_id, "password": "pytest-pass-9x"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = client.post(
        "/api/assistant/query",
        json={"message": "what is smruti askrota's shift today"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["intent"] == "schedule"
    assert "smruti" in data["answer"].lower()
    assert any("A" in (item.get("detail") or "") for item in data.get("items") or []), data


def test_tasks_activity_is_light(client: TestClient, require_mongo, auth_headers: dict[str, str]):
    response = client.get("/api/tasks/activity", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "changed" in body
    assert "items" in body
    assert isinstance(body["items"], list)


def test_employee_can_move_kanban_column(
    client: TestClient, require_mongo, admin_headers: dict[str, str], auth_headers: dict[str, str]
):
    departments = client.get("/api/departments").json()["departments"]
    department_id = next((d["id"] for d in departments if d.get("name") == "rota"), departments[0]["id"])
    created = client.post(
        "/api/tasks",
        json={"title": "Move me", "department_id": department_id, "column": "todo"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]
    moved = client.patch(
        f"/api/tasks/{task_id}",
        json={"column": "in_progress"},
        headers=auth_headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["column"] == "in_progress"
    blocked = client.patch(
        f"/api/tasks/{task_id}",
        json={"title": "Nope"},
        headers=auth_headers,
    )
    assert blocked.status_code == 403


def test_coverage_preview_and_manager_department_scope(
    client: TestClient, require_mongo, admin_headers: dict[str, str], unique_employee_id: str
):
    department_id = client.get("/api/departments").json()["departments"][0]["id"]
    today = date.today().isoformat()
    password = "coverage-mgr-pass-9x"
    created = client.post(
        "/api/users",
        json={
            "employee_id": unique_employee_id,
            "password": password,
            "full_name": "Coverage Manager QA",
            "department_id": department_id,
            "role": "manager",
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    login = client.post("/api/auth/login", json={"employee_id": unique_employee_id, "password": password})
    assert login.status_code == 200, login.text
    manager_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    leave = client.post(
        "/api/requests/leave",
        json={"start_date": today, "end_date": today, "reason": "Coverage check"},
        headers=manager_headers,
    )
    assert leave.status_code == 200, leave.text
    preview = client.get(
        "/api/requests/coverage-preview",
        params={"kind": "leave", "id": leave.json()["id"]},
        headers=manager_headers,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body.get("ok") is True
    assert body.get("days")

    dept_leave = client.get("/api/requests/leave?scope=department", headers=manager_headers)
    assert dept_leave.status_code == 200, dept_leave.text
    assert any(item["id"] == leave.json()["id"] for item in dept_leave.json()["requests"])


def test_comp_off_earn_avail_g_and_dual(
    client: TestClient, require_mongo, admin_headers: dict[str, str], unique_employee_id: str
):
    department_id = client.get("/api/departments").json()["departments"][0]["id"]
    password = "compoff-pass-9x"
    created = client.post(
        "/api/users",
        json={
            "employee_id": unique_employee_id,
            "password": password,
            "full_name": "Comp Off QA",
            "department_id": department_id,
            "role": "employee",
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    manager_id = f"M{uuid.uuid4().hex[:10].upper()}"
    manager_created = client.post(
        "/api/users",
        json={
            "employee_id": manager_id,
            "password": "compoff-mgr-pass-9x",
            "full_name": "Comp Off Manager QA",
            "department_id": department_id,
            "role": "manager",
        },
        headers=admin_headers,
    )
    assert manager_created.status_code == 200, manager_created.text
    manager_login = client.post(
        "/api/auth/login", json={"employee_id": manager_id, "password": "compoff-mgr-pass-9x"}
    )
    assert manager_login.status_code == 200, manager_login.text
    manager_headers = {"Authorization": f"Bearer {manager_login.json()['access_token']}"}
    login = client.post("/api/auth/login", json={"employee_id": unique_employee_id, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    work_day = date.today().isoformat()
    dual_day = (date.today() + timedelta(days=1)).isoformat()
    later = (date.today() + timedelta(days=3)).isoformat()

    earn = client.post(
        "/api/requests/comp-off/earn",
        json={"work_date": work_day, "earn_type": "worked_wo", "worked_shift": "G", "reason": "Came in on WO as G"},
        headers=headers,
    )
    assert earn.status_code == 200, earn.text
    pending_earn_table = client.get(
        f"/api/shifts/table?start={work_day}&end={work_day}",
        headers=headers,
    )
    assert pending_earn_table.status_code == 200, pending_earn_table.text
    pending_earn_row = next(
        r for r in pending_earn_table.json()["rows"] if r["employee_id"] == unique_employee_id
    )
    assert pending_earn_row["earns"][work_day]["pending"] is True
    assert pending_earn_row["earns"][work_day]["earned_on"] == work_day
    pending_balance = client.get("/api/requests/comp-off/balance", headers=headers).json()
    assert pending_balance["pending_earn"] == 1
    assert any(c.get("status") == "pending" and c.get("work_date") == work_day for c in pending_balance["credits"])
    again = client.post(
        "/api/requests/comp-off/earn",
        json={"work_date": work_day, "earn_type": "worked_wo", "worked_shift": "B"},
        headers=headers,
    )
    assert again.status_code == 400

    empty_avail = client.post(
        "/api/requests/comp-off/avail",
        json={"start_date": later, "end_date": later},
        headers=headers,
    )
    assert empty_avail.status_code == 400

    manager_blocked = client.patch(
        f"/api/requests/comp-off/{earn.json()['id']}/decide",
        json={"status": "approved"},
        headers=manager_headers,
    )
    assert manager_blocked.status_code == 403

    approved = client.patch(
        f"/api/requests/comp-off/{earn.json()['id']}/decide",
        json={"status": "approved"},
        headers=admin_headers,
    )
    assert approved.status_code == 200, approved.text
    earned_table = client.get(
        f"/api/shifts/table?start={work_day}&end={work_day}",
        headers=headers,
    )
    assert earned_table.status_code == 200, earned_table.text
    earned_row = next(r for r in earned_table.json()["rows"] if r["employee_id"] == unique_employee_id)
    assert earned_row["earns"][work_day]["pending"] is False
    assert earned_row["earns"][work_day]["status"] == "available"
    assert earned_row["earns"][work_day]["earned_on"] == work_day
    assert earned_row["cells"][work_day] == "G"
    balance = client.get("/api/requests/comp-off/balance", headers=headers)
    assert balance.status_code == 200
    assert balance.json()["available"] == 1
    assert balance.json()["earned"] == 1

    not_eligible = client.post(
        "/api/requests/comp-off/avail",
        json={"start_date": later, "end_date": later, "reason": "No roster yet"},
        headers=headers,
    )
    assert not_eligible.status_code == 400

    bulk_a = client.post(
        "/api/shifts/bulk",
        json={
            "department_id": department_id,
            "assignments": [{"employee_id": unique_employee_id, "date": later, "shift_code": "A"}],
        },
        headers=admin_headers,
    )
    assert bulk_a.status_code == 200, bulk_a.text

    avail = client.post(
        "/api/requests/comp-off/avail",
        json={"start_date": later, "end_date": later, "reason": "Paid day off against A"},
        headers=headers,
    )
    assert avail.status_code == 200, avail.text

    forbidden = client.get("/api/requests/comp-off/ledger", headers=headers)
    assert forbidden.status_code == 403

    pending_table = client.get(
        f"/api/shifts/table?start={later}&end={later}",
        headers=headers,
    )
    assert pending_table.status_code == 200, pending_table.text
    pending_row = next(r for r in pending_table.json()["rows"] if r["employee_id"] == unique_employee_id)
    assert pending_row["traces"][later]["pending"] is True
    assert pending_row["traces"][later]["earned_on"] == work_day
    assert pending_row["traces"][later]["used_on"] == later

    ledger = client.get("/api/requests/comp-off/ledger", headers=admin_headers)
    assert ledger.status_code == 200, ledger.text
    person = next(u for u in ledger.json()["users"] if u["employee_id"] == unique_employee_id)
    assert person["earned"] == 1
    assert person["reserved"] == 1
    assert person["status"] == "pending"

    used = client.patch(
        f"/api/requests/comp-off/{avail.json()['id']}/decide",
        json={"status": "approved"},
        headers=admin_headers,
    )
    assert used.status_code == 200, used.text
    after = client.get("/api/requests/comp-off/balance", headers=headers).json()
    assert after["available"] == 0
    assert after["used"] == 1
    assert after["earned"] == 1
    used_credit = next((c for c in after["credits"] if c.get("status") == "used"), None)
    assert used_credit
    assert used_credit["used_on"] == later
    assert used_credit["work_date"] == work_day

    table = client.get(
        f"/api/shifts/table?start={later}&end={later}",
        headers=headers,
    )
    assert table.status_code == 200, table.text
    my_row = next(r for r in table.json()["rows"] if r["employee_id"] == unique_employee_id)
    assert my_row["cells"][later] == "CO"
    assert my_row["traces"][later]["earned_on"] == work_day
    assert my_row["traces"][later]["worked_shift"] == "G"
    assert len(table.json()["rows"]) >= 1
    generated = client.get(
        f"/api/shifts/table?start={work_day}&end={work_day}",
        headers=headers,
    ).json()
    generated_row = next(r for r in generated["rows"] if r["employee_id"] == unique_employee_id)
    assert generated_row["earns"][work_day]["status"] == "used"
    assert generated_row["earns"][work_day]["used_on"] == later

    bulk_dual = client.post(
        "/api/shifts/bulk",
        json={
            "department_id": department_id,
            "assignments": [{"employee_id": unique_employee_id, "date": dual_day, "shift_code": "B"}],
        },
        headers=admin_headers,
    )
    assert bulk_dual.status_code == 200, bulk_dual.text
    joint = client.post(
        "/api/requests/comp-off/earn",
        json={
            "work_date": dual_day,
            "earn_type": "joint_ab",
            "worked_shift": "A",
            "reason": "Did A+B",
        },
        headers=headers,
    )
    assert joint.status_code == 200, joint.text
    pending_dual = client.get(
        f"/api/shifts/table?start={dual_day}&end={dual_day}",
        headers=headers,
    ).json()
    pending_dual_row = next(r for r in pending_dual["rows"] if r["employee_id"] == unique_employee_id)
    assert pending_dual_row["duals"][dual_day]["pair"] == "A+B"
    assert pending_dual_row["duals"][dual_day]["pending"] is True
    joint_ok = client.patch(
        f"/api/requests/comp-off/{joint.json()['id']}/decide",
        json={"status": "approved"},
        headers=admin_headers,
    )
    assert joint_ok.status_code == 200, joint_ok.text
    dual_table = client.get(
        f"/api/shifts/table?start={dual_day}&end={dual_day}",
        headers=headers,
    ).json()
    dual_row = next(r for r in dual_table["rows"] if r["employee_id"] == unique_employee_id)
    assert dual_row["cells"][dual_day] == "B"
    assert dual_row["duals"][dual_day]["pair"] == "A+B"
    assert dual_row["duals"][dual_day]["extra"] == "A"
    assert dual_row["duals"][dual_day]["pending"] is False


def test_overnight_ca_then_follow_on_b(
    client: TestClient, require_mongo, admin_headers: dict[str, str], unique_employee_id: str
):
    department_id = client.get("/api/departments").json()["departments"][0]["id"]
    password = "overnight-ca-pass-9x"
    created = client.post(
        "/api/users",
        json={
            "employee_id": unique_employee_id,
            "password": password,
            "full_name": "Overnight CA QA",
            "department_id": department_id,
            "role": "employee",
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    login = client.post("/api/auth/login", json={"employee_id": unique_employee_id, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    c_day = date.today().isoformat()
    a_day = (date.today() + timedelta(days=1)).isoformat()
    bulk = client.post(
        "/api/shifts/bulk",
        json={
            "department_id": department_id,
            "assignments": [{"employee_id": unique_employee_id, "date": c_day, "shift_code": "C"}],
        },
        headers=admin_headers,
    )
    assert bulk.status_code == 200, bulk.text
    overnight = client.post(
        "/api/requests/comp-off/earn",
        json={"work_date": c_day, "earn_type": "joint_ca", "worked_shift": "A", "reason": "C into A"},
        headers=headers,
    )
    assert overnight.status_code == 200, overnight.text
    assert overnight.json().get("span_end_date") == a_day
    pending = client.get(f"/api/shifts/table?start={c_day}&end={a_day}", headers=headers).json()
    pending_row = next(r for r in pending["rows"] if r["employee_id"] == unique_employee_id)
    assert pending_row["duals"][c_day]["overnight"] is True
    assert pending_row["duals"][c_day]["role"] == "c"
    assert pending_row["duals"][a_day]["role"] == "a"
    approved = client.patch(
        f"/api/requests/comp-off/{overnight.json()['id']}/decide",
        json={"status": "approved"},
        headers=admin_headers,
    )
    assert approved.status_code == 200, approved.text
    after = client.get(f"/api/shifts/table?start={c_day}&end={a_day}", headers=headers).json()
    row = next(r for r in after["rows"] if r["employee_id"] == unique_employee_id)
    assert row["cells"][c_day] == "C"
    assert row["cells"][a_day] == "A"
    assert row["duals"][c_day]["pair"] == "C→A"
    assert row["duals"][a_day]["pair"] == "C→A"
    follow = client.post(
        "/api/requests/comp-off/earn",
        json={"work_date": a_day, "earn_type": "joint_ab", "worked_shift": "B", "reason": "Then B after A"},
        headers=headers,
    )
    assert follow.status_code == 200, follow.text
    follow_ok = client.patch(
        f"/api/requests/comp-off/{follow.json()['id']}/decide",
        json={"status": "approved"},
        headers=admin_headers,
    )
    assert follow_ok.status_code == 200, follow_ok.text
    final = client.get(f"/api/shifts/table?start={c_day}&end={a_day}", headers=headers).json()
    final_row = next(r for r in final["rows"] if r["employee_id"] == unique_employee_id)
    assert final_row["cells"][c_day] == "C"
    assert final_row["cells"][a_day] == "A"
    assert final_row["duals"][a_day]["extra"] == "B"
    assert final_row["duals"][a_day]["pair"] == "C→A+B"
    assert final_row["duals"][c_day]["pair"] == "C→A+B"
