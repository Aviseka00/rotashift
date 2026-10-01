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
    "joint_ca": "Overnight C then A next morning",
    "joint_ac": "A then C later the same day",
}

JOINT_PAIRS = {
    "joint_ab": frozenset({"A", "B"}),
    "joint_bc": frozenset({"B", "C"}),
    "joint_ca": frozenset({"C", "A"}),
    "joint_ac": frozenset({"A", "C"}),
}
OVERNIGHT_EARN_TYPES = frozenset({"joint_ca"})
SAME_DAY_JOINT_TYPES = frozenset({"joint_ab", "joint_bc", "joint_ac"})
ROSTER_DUAL_UNSET = {
    "extra_shift_code": "",
    "dual_pair": "",
    "overnight_to_date": "",
    "overnight_to_code": "",
    "overnight_from_date": "",
    "overnight_from_code": "",
}

# Extra duty that can generate a credit: A, B, C, or G general duty.
WORKED_SHIFT_CODES = frozenset({"A", "B", "C", "G"})
# Using a banked credit can convert these roster days to paid CO.
AVAIL_SHIFT_CODES = frozenset({"L", "A", "B", "C", "G", "WO"})
REST_EARN_TYPES = frozenset({"worked_wo", "worked_leave", "worked_holiday"})
JOINT_EARN_TYPES = frozenset(JOINT_PAIRS)
MAX_AVAIL_DAYS = 31
_SHIFT_ORDER = {"A": 0, "B": 1, "C": 2, "G": 3}


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
        raise HTTPException(status_code=400, detail="The extra duty must be shift A, B, C, or G")
    pair = JOINT_PAIRS.get(et)
    if pair and ws not in pair:
        if et == "joint_ca":
            raise HTTPException(status_code=400, detail="For overnight C then A, the extra shift must be A or C")
        a, b = sorted(pair, key=lambda c: _SHIFT_ORDER.get(c, 9))
        raise HTTPException(status_code=400, detail=f"For {a}+{b} extra duty, the extra shift must be {a} or {b}")
    return et, ws


def joint_pair_label(earn_type: str) -> str:
    labels = {"joint_ab": "A+B", "joint_bc": "B+C", "joint_ca": "C→A", "joint_ac": "A+C"}
    if earn_type in labels:
        return labels[earn_type]
    pair = JOINT_PAIRS.get(earn_type) or frozenset()
    return "+".join(sorted(pair, key=lambda c: _SHIFT_ORDER.get(c, 9)))


def pair_label_for_codes(primary: str, extra: str) -> str:
    p = str(primary or "").strip().upper()
    e = str(extra or "").strip().upper()
    codes = {c for c in (p, e) if c}
    if p == "A" and e == "C":
        return "A+C"
    if codes == {"A", "B"}:
        return "A+B"
    if codes == {"B", "C"}:
        return "B+C"
    if codes == {"C", "A"}:
        return "C+A"
    if p and e and p != e:
        return f"{p}+{e}"
    return p or e


def iso_offset(day_iso: str, days: int) -> str:
    return (datetime.strptime(str(day_iso)[:10], "%Y-%m-%d").date() + timedelta(days=days)).isoformat()


def overnight_ca_span(work_date: str, current_code: str, worked_shift: str) -> tuple[str, str]:
    """C is overnight into the next calendar morning A. Return (c_date, a_date)."""
    day = str(work_date or "")[:10]
    current = (current_code or "").strip().upper()
    extra = (worked_shift or "").strip().upper()
    if current == "A" or (current not in {"C"} and extra == "C"):
        return iso_offset(day, -1), day
    return day, iso_offset(day, 1)


def overnight_follow_label(extra: str) -> str:
    extra_u = (extra or "").strip().upper()
    if extra_u in {"B", "C"}:
        return f"C→A+{extra_u}"
    return "C→A"


def dual_roster_codes(earn_type: str, current_code: str, worked_shift: str) -> tuple[str, str, str]:
    """Return (primary roster code, extra shift, A+B label) for a joint extra-duty day."""
    pair = JOINT_PAIRS.get(earn_type)
    extra = (worked_shift or "").strip().upper()
    current = (current_code or "").strip().upper()
    if not pair:
        return current or extra, "", ""
    if extra not in pair:
        extra = sorted(pair, key=lambda c: _SHIFT_ORDER.get(c, 9))[-1]
    if current in pair and current != extra:
        primary = current
    else:
        primary = next(c for c in sorted(pair, key=lambda c: _SHIFT_ORDER.get(c, 9)) if c != extra)
    return primary, extra, joint_pair_label(earn_type)


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


async def require_avail_days(db, user_id: ObjectId, department_id: ObjectId, days: list[str]) -> None:
    """Comp-off can be applied against leave (L), week off (WO), or a rostered A, B, C, or G shift."""
    if not days:
        return
    found: dict[str, str] = {}
    async for shift in db.shifts.find(
        {"user_id": user_id, "department_id": department_id, "date": {"$in": days}},
        {"date": 1, "shift_code": 1},
    ):
        found[str(shift.get("date") or "")] = str((shift or {}).get("shift_code") or "").strip().upper()
    not_ok = [day for day in days if found.get(day) not in AVAIL_SHIFT_CODES]
    if not_ok:
        shown = ", ".join(not_ok[:6])
        extra = f" (+{len(not_ok) - 6} more)" if len(not_ok) > 6 else ""
        raise HTTPException(
            status_code=400,
            detail=(
                "Comp-off can be used against leave (L), week off (WO), or a rostered A, B, C, or G shift. "
                f"These dates are not eligible: {shown}{extra}"
            ),
        )


async def require_leave_days(db, user_id: ObjectId, department_id: ObjectId, days: list[str]) -> None:
    await require_avail_days(db, user_id, department_id, days)


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
