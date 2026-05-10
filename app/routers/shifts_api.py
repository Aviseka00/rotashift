from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.config import SHIFT_DEFINITIONS
from app.database import get_db
from app.deps import get_current_user, require_roles
from app.shift_utils import calendar_event_for_shift, calendar_event_leave, parse_iso_date

router = APIRouter(prefix="/api/shifts", tags=["shifts"])


class AssignmentItem(BaseModel):
    employee_id: str
    date: str = Field(..., description="YYYY-MM-DD")
    shift_code: str = Field(..., min_length=1, max_length=2, description="A B C G L or WO")


class BulkBody(BaseModel):
    department_id: Optional[str] = None
    assignments: List[AssignmentItem]


class SelfRosterDayBody(BaseModel):
    date: str = Field(..., description="YYYY-MM-DD")
    shift_code: str = Field(..., min_length=1, max_length=2, description="A B C G L or WO")


def _dept_scope(user: dict, body_dept: Optional[str]) -> ObjectId:
    role = user.get("role")
    if role == "admin":
        if not body_dept:
            raise HTTPException(status_code=400, detail="department_id required for admin bulk assign")
        try:
            return ObjectId(body_dept)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid department_id")
    if role == "manager":
        if not user.get("department_id"):
            raise HTTPException(status_code=400, detail="Manager has no department")
        return ObjectId(user["department_id"])
    raise HTTPException(status_code=403, detail="Not allowed")


async def _resolve_user_in_dept(db, emp: str, dept_id: ObjectId):
    u = await db.users.find_one({"employee_id": emp.strip().upper(), "department_id": dept_id})
    if not u:
        raise HTTPException(status_code=400, detail=f"Employee {emp} not in selected department")
    return u


@router.post("/bulk")
async def bulk_assign(body: BulkBody, user=Depends(require_roles("admin", "manager"))):
    db = get_db()
    dept_id = _dept_scope(user, body.department_id)
    now = datetime.now(timezone.utc)
    actor_id = ObjectId(user["_id"])
    inserted = 0
    errors = []
    for row in body.assignments:
        code = row.shift_code.upper()
        if code not in SHIFT_DEFINITIONS:
            errors.append({"employee_id": row.employee_id, "error": "bad shift code"})
            continue
        try:
            parse_iso_date(row.date)
        except Exception:
            errors.append({"employee_id": row.employee_id, "error": "bad date"})
            continue
        try:
            target = await _resolve_user_in_dept(db, row.employee_id, dept_id)
        except HTTPException as e:
            errors.append({"employee_id": row.employee_id, "error": e.detail})
            continue
        doc = {
            "department_id": dept_id,
            "user_id": target["_id"],
            "date": row.date[:10],
            "shift_code": code,
            "assigned_by": actor_id,
            "updated_at": now,
        }
        await db.shifts.update_one(
            {"department_id": dept_id, "user_id": target["_id"], "date": doc["date"]},
            {"$set": doc},
            upsert=True,
        )
        inserted += 1
    return {"upserted": inserted, "errors": errors}


@router.post("/mine")
async def set_own_roster_day(body: SelfRosterDayBody, user=Depends(get_current_user)):
    """Employees set their own roster cell (same codes as the department table). Managers/admins use /bulk."""
    if user.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Managers and administrators use bulk roster assign for any team member")
    if not user.get("department_id"):
        raise HTTPException(status_code=400, detail="No department")
    code = body.shift_code.strip().upper()
    if code not in SHIFT_DEFINITIONS:
        raise HTTPException(status_code=400, detail="Invalid shift code")
    try:
        parse_iso_date(body.date)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date")

    db = get_db()
    dept_id = ObjectId(user["department_id"])
    uid = ObjectId(user["_id"])
    now = datetime.now(timezone.utc)
    doc = {
        "department_id": dept_id,
        "user_id": uid,
        "date": body.date[:10],
        "shift_code": code,
        "assigned_by": uid,
        "updated_at": now,
    }
    await db.shifts.update_one(
        {"department_id": dept_id, "user_id": uid, "date": doc["date"]},
        {"$set": doc},
        upsert=True,
    )
    return {"ok": True, "shift_code": code, "date": doc["date"]}


@router.get("/calendar")
async def calendar_feed(
    start: str = Query(..., description="ISO date start"),
    end: str = Query(..., description="ISO date end"),
    department_id: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    db = get_db()
    role = user.get("role")
    if role == "employee":
        if not user.get("department_id"):
            return {"events": []}
        dept_oid = ObjectId(user["department_id"])
    elif role == "manager":
        if not user.get("department_id"):
            return {"events": []}
        dept_oid = ObjectId(user["department_id"])
    else:
        if not department_id:
            raise HTTPException(status_code=400, detail="department_id query required for admin")
        try:
            dept_oid = ObjectId(department_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid department_id")

    try:
        d0 = parse_iso_date(start)
        d1 = parse_iso_date(end)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date range")

    events: List[Dict[str, Any]] = []
    users_by_id: Dict[ObjectId, dict] = {}
    user_ids: Set[ObjectId] = set()

    shift_q = {"department_id": dept_oid, "date": {"$gte": d0.isoformat(), "$lte": d1.isoformat()}}
    shifts_in_range: List[dict] = []
    async for s in db.shifts.find(shift_q):
        shifts_in_range.append(s)
        if s.get("user_id"):
            user_ids.add(s["user_id"])

    approved_leaves: List[dict] = []
    async for lv in db.leave_requests.find({"department_id": dept_oid, "status": "approved"}):
        approved_leaves.append(lv)
        if lv.get("user_id"):
            user_ids.add(lv["user_id"])

    if user_ids:
        async for u in db.users.find({"_id": {"$in": list(user_ids)}}):
            users_by_id[u["_id"]] = u

    for s in shifts_in_range:
        try:
            day = parse_iso_date(s["date"])
        except Exception:
            continue
        u = users_by_id.get(s.get("user_id"))
        if not u:
            continue
        events.append(
            calendar_event_for_shift(
                assignment_id=str(s["_id"]),
                user_name=u["full_name"],
                employee_id=u["employee_id"],
                shift_code=s["shift_code"],
                day=day,
                kind="shift",
            )
        )

    for lv in approved_leaves:
        sd = parse_iso_date(lv["start_date"])
        ed = parse_iso_date(lv["end_date"])
        if ed < d0 or sd > d1:
            continue
        u = users_by_id.get(lv.get("user_id"))
        if not u:
            continue
        events.append(
            calendar_event_leave(
                request_id=str(lv["_id"]),
                user_name=u["full_name"],
                employee_id=u["employee_id"],
                start_day=max(sd, d0),
                end_day=min(ed, d1),
            )
        )

    return {"events": events}


@router.get("/table")
async def table_matrix(
    start: str = Query(...),
    end: str = Query(...),
    department_id: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    """Landscape-oriented grid: rows = users in dept, cols = dates, cells = shift code."""
    db = get_db()
    role = user.get("role")
    if role == "employee":
        if not user.get("department_id"):
            return {"dates": [], "rows": []}
        dept_oid = ObjectId(user["department_id"])
    elif role == "manager":
        if not user.get("department_id"):
            return {"dates": [], "rows": []}
        dept_oid = ObjectId(user["department_id"])
    else:
        if not department_id:
            raise HTTPException(status_code=400, detail="department_id required")
        try:
            dept_oid = ObjectId(department_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid department_id")

    d0 = parse_iso_date(start)
    d1 = parse_iso_date(end)
    dates: List[str] = []
    cur = d0
    from datetime import timedelta

    while cur <= d1:
        dates.append(cur.isoformat())
        cur += timedelta(days=1)

    users_list = []
    async for u in db.users.find({"department_id": dept_oid}).sort("employee_id", 1):
        users_list.append(u)

    shifts_q = {"department_id": dept_oid, "date": {"$gte": d0.isoformat(), "$lte": d1.isoformat()}}
    shifts_by_user: Dict[ObjectId, Dict[str, str]] = {}
    async for s in db.shifts.find(shifts_q):
        uid = s.get("user_id")
        day = s.get("date")
        if not uid or not day:
            continue
        shifts_by_user.setdefault(uid, {})[day] = s.get("shift_code", "")

    rows = []
    for u in users_list:
        uid = u["_id"]
        cells = shifts_by_user.get(uid, {})
        rows.append(
            {
                "employee_id": u["employee_id"],
                "full_name": u["full_name"],
                "role": u["role"],
                "cells": cells,
            }
        )

    dept = await db.departments.find_one({"_id": dept_oid})
    return {
        "department_name": dept["name"] if dept else "",
        "dates": dates,
        "shift_legend": SHIFT_DEFINITIONS,
        "rows": rows,
    }


@router.get("/manpower-summary")
async def manpower_summary(
    start: str = Query(...),
    end: str = Query(...),
    department_id: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    """Date-wise manpower distribution by shift code for the roster table range."""
    db = get_db()
    role = user.get("role")
    if role == "employee":
        if not user.get("department_id"):
            return {"department_name": "", "dates": [], "shift_codes": [], "summary": []}
        dept_oid = ObjectId(user["department_id"])
    elif role == "manager":
        if not user.get("department_id"):
            return {"department_name": "", "dates": [], "shift_codes": [], "summary": []}
        dept_oid = ObjectId(user["department_id"])
    else:
        if not department_id:
            raise HTTPException(status_code=400, detail="department_id required")
        try:
            dept_oid = ObjectId(department_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid department_id")

    d0 = parse_iso_date(start)
    d1 = parse_iso_date(end)
    if d1 < d0:
        raise HTTPException(status_code=400, detail="Invalid date range")

    from datetime import timedelta

    dates: List[str] = []
    by_day: Dict[str, Dict[str, int]] = {}
    cur = d0
    while cur <= d1:
        day = cur.isoformat()
        dates.append(day)
        by_day[day] = {}
        cur += timedelta(days=1)

    q = {"department_id": dept_oid, "date": {"$gte": d0.isoformat(), "$lte": d1.isoformat()}}
    async for s in db.shifts.find(q):
        day = s.get("date")
        code = (s.get("shift_code") or "").strip().upper()
        if not day or not code or day not in by_day:
            continue
        by_day[day][code] = by_day[day].get(code, 0) + 1

    shift_codes = sorted({code for counts in by_day.values() for code in counts.keys()})
    summary = []
    for day in dates:
        counts = by_day[day]
        summary.append({"date": day, "counts": counts, "total": sum(counts.values())})

    dept = await db.departments.find_one({"_id": dept_oid})
    return {
        "department_name": dept["name"] if dept else "",
        "dates": dates,
        "shift_codes": shift_codes,
        "summary": summary,
    }


@router.get("/manpower-summary/users")
async def manpower_summary_users(
    date: str = Query(..., description="YYYY-MM-DD"),
    shift_code: str = Query(..., description="Shift code like A/B/C/G/L/WO"),
    department_id: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    """Drill-down users for one day + one shift in the department scope."""
    db = get_db()
    role = user.get("role")
    if role == "employee":
        if not user.get("department_id"):
            return {"department_name": "", "date": date, "shift_code": shift_code, "users": []}
        dept_oid = ObjectId(user["department_id"])
    elif role == "manager":
        if not user.get("department_id"):
            return {"department_name": "", "date": date, "shift_code": shift_code, "users": []}
        dept_oid = ObjectId(user["department_id"])
    else:
        if not department_id:
            raise HTTPException(status_code=400, detail="department_id required")
        try:
            dept_oid = ObjectId(department_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid department_id")

    try:
        day = parse_iso_date(date).isoformat()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date")
    code = (shift_code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Invalid shift_code")

    out_users: List[Dict[str, str]] = []
    q = {"department_id": dept_oid, "date": day, "shift_code": code}
    user_ids: List[ObjectId] = []
    async for s in db.shifts.find(q):
        uid = s.get("user_id")
        if uid:
            user_ids.append(uid)
    if user_ids:
        async for u in db.users.find({"_id": {"$in": user_ids}}):
            out_users.append(
                {
                    "employee_id": u.get("employee_id", ""),
                    "full_name": u.get("full_name", ""),
                    "role": u.get("role", "employee"),
                }
            )
    out_users.sort(key=lambda item: (item.get("employee_id") or ""))
    dept = await db.departments.find_one({"_id": dept_oid})
    return {
        "department_name": dept["name"] if dept else "",
        "date": day,
        "shift_code": code,
        "users": out_users,
    }
