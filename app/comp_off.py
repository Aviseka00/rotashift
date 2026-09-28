"""Compensatory-off rules: earn one credit for extra work, later avail it as paid CO."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from bson import ObjectId
from fastapi import HTTPException

EARN_TYPES = {
    "worked_wo": "Worked on week off",
    "worked_leave": "Worked on leave",
    "worked_holiday": "Worked on holiday",
    "joint_ab": "Joint extra shift A + B",
    "joint_bc": "Joint extra shift B + C",
    "joint_ca": "Joint extra shift C + A",
}

JOINT_PAIRS = {
    "joint_ab": frozenset({"A", "B"}),
    "joint_bc": frozenset({"B", "C"}),
    "joint_ca": frozenset({"C", "A"}),
}

# Extra duty that can generate a credit — G general duty never qualifies.
WORKED_SHIFT_CODES = frozenset({"A", "B", "C"})
REST_EARN_TYPES = frozenset({"worked_wo", "worked_leave", "worked_holiday"})
JOINT_EARN_TYPES = frozenset(JOINT_PAIRS)
MAX_AVAIL_DAYS = 31


def inclusive_days(start: date, end: date) -> list[str]:
    if end < start:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")
    days = []
    day = start
    while day <= end:
        days.append(day.isoformat())
        day += timedelta(days=1)
        if len(days) > MAX_AVAIL_DAYS:
            raise HTTPException(status_code=400, detail=f"Choose at most {MAX_AVAIL_DAYS} days at a time")
    return days


def validate_earn(earn_type: str, worked_shift: str) -> tuple[str, str]:
    et = (earn_type or "").strip().lower()
    ws = (worked_shift or "").strip().upper()
    if et not in EARN_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Choose why this credit is due: week off, leave, holiday, or a joint A+B / B+C / C+A shift",
        )
    if ws not in WORKED_SHIFT_CODES:
        raise HTTPException(status_code=400, detail="The extra duty must be shift A, B, or C — G does not earn comp-off")
    pair = JOINT_PAIRS.get(et)
    if pair and ws not in pair:
        a, b = sorted(pair)
        raise HTTPException(status_code=400, detail=f"For {a}+{b} extra duty, the extra shift must be {a} or {b}")
    return et, ws


async def existing_open_earn(db, user_id: ObjectId, work_date: str, earn_type: str) -> dict | None:
    return await db.comp_off_requests.find_one(
        {
            "user_id": user_id,
            "kind": "earn",
            "work_date": work_date,
            "earn_type": earn_type,
            "status": {"$in": ["pending", "approved"]},
        }
    )


async def require_leave_days(db, user_id: ObjectId, department_id: ObjectId, days: list[str]) -> None:
    """Comp-off can only be applied to days already marked leave on the roster."""
    not_leave: list[str] = []
    for day in days:
        shift = await db.shifts.find_one({"user_id": user_id, "department_id": department_id, "date": day})
        code = str((shift or {}).get("shift_code") or "").strip().upper()
        if code != "L":
            not_leave.append(day)
    if not_leave:
        shown = ", ".join(not_leave[:6])
        extra = f" (+{len(not_leave) - 6} more)" if len(not_leave) > 6 else ""
        raise HTTPException(
            status_code=400,
            detail=(
                "Comp-off can only be used against leave you have already taken. "
                f"These dates are not leave (L): {shown}{extra}"
            ),
        )


async def credit_counts(db, user_id: ObjectId) -> dict[str, int]:
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ]
    rows = await db.comp_off_credits.aggregate(pipeline).to_list(length=None)
    counts = {r["_id"]: r["n"] for r in rows}
    pending_earn = await db.comp_off_requests.count_documents(
        {"user_id": user_id, "kind": "earn", "status": "pending"}
    )
    pending_avail_days = 0
    async for req in db.comp_off_requests.find(
        {"user_id": user_id, "kind": "avail", "status": "pending"},
        {"days": 1},
    ):
        pending_avail_days += int(req.get("days") or 0)
    available = int(counts.get("available") or 0)
    reserved = int(counts.get("reserved") or 0)
    used = int(counts.get("used") or 0)
    return {
        "available": available,
        "reserved": reserved,
        "used": used,
        "earned": available + reserved + used,
        "pending_earn": int(pending_earn),
        "pending_avail_days": int(pending_avail_days),
    }


async def reserve_oldest_credits(db, user_id: ObjectId, n: int, avail_request_id: ObjectId, days: list[str] | None = None) -> list[ObjectId]:
    now = datetime.now(timezone.utc)
    credits = (
        await db.comp_off_credits.find({"user_id": user_id, "status": "available"})
        .sort("created_at", 1)
        .to_list(length=n)
    )
    have = len(credits)
    if have < n:
        raise HTTPException(
            status_code=400,
            detail=f"You have {have} available comp-off day(s); this request needs {n}. Earn and get extra days approved first.",
        )
    if days and len(days) != n:
        raise HTTPException(status_code=400, detail="Comp-off days do not match the number of credits")
    ids = [c["_id"] for c in credits]
    for index, credit_id in enumerate(ids):
        payload = {"status": "reserved", "avail_request_id": avail_request_id, "updated_at": now}
        if days:
            payload["used_on"] = days[index]
        result = await db.comp_off_credits.update_one(
            {"_id": credit_id, "status": "available"},
            {"$set": payload},
        )
        if result.modified_count != 1:
            await db.comp_off_credits.update_many(
                {"avail_request_id": avail_request_id, "status": "reserved"},
                {"$set": {"status": "available"}, "$unset": {"avail_request_id": "", "used_on": ""}},
            )
            raise HTTPException(status_code=409, detail="Those credits were just used. Refresh and try again.")
    return ids


async def release_reserved_credits(db, avail_request_id: ObjectId) -> None:
    await db.comp_off_credits.update_many(
        {"avail_request_id": avail_request_id, "status": "reserved"},
        {
            "$set": {"status": "available", "updated_at": datetime.now(timezone.utc)},
            "$unset": {"avail_request_id": "", "used_on": ""},
        },
    )


async def consume_reserved_credits(db, avail_request_id: ObjectId, days: list[str] | None = None) -> list[dict]:
    now = datetime.now(timezone.utc)
    credits = (
        await db.comp_off_credits.find({"avail_request_id": avail_request_id, "status": "reserved"})
        .sort("created_at", 1)
        .to_list(length=None)
    )
    if days and len(credits) != len(days):
        raise HTTPException(
            status_code=409,
            detail="Reserved credits no longer match this request. Ask the employee to apply again.",
        )
    traces: list[dict] = []
    for index, credit in enumerate(credits):
        used_on = days[index] if days else credit.get("used_on")
        await db.comp_off_credits.update_one(
            {"_id": credit["_id"]},
            {"$set": {"status": "used", "used_on": used_on, "updated_at": now}},
        )
        traces.append(
            {
                "day": used_on,
                "credit_id": credit["_id"],
                "work_date": credit.get("work_date"),
                "earn_type": credit.get("earn_type"),
                "worked_shift": credit.get("worked_shift"),
            }
        )
    return traces


def _credit_trace(credit: dict, *, pending: bool) -> dict:
    earn_type = credit.get("earn_type")
    return {
        "kind": "avail",
        "pending": pending,
        "used_on": credit.get("used_on"),
        "earned_on": credit.get("work_date"),
        "earn_type": earn_type,
        "earn_label": EARN_TYPES.get(earn_type or "", earn_type),
        "worked_shift": credit.get("worked_shift"),
        "status": "pending" if pending else credit.get("status") or "used",
    }


def _earn_trace(doc: dict, *, pending: bool) -> dict:
    earn_type = doc.get("earn_type")
    return {
        "kind": "earn",
        "pending": pending,
        "earned_on": doc.get("work_date"),
        "used_on": doc.get("used_on"),
        "earn_type": earn_type,
        "earn_label": EARN_TYPES.get(earn_type or "", earn_type),
        "worked_shift": doc.get("worked_shift"),
        "status": "pending" if pending else (doc.get("status") or "available"),
    }


async def ledger_rows(db, department_id: ObjectId | None = None) -> list[dict]:
    ufilter: dict = {}
    if department_id:
        ufilter["department_id"] = department_id
    users_list = await db.users.find(
        ufilter,
        {"employee_id": 1, "full_name": 1, "role": 1, "department_id": 1},
    ).sort([("employee_id", 1)]).to_list(length=None)
    user_ids = [u["_id"] for u in users_list]
    dept_ids = {u.get("department_id") for u in users_list if u.get("department_id")}
    departments = {}
    if dept_ids:
        async for d in db.departments.find({"_id": {"$in": list(dept_ids)}}, {"name": 1}):
            departments[d["_id"]] = d.get("name") or "?"

    counts_by_user: dict[ObjectId, dict[str, int]] = {}
    if user_ids:
        grouped = await db.comp_off_credits.aggregate(
            [
                {"$match": {"user_id": {"$in": user_ids}}},
                {"$group": {"_id": {"user_id": "$user_id", "status": "$status"}, "n": {"$sum": 1}}},
            ]
        ).to_list(length=None)
        for row in grouped:
            uid = row["_id"]["user_id"]
            status = row["_id"].get("status") or "available"
            counts_by_user.setdefault(uid, {})[status] = int(row["n"])

        pending_earn_rows = await db.comp_off_requests.aggregate(
            [
                {"$match": {"user_id": {"$in": user_ids}, "kind": "earn", "status": "pending"}},
                {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
            ]
        ).to_list(length=None)
        pending_avail_rows = await db.comp_off_requests.aggregate(
            [
                {"$match": {"user_id": {"$in": user_ids}, "kind": "avail", "status": "pending"}},
                {"$group": {"_id": "$user_id", "n": {"$sum": "$days"}}},
            ]
        ).to_list(length=None)
        for row in pending_earn_rows:
            counts_by_user.setdefault(row["_id"], {})["pending_earn"] = int(row["n"])
        for row in pending_avail_rows:
            counts_by_user.setdefault(row["_id"], {})["pending_avail_days"] = int(row.get("n") or 0)

        credits = (
            await db.comp_off_credits.find({"user_id": {"$in": user_ids}})
            .sort("created_at", -1)
            .to_list(length=1200)
        )
    else:
        credits = []

    credits_by_user: dict[ObjectId, list[dict]] = {}
    for c in credits:
        uid = c.get("user_id")
        if not uid:
            continue
        bucket = credits_by_user.setdefault(uid, [])
        if len(bucket) >= 12:
            continue
        bucket.append(
            {
                "work_date": c.get("work_date"),
                "used_on": c.get("used_on"),
                "earn_type": c.get("earn_type"),
                "earn_label": EARN_TYPES.get(c.get("earn_type") or "", c.get("earn_type")),
                "worked_shift": c.get("worked_shift"),
                "status": c.get("status"),
            }
        )

    rows = []
    for u in users_list:
        counts = counts_by_user.get(u["_id"], {})
        available = int(counts.get("available") or 0)
        reserved = int(counts.get("reserved") or 0)
        used = int(counts.get("used") or 0)
        pending_earn = int(counts.get("pending_earn") or 0)
        pending_avail = int(counts.get("pending_avail_days") or 0)
        status = "clear"
        if pending_earn or pending_avail:
            status = "pending"
        elif available:
            status = "banked"
        elif used:
            status = "used"
        rows.append(
            {
                "employee_id": u.get("employee_id"),
                "full_name": u.get("full_name"),
                "role": u.get("role"),
                "department_id": str(u["department_id"]) if u.get("department_id") else None,
                "department_name": departments.get(u.get("department_id")),
                "earned": available + reserved + used,
                "available": available,
                "reserved": reserved,
                "used": used,
                "pending_earn": pending_earn,
                "pending_avail_days": pending_avail,
                "status": status,
                "credits": credits_by_user.get(u["_id"], []),
            }
        )
    rows.sort(key=lambda r: (-int(r["earned"]), -int(r["available"]), (r.get("full_name") or "").lower()))
    return rows
