"""Monthly auto-roster: admin picks BATCH or NON_BATCH for a month and the grid is generated."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from pymongo import UpdateOne
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import get_db
from app.deps import require_roles
from app.roster_scheduler import (
    CATEGORIES,
    CYCLE_TEXT,
    MODE_LABELS,
    MODES,
    Person,
    generate_month,
    month_bounds,
    previous_month,
)

router = APIRouter(prefix="/api/roster", tags=["roster"])

HISTORY_DAYS = 45
# Cells the generator never overwrites: leave, comp-off, and anything set by an approved request.
KEEP_CODES = {"L", "CO"}
KEEP_FIELDS = (
    "leave_request_id",
    "change_request_id",
    "comp_off_earn_request_id",
    "comp_off_avail_request_id",
    "extra_shift_code",
    "overnight_to_date",
    "overnight_from_date",
)


class GenerateBody(BaseModel):
    department_id: str
    month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    mode: str = Field(..., pattern="^(BATCH|NON_BATCH)$")
    dry_run: bool = True


def _oid(value: str, what: str = "id") -> ObjectId:
    try:
        return ObjectId(value)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {what}")


async def _pending_request_days(db, dept: ObjectId, uids: list, start: date, end: date) -> list[tuple]:
    """(user_id, iso_day) pairs inside the month that pending requests refer to."""
    lo, hi = start.isoformat(), end.isoformat()
    out: list[tuple] = []

    def span(uid, a: str, b: str) -> None:
        d0 = max(date.fromisoformat(str(a)[:10]), start)
        d1 = min(date.fromisoformat(str(b or a)[:10]), end)
        while d0 <= d1:
            out.append((uid, d0.isoformat()))
            d0 += timedelta(days=1)

    base = {"department_id": dept, "user_id": {"$in": uids}, "status": "pending"}
    async for r in db.leave_requests.find({**base, "start_date": {"$lte": hi}, "end_date": {"$gte": lo}}):
        span(r["user_id"], r["start_date"], r["end_date"])
    async for r in db.shift_change_requests.find(base):
        for key in ("date", "new_off_date"):
            day = str(r.get(key) or "")[:10]
            if lo <= day <= hi:
                out.append((r["user_id"], day))
    async for r in db.comp_off_requests.find(base):
        if r.get("start_date"):
            if str(r["start_date"])[:10] <= hi and str(r.get("end_date") or r["start_date"])[:10] >= lo:
                span(r["user_id"], r["start_date"], r.get("end_date"))
        for key in ("work_date", "span_end_date"):
            day = str(r.get(key) or "")[:10]
            if lo <= day <= hi:
                out.append((r["user_id"], day))
    return out


def _is_kept(doc: dict) -> bool:
    code = str(doc.get("shift_code") or "").strip().upper()
    return code in KEEP_CODES or any(doc.get(k) for k in KEEP_FIELDS)


def _month_doc_out(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return {
        "month": doc["month"],
        "mode": doc["mode"],
        "mode_label": MODE_LABELS.get(doc["mode"], doc["mode"]),
        "generated_at": doc["generated_at"].isoformat() if doc.get("generated_at") else None,
        "warnings": doc.get("warnings", []),
    }


@router.get("/month")
async def get_month(
    department_id: str = Query(...),
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    user=Depends(require_roles("admin")),
):
    db = get_db()
    dept = _oid(department_id, "department_id")
    doc = await db.roster_months.find_one({"department_id": dept, "month": month})
    total = await db.users.count_documents({"department_id": dept})
    with_cat = await db.users.count_documents(
        {"department_id": dept, "roster_category": {"$in": list(CATEGORIES)}}
    )
    return {
        "month": month,
        "setting": _month_doc_out(doc),
        "people_total": total,
        "people_without_category": total - with_cat,
        "modes": [{"value": m, "label": MODE_LABELS[m]} for m in MODES],
        "categories": CATEGORIES,
        "cycles": CYCLE_TEXT,
    }


@router.post("/month/generate")
async def generate(body: GenerateBody, user=Depends(require_roles("admin"))):
    db = get_db()
    dept = _oid(body.department_id, "department_id")
    if not await db.departments.find_one({"_id": dept}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Department not found")
    try:
        start, days = month_bounds(body.month)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid month")
    end = start + timedelta(days=days - 1)
    hist_start = start - timedelta(days=HISTORY_DAYS)

    users = await db.users.find(
        {"department_id": dept},
        {"employee_id": 1, "full_name": 1, "roster_category": 1, "roster_fixed_g_non_batch": 1},
    ).to_list(length=None)
    if not users:
        raise HTTPException(status_code=400, detail="This department has no people yet")
    uids = [u["_id"] for u in users]

    shift_docs = await db.shifts.find(
        {"department_id": dept, "user_id": {"$in": uids}, "date": {"$gte": hist_start.isoformat(), "$lte": end.isoformat()}}
    ).to_list(length=None)
    prev_states = {
        s["user_id"]: s
        for s in await db.roster_state.find(
            {"department_id": dept, "month": previous_month(body.month), "user_id": {"$in": uids}}
        ).to_list(length=None)
    }

    history: dict = {u: {} for u in uids}
    locked: dict = {u: {} for u in uids}
    for s in shift_docs:
        uid, day = s.get("user_id"), str(s.get("date") or "")[:10]
        if uid not in history or not day:
            continue
        code = str(s.get("shift_code") or "").strip().upper()
        if day < start.isoformat():
            history[uid][day] = code
        elif _is_kept(s):
            locked[uid][(datetime.fromisoformat(day).date() - start).days] = code

    # Like grid imports, never touch days that a pending request refers to:
    # approving the request later must still find the roster it was made against.
    current = {
        (s["user_id"], str(s.get("date") or "")[:10]): str(s.get("shift_code") or "").strip().upper()
        for s in shift_docs
        if s.get("user_id")
    }
    for uid, day in await _pending_request_days(db, dept, uids, start, end):
        if uid in locked:
            idx = (date.fromisoformat(day) - start).days
            if 0 <= idx < days:
                locked[uid].setdefault(idx, current.get((uid, day), ""))

    people = [
        Person(
            key=str(u["_id"]),
            employee_id=u["employee_id"],
            name=u.get("full_name") or u["employee_id"],
            category=str(u.get("roster_category") or ""),
            fixed_g_non_batch=bool(u.get("roster_fixed_g_non_batch")),
            history=history[u["_id"]],
            prev_state=prev_states.get(u["_id"]),
            locked=locked[u["_id"]],
        )
        for u in users
    ]
    result = generate_month(
        mode=body.mode,
        start=start,
        days=days,
        people=people,
    )

    written = 0
    if not body.dry_run:
        now = datetime.now(timezone.utc)
        actor = ObjectId(user["_id"])
        shift_ops = []
        state_ops = []
        for res in result["people"]:
            if res.role == "skipped":
                continue
            uid = ObjectId(res.person.key)
            for d, code in enumerate(res.codes):
                if code is None:
                    continue
                day = result["dates"][d]
                shift_ops.append(UpdateOne(
                    {"department_id": dept, "user_id": uid, "date": day},
                    {
                        "$set": {
                            "department_id": dept,
                            "user_id": uid,
                            "date": day,
                            "shift_code": code,
                            "assigned_by": actor,
                            "updated_at": now,
                            "auto_roster_month": body.month,
                        },
                        "$unset": {"extra_shift_code": "", "dual_pair": ""},
                    },
                    upsert=True,
                ))
            if res.end_state:
                state_ops.append(UpdateOne(
                    {"department_id": dept, "user_id": uid, "month": body.month},
                    {"$set": {**res.end_state, "department_id": dept, "user_id": uid, "month": body.month, "updated_at": now}},
                    upsert=True,
                ))
        if shift_ops:
            await db.shifts.bulk_write(shift_ops, ordered=False)
        if state_ops:
            await db.roster_state.bulk_write(state_ops, ordered=False)
        written = len(shift_ops)
        await db.roster_months.update_one(
            {"department_id": dept, "month": body.month},
            {
                "$set": {
                    "department_id": dept,
                    "month": body.month,
                    "mode": body.mode,
                    "generated_at": now,
                    "generated_by": actor,
                    "warnings": result["warnings"],
                }
            },
            upsert=True,
        )

    rows = []
    for res in result["people"]:
        p = res.person
        rows.append(
            {
                "employee_id": p.employee_id,
                "full_name": p.name,
                "category": p.category or None,
                "role": res.role,
                "cells": [c if c is not None else (p.locked.get(d) or "") for d, c in enumerate(res.codes)]
                if res.role != "skipped"
                else [],
                "kept": sorted(p.locked.keys()),
                "cycle": res.cycle,
                "phase": res.phase,
                "notes": res.notes,
            }
        )
    return {
        "month": body.month,
        "mode": body.mode,
        "mode_label": MODE_LABELS[body.mode],
        "dry_run": body.dry_run,
        "written": written,
        "dates": result["dates"],
        "rows": rows,
        "daily": result["daily"],
        "targets": result["targets"],
        "warnings": result["warnings"],
        "cycles": result["cycles"],
    }
