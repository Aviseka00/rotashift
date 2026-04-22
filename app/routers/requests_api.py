from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import TIMED_SHIFT_CODES
from app.database import get_db
from app.deps import get_current_user, require_roles

router = APIRouter(prefix="/api/requests", tags=["requests"])


def _iso(dt: Any) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


async def _leave_row(db, x: dict) -> dict:
    u = await db.users.find_one({"_id": x["user_id"]})
    dept_name = None
    dept_id_str = None
    if x.get("department_id"):
        dept_id_str = str(x["department_id"])
        dep = await db.departments.find_one({"_id": x["department_id"]})
        dept_name = dep["name"] if dep else "?"
    dec_name = None
    dec_eid = None
    if x.get("decided_by"):
        dec = await db.users.find_one({"_id": x["decided_by"]})
        if dec:
            dec_name = dec.get("full_name")
            dec_eid = dec.get("employee_id")
    return {
        "id": str(x["_id"]),
        "employee_id": u["employee_id"] if u else "?",
        "full_name": u["full_name"] if u else "?",
        "start_date": x["start_date"],
        "end_date": x["end_date"],
        "reason": x["reason"],
        "status": x["status"],
        "created_at": _iso(x.get("created_at")),
        "decided_at": _iso(x.get("decided_at")),
        "department_id": dept_id_str,
        "department_name": dept_name,
        "decided_by_name": dec_name,
        "decided_by_employee_id": dec_eid,
    }


async def _shift_change_row(db, x: dict) -> dict:
    u = await db.users.find_one({"_id": x["user_id"]})
    dept_name = None
    dept_id_str = None
    if x.get("department_id"):
        dept_id_str = str(x["department_id"])
        dep = await db.departments.find_one({"_id": x["department_id"]})
        dept_name = dep["name"] if dep else "?"
    dec_name = None
    dec_eid = None
    if x.get("decided_by"):
        dec = await db.users.find_one({"_id": x["decided_by"]})
        if dec:
            dec_name = dec.get("full_name")
            dec_eid = dec.get("employee_id")
    return {
        "id": str(x["_id"]),
        "employee_id": u["employee_id"] if u else "?",
        "full_name": u["full_name"] if u else "?",
        "date": x["date"],
        "from_shift": x["from_shift"],
        "to_shift": x["to_shift"],
        "reason": x["reason"],
        "status": x["status"],
        "created_at": _iso(x.get("created_at")),
        "decided_at": _iso(x.get("decided_at")),
        "department_id": dept_id_str,
        "department_name": dept_name,
        "decided_by_name": dec_name,
        "decided_by_employee_id": dec_eid,
    }


class LeaveCreate(BaseModel):
    start_date: str
    end_date: str
    reason: str = Field("", max_length=2000)


class ShiftChangeCreate(BaseModel):
    date: str
    from_shift: str = Field(..., pattern="^[ABCG]$")
    to_shift: str = Field(..., pattern="^[ABCG]$")
    reason: str = Field("", max_length=2000)


class DecideBody(BaseModel):
    status: str = Field(..., pattern="^(approved|rejected)$")


def _require_manager_department(user) -> ObjectId:
    if user.get("role") != "manager" or not user.get("department_id"):
        raise HTTPException(status_code=403, detail="Managers only")
    return ObjectId(user["department_id"])


@router.post("/leave")
async def create_leave(body: LeaveCreate, user=Depends(get_current_user)):
    db = get_db()
    if not user.get("department_id"):
        raise HTTPException(status_code=400, detail="User must belong to a department")
    doc = {
        "user_id": ObjectId(user["_id"]),
        "department_id": ObjectId(user["department_id"]),
        "start_date": body.start_date[:10],
        "end_date": body.end_date[:10],
        "reason": body.reason.strip(),
        "status": "pending",
        "created_at": datetime.now(timezone.utc),
    }
    res = await db.leave_requests.insert_one(doc)
    return {"id": str(res.inserted_id), "status": "pending"}


@router.get("/leave")
async def list_leave(
    department_id: str | None = Query(None),
    user=Depends(get_current_user),
):
    db = get_db()
    role = user.get("role")
    if role == "employee":
        cur = db.leave_requests.find({"user_id": ObjectId(user["_id"])}).sort("created_at", -1)
    elif role == "manager":
        dept = _require_manager_department(user)
        cur = db.leave_requests.find({"department_id": dept}).sort("created_at", -1)
    else:
        qfilter = {}
        if department_id:
            try:
                qfilter["department_id"] = ObjectId(department_id)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid department_id")
        cur = db.leave_requests.find(qfilter).sort("created_at", -1)

    items = []
    async for x in cur:
        items.append(await _leave_row(db, x))
    return {"requests": items}


@router.patch("/leave/{rid}/decide")
async def decide_leave(rid: str, body: DecideBody, user=Depends(require_roles("manager", "admin"))):
    db = get_db()
    try:
        oid = ObjectId(rid)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    req = await db.leave_requests.find_one({"_id": oid})
    if not req:
        raise HTTPException(status_code=404, detail="Not found")

    if user["role"] == "manager":
        if str(req["department_id"]) != user.get("department_id"):
            raise HTTPException(status_code=403, detail="Wrong department")

    await db.leave_requests.update_one(
        {"_id": oid},
        {
            "$set": {
                "status": body.status,
                "decided_at": datetime.now(timezone.utc),
                "decided_by": ObjectId(user["_id"]),
            }
        },
    )
    return {"ok": True, "status": body.status}


@router.post("/shift-change")
async def create_shift_change(body: ShiftChangeCreate, user=Depends(get_current_user)):
    db = get_db()
    if not user.get("department_id"):
        raise HTTPException(status_code=400, detail="User must belong to a department")
    fs = body.from_shift.upper()
    ts = body.to_shift.upper()
    if fs not in TIMED_SHIFT_CODES or ts not in TIMED_SHIFT_CODES:
        raise HTTPException(status_code=400, detail="Invalid shift codes (use A, B, C, or G)")
    doc = {
        "user_id": ObjectId(user["_id"]),
        "department_id": ObjectId(user["department_id"]),
        "date": body.date[:10],
        "from_shift": fs,
        "to_shift": ts,
        "reason": body.reason.strip(),
        "status": "pending",
        "created_at": datetime.now(timezone.utc),
    }
    res = await db.shift_change_requests.insert_one(doc)
    return {"id": str(res.inserted_id), "status": "pending"}


@router.get("/shift-change")
async def list_shift_change(
    department_id: str | None = Query(None),
    user=Depends(get_current_user),
):
    db = get_db()
    role = user.get("role")
    if role == "employee":
        cur = db.shift_change_requests.find({"user_id": ObjectId(user["_id"])}).sort("created_at", -1)
    elif role == "manager":
        dept = _require_manager_department(user)
        cur = db.shift_change_requests.find({"department_id": dept}).sort("created_at", -1)
    else:
        qfilter = {}
        if department_id:
            try:
                qfilter["department_id"] = ObjectId(department_id)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid department_id")
        cur = db.shift_change_requests.find(qfilter).sort("created_at", -1)

    items = []
    async for x in cur:
        items.append(await _shift_change_row(db, x))
    return {"requests": items}


@router.patch("/shift-change/{rid}/decide")
async def decide_shift_change(rid: str, body: DecideBody, user=Depends(require_roles("manager", "admin"))):
    db = get_db()
    try:
        oid = ObjectId(rid)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    req = await db.shift_change_requests.find_one({"_id": oid})
    if not req:
        raise HTTPException(status_code=404, detail="Not found")

    if user["role"] == "manager":
        if str(req["department_id"]) != user.get("department_id"):
            raise HTTPException(status_code=403, detail="Wrong department")

    new_status = body.status
    await db.shift_change_requests.update_one(
        {"_id": oid},
        {
            "$set": {
                "status": new_status,
                "decided_at": datetime.now(timezone.utc),
                "decided_by": ObjectId(user["_id"]),
            }
        },
    )

    if new_status == "approved":
        # Apply to shifts collection
        day = req["date"]
        dept_id = req["department_id"]
        uid = req["user_id"]
        code = req["to_shift"]
        now = datetime.now(timezone.utc)
        actor = ObjectId(user["_id"])
        await db.shifts.update_one(
            {"department_id": dept_id, "user_id": uid, "date": day},
            {
                "$set": {
                    "department_id": dept_id,
                    "user_id": uid,
                    "date": day,
                    "shift_code": code,
                    "assigned_by": actor,
                    "updated_at": now,
                    "change_request_id": oid,
                }
            },
            upsert=True,
        )

    return {"ok": True, "status": new_status}
