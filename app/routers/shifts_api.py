from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
    shift_code: str = Field(..., pattern="^[ABCG]$")


class BulkBody(BaseModel):
    department_id: Optional[str] = None
    assignments: List[AssignmentItem]


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

    async for s in db.shifts.find({"department_id": dept_oid}):
        day = parse_iso_date(s["date"])
        if day < d0 or day > d1:
            continue
        u = await db.users.find_one({"_id": s["user_id"]})
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

    async for lv in db.leave_requests.find({"department_id": dept_oid, "status": "approved"}):
        sd = parse_iso_date(lv["start_date"])
        ed = parse_iso_date(lv["end_date"])
        if ed < d0 or sd > d1:
            continue
        u = await db.users.find_one({"_id": lv["user_id"]})
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

    rows = []
    for u in users_list:
        uid = u["_id"]
        cells = {}
        async for s in db.shifts.find({"department_id": dept_oid, "user_id": uid}):
            cells[s["date"]] = s["shift_code"]
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
