import os
from datetime import datetime, timezone

from app.config import SHIFT_DEFINITIONS
from app.database import get_db
from app.deps import hash_password


DEFAULT_DEPARTMENTS = ["rota", "cholera", "malaria", "shigella"]


async def ensure_indexes_and_seed():
    db = get_db()
    await db.departments.create_index("name", unique=True)
    await db.users.create_index("employee_id", unique=True)
    await db.shifts.create_index([("department_id", 1), ("user_id", 1), ("date", 1)], unique=True)
    await db.leave_requests.create_index([("department_id", 1), ("status", 1)])
    await db.shift_change_requests.create_index([("department_id", 1), ("status", 1)])

    if await db.departments.count_documents({}) == 0:
        now = datetime.now(timezone.utc)
        await db.departments.insert_many(
            [{"name": n.lower().strip(), "created_at": now} for n in DEFAULT_DEPARTMENTS]
        )

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
