from fastapi import APIRouter, Depends

from app.database import get_db
from app.deps import require_roles

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _serialize_user(u: dict) -> dict:
    d = dict(u)
    d["id"] = str(d.pop("_id"))
    d.pop("password_hash", None)
    if d.get("department_id"):
        d["department_id"] = str(d["department_id"])
    if d.get("assigned_by"):
        d["assigned_by"] = str(d["assigned_by"])
    return d


@router.get("/export")
async def export_all(user=Depends(require_roles("admin"))):
    db = get_db()
    departments = []
    async for x in db.departments.find():
        x["id"] = str(x.pop("_id"))
        departments.append(x)

    users = []
    async for x in db.users.find():
        users.append(_serialize_user(x))

    shifts = []
    async for x in db.shifts.find():
        x["id"] = str(x.pop("_id"))
        x["department_id"] = str(x["department_id"])
        x["user_id"] = str(x["user_id"])
        if x.get("assigned_by"):
            x["assigned_by"] = str(x["assigned_by"])
        shifts.append(x)

    leaves = []
    async for x in db.leave_requests.find():
        x["id"] = str(x.pop("_id"))
        x["user_id"] = str(x["user_id"])
        x["department_id"] = str(x["department_id"])
        if x.get("decided_by"):
            x["decided_by"] = str(x["decided_by"])
        leaves.append(x)

    changes = []
    async for x in db.shift_change_requests.find():
        x["id"] = str(x.pop("_id"))
        x["user_id"] = str(x["user_id"])
        x["department_id"] = str(x["department_id"])
        if x.get("decided_by"):
            x["decided_by"] = str(x["decided_by"])
        changes.append(x)

    task_rows = []
    async for x in db.tasks.find():
        x["id"] = str(x.pop("_id"))
        x["department_id"] = str(x["department_id"])
        if x.get("created_by"):
            x["created_by"] = str(x["created_by"])
        task_rows.append(x)

    return {
        "departments": departments,
        "users": users,
        "shifts": shifts,
        "leave_requests": leaves,
        "shift_change_requests": changes,
        "tasks": task_rows,
    }
