from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi.testclient import TestClient


def _new_department(client: TestClient, admin_headers: dict) -> str:
    name = f"autoroster-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/departments", json={"name": name}, headers=admin_headers)
    assert r.status_code in (200, 201), r.text
    for d in client.get("/api/departments").json()["departments"]:
        if d["name"] == name:
            return d["id"]
    raise AssertionError("department not created")


def _add_user(client, admin_headers, dept_id, category, fixed=False):
    emp = f"R{uuid.uuid4().hex[:9].upper()}"
    r = client.post(
        "/api/users",
        json={"employee_id": emp, "password": "pytest-pass-9x", "full_name": f"Roster {emp}", "department_id": dept_id, "role": "employee"},
        headers=admin_headers,
    )
    assert r.status_code in (200, 201), r.text
    if category:
        r = client.post(
            "/api/users/roster-categories",
            json={"items": [{"employee_id": emp, "roster_category": category, "roster_fixed_g_non_batch": fixed}]},
            headers=admin_headers,
        )
        assert r.json()["updated"] == 1, r.text
    return emp


def _login(client, emp):
    r = client.post("/api/auth/login", json={"employee_id": emp, "password": "pytest-pass-9x"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_generate_month_preview_then_save(client: TestClient, require_mongo, admin_headers):
    dept = _new_department(client, admin_headers)
    workers = [_add_user(client, admin_headers, dept, "W") for _ in range(10)]
    manager = _add_user(client, admin_headers, dept, "M")
    no_cat = _add_user(client, admin_headers, dept, None)

    info = client.get(f"/api/roster/month?department_id={dept}&month=2031-03", headers=admin_headers).json()
    assert info["setting"] is None and info["people_without_category"] == 1

    body = {"department_id": dept, "month": "2031-03", "mode": "NON_BATCH", "dry_run": True}
    prev = client.post("/api/roster/month/generate", json=body, headers=admin_headers)
    assert prev.status_code == 200, prev.text
    data = prev.json()
    assert data["written"] == 0 and len(data["dates"]) == 31
    rows = {r["employee_id"]: r for r in data["rows"]}
    assert rows[manager]["role"] == "fixed" and rows[no_cat]["role"] == "skipped"
    assert all(rows[w]["cycle"] == "nonbatch-rotator-17" for w in workers)
    assert all(4 <= rows[w]["cells"].count("C") <= 6 for w in workers)

    # dry run must not touch the roster
    table = client.get(f"/api/shifts/table?start=2031-03-01&end=2031-03-31&department_id={dept}", headers=admin_headers).json()
    assert all(not r["cells"] for r in table["rows"])

    body["dry_run"] = False
    saved = client.post("/api/roster/month/generate", json=body, headers=admin_headers)
    assert saved.status_code == 200 and saved.json()["written"] == 11 * 31
    table = client.get(f"/api/shifts/table?start=2031-03-01&end=2031-03-31&department_id={dept}", headers=admin_headers).json()
    trow = {r["employee_id"]: r for r in table["rows"]}
    assert trow[manager]["cells"]["2031-03-03"] == "G"  # Monday
    assert trow[manager]["cells"]["2031-03-02"] == "WO"  # Sunday
    assert not trow[no_cat]["cells"]

    info = client.get(f"/api/roster/month?department_id={dept}&month=2031-03", headers=admin_headers).json()
    assert info["setting"]["mode"] == "NON_BATCH"


def test_generate_keeps_approved_leave(client: TestClient, require_mongo, admin_headers):
    dept = _new_department(client, admin_headers)
    emp = _add_user(client, admin_headers, dept, "W")
    client.post(
        "/api/shifts/bulk",
        json={"department_id": dept, "assignments": [{"employee_id": emp, "date": "2031-04-10", "shift_code": "L"}]},
        headers=admin_headers,
    )
    r = client.post(
        "/api/roster/month/generate",
        json={"department_id": dept, "month": "2031-04", "mode": "NON_BATCH", "dry_run": False},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    table = client.get(f"/api/shifts/table?start=2031-04-01&end=2031-04-30&department_id={dept}", headers=admin_headers).json()
    assert table["rows"][0]["cells"]["2031-04-10"] == "L"


def test_holidays_show_on_roster_table(client: TestClient, require_mongo, admin_headers):
    dept = _new_department(client, admin_headers)
    table = client.get(f"/api/shifts/table?start=2031-01-20&end=2031-01-31&department_id={dept}", headers=admin_headers).json()
    assert "Republic Day" in table["holidays"].get("2031-01-26", [])

    fest = f"Test Festival {uuid.uuid4().hex[:6]}"
    added = client.post("/api/holidays", json={"date": "2031-01-28", "name": fest}, headers=admin_headers)
    assert added.status_code == 200, added.text
    table = client.get(f"/api/shifts/table?start=2031-01-20&end=2031-01-31&department_id={dept}", headers=admin_headers).json()
    assert fest in table["holidays"]["2031-01-28"]
    assert client.delete(f"/api/holidays/{added.json()['id']}", headers=admin_headers).status_code == 200


def test_wo_move_rules(client: TestClient, require_mongo, admin_headers):
    dept = _new_department(client, admin_headers)
    emp = _add_user(client, admin_headers, dept, "W")
    #            Mon   Tue   Wed  Thu  Fri   Sat  Sun  Mon  Tue
    week = ["A", "A", "WO", "B", "B", "B", "WO", "A", "C"]
    base = date(2031, 6, 2)
    client.post(
        "/api/shifts/bulk",
        json={
            "department_id": dept,
            "assignments": [
                {"employee_id": emp, "date": (base + timedelta(days=i)).isoformat(), "shift_code": c}
                for i, c in enumerate(week)
            ],
        },
        headers=admin_headers,
    )
    me = _login(client, emp)

    # Clubbing: new WO on Fri 6th would sit next to the WO on Sat 7th... (Sun 8th here)
    r = client.post("/api/requests/wo-move", json={"wo_date": "2031-06-04", "new_off_date": "2031-06-07"}, headers=me)
    assert r.status_code == 400 and "clubbed" in r.json()["detail"]
    # New WO must replace a duty day
    r = client.post("/api/requests/wo-move", json={"wo_date": "2031-06-04", "new_off_date": "2031-06-08"}, headers=me)
    assert r.status_code == 400
    # Valid: work B on Wed 4th, take WO on Fri 6th
    r = client.post("/api/requests/wo-move", json={"wo_date": "2031-06-04", "new_off_date": "2031-06-06"}, headers=me)
    assert r.status_code == 200, r.text
    assert r.json()["work_shift"] == "B"
    rid = r.json()["id"]

    ok = client.patch(f"/api/requests/shift-change/{rid}/decide", json={"status": "approved"}, headers=admin_headers)
    assert ok.status_code == 200, ok.text
    table = client.get(f"/api/shifts/table?start=2031-06-02&end=2031-06-10&department_id={dept}", headers=admin_headers).json()
    cells = table["rows"][0]["cells"]
    assert cells["2031-06-04"] == "B" and cells["2031-06-06"] == "WO"

    listed = client.get("/api/requests/shift-change", headers=me).json()["requests"]
    assert any(x.get("kind") == "wo_move" and x["new_off_date"] == "2031-06-06" for x in listed)


def test_generate_skips_days_with_pending_requests(client: TestClient, require_mongo, admin_headers):
    dept = _new_department(client, admin_headers)
    emp = _add_user(client, admin_headers, dept, "W")
    me = _login(client, emp)
    r = client.post("/api/requests/leave", json={"start_date": "2031-05-12", "end_date": "2031-05-13", "reason": "x"}, headers=me)
    assert r.status_code == 200, r.text
    gen = client.post(
        "/api/roster/month/generate",
        json={"department_id": dept, "month": "2031-05", "mode": "NON_BATCH", "dry_run": False},
        headers=admin_headers,
    )
    assert gen.status_code == 200, gen.text
    row = gen.json()["rows"][0]
    assert 11 in row["kept"] and 12 in row["kept"]
    table = client.get(f"/api/shifts/table?start=2031-05-01&end=2031-05-31&department_id={dept}", headers=admin_headers).json()
    cells = table["rows"][0]["cells"]
    assert "2031-05-12" not in cells and "2031-05-13" not in cells and "2031-05-14" in cells
