import asyncio
import os
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import OperationFailure

from app.config import SHIFT_DEFINITIONS
from app.database import get_db
from app.deps import hash_password


DEFAULT_DEPARTMENTS = ["rota", "cholera", "malaria", "shigella"]


async def ensure_default_departments_exist(db) -> None:
    """Create seed departments if missing (idempotent). Safe to call from registration, not only startup."""
    now = datetime.now(timezone.utc)
    writes = []
    for n in DEFAULT_DEPARTMENTS:
        name = (n or "").lower().strip()
        if not name:
            continue
        writes.append(db.departments.update_one({"name": name}, {"$setOnInsert": {"name": name, "created_at": now}}, upsert=True))
    try:
        await asyncio.gather(*writes)
    except OperationFailure:
        raise


async def ensure_indexes_and_seed():
    db = get_db()
    await asyncio.gather(
        db.departments.create_index("name", unique=True),
        db.users.create_index("employee_id", unique=True),
        db.registration_requests.create_index([("employee_id", ASCENDING), ("status", ASCENDING)]),
        db.registration_requests.create_index([("status", ASCENDING), ("created_at", DESCENDING)]),
        db.users.create_index([("department_id", ASCENDING), ("employee_id", ASCENDING)]),
        db.shifts.create_index([("department_id", ASCENDING), ("user_id", ASCENDING), ("date", ASCENDING)], unique=True),
        db.shifts.create_index([("department_id", ASCENDING), ("date", ASCENDING)]),
        db.leave_requests.create_index([("department_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)]),
        db.leave_requests.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)]),
        db.shift_change_requests.create_index([("department_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)]),
        db.shift_change_requests.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)]),
        db.tasks.create_index([("department_id", ASCENDING), ("column", ASCENDING), ("priority", DESCENDING)]),
        db.activities.create_index([("department_id", ASCENDING), ("activity_date", DESCENDING), ("created_at", DESCENDING)]),
        db.activity_uploads.create_index([("department_id", ASCENDING), ("uploaded_at", DESCENDING)]),
    )

    await ensure_default_departments_exist(db)

    # Comma- or space-separated IDs, e.g. ROTASHIFT_ADMIN_EMPLOYEE_ID=001,007,MASTER
    # For each existing user: set role to admin. For missing users: insert if ROTASHIFT_ADMIN_PASSWORD is set.
    admin_raw = (os.getenv("ROTASHIFT_ADMIN_EMPLOYEE_ID") or "").strip()
    admin_pw = os.getenv("ROTASHIFT_ADMIN_PASSWORD")
    if admin_raw:
        parts = [p.strip().upper() for p in admin_raw.replace(",", " ").split() if p.strip()]
        seen = set()
        dept = await db.departments.find_one()
        for emp_u in parts:
            if emp_u in seen:
                continue
            seen.add(emp_u)
            exists = await db.users.find_one({"employee_id": emp_u})
            if exists and exists.get("role") != "admin":
                await db.users.update_one({"_id": exists["_id"]}, {"$set": {"role": "admin"}})
            elif not exists and admin_pw and dept:
                await db.users.insert_one(
                    {
                        "employee_id": emp_u,
                        "password_hash": hash_password(admin_pw),
                        "full_name": "System Administrator",
                        "department_id": dept["_id"],
                        "role": "admin",
                        "created_at": datetime.now(timezone.utc),
                    }
                )


def shift_catalog_public():
    return SHIFT_DEFINITIONS
