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
    return {
        "available": int(counts.get("available") or 0),
        "reserved": int(counts.get("reserved") or 0),
        "used": int(counts.get("used") or 0),
        "pending_earn": int(pending_earn),
        "pending_avail_days": int(pending_avail_days),
    }


async def reserve_oldest_credits(db, user_id: ObjectId, n: int, avail_request_id: ObjectId) -> list[ObjectId]:
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
    ids = [c["_id"] for c in credits]
    result = await db.comp_off_credits.update_many(
        {"_id": {"$in": ids}, "status": "available"},
        {"$set": {"status": "reserved", "avail_request_id": avail_request_id, "updated_at": now}},
    )
    if result.modified_count != n:
        await db.comp_off_credits.update_many(
            {"avail_request_id": avail_request_id, "status": "reserved"},
            {"$set": {"status": "available"}, "$unset": {"avail_request_id": ""}},
        )
        raise HTTPException(status_code=409, detail="Those credits were just used. Refresh and try again.")
    return ids


async def release_reserved_credits(db, avail_request_id: ObjectId) -> None:
    await db.comp_off_credits.update_many(
        {"avail_request_id": avail_request_id, "status": "reserved"},
        {
            "$set": {"status": "available", "updated_at": datetime.now(timezone.utc)},
            "$unset": {"avail_request_id": ""},
        },
    )


async def consume_reserved_credits(db, avail_request_id: ObjectId) -> None:
    await db.comp_off_credits.update_many(
        {"avail_request_id": avail_request_id, "status": "reserved"},
        {"$set": {"status": "used", "updated_at": datetime.now(timezone.utc)}},
    )
