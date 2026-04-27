from datetime import datetime, timezone
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.database import get_db
from app.deps import get_current_user, hash_password, require_roles

router = APIRouter(prefix="/api/users", tags=["users"])


class UserCreateBody(BaseModel):
    employee_id: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=120)
    department_id: str
    role: Literal["employee", "manager", "admin"]


class UserResetPasswordBody(BaseModel):
    password: str = Field(..., min_length=6, max_length=128)


@router.get("")
async def list_users(
    department_id: Optional[str] = Query(None),
    user=Depends(require_roles("admin", "manager")),
):
    db = get_db()
    if user["role"] == "manager":
        if not user.get("department_id"):
            return {"users": []}
        dept_oid = ObjectId(user["department_id"])
        q = {"department_id": dept_oid}
    else:
        if department_id:
            try:
                dept_oid = ObjectId(department_id)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid department_id")
            q = {"department_id": dept_oid}
        else:
            q = {}

    out = []
    async for u in db.users.find(q).sort("employee_id", 1):
        dept_name = None
        if u.get("department_id"):
            dep = await db.departments.find_one({"_id": u["department_id"]})
            if dep:
                dept_name = dep["name"]
        out.append(
            {
                "id": str(u["_id"]),
                "employee_id": u["employee_id"],
                "full_name": u["full_name"],
                "role": u["role"],
                "department_id": str(u["department_id"]) if u.get("department_id") else None,
                "department_name": dept_name,
            }
        )
    return {"users": out}


@router.post("")
async def create_user(
    body: UserCreateBody,
    user=Depends(require_roles("admin")),
):
    db = get_db()
    emp = body.employee_id.strip().upper()
    if await db.users.find_one({"employee_id": emp}):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Employee ID already exists")
    try:
        dept_oid = ObjectId(body.department_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid department_id")
    dept = await db.departments.find_one({"_id": dept_oid})
    if not dept:
        raise HTTPException(status_code=400, detail="Department not found")
    doc = {
        "employee_id": emp,
        "password_hash": hash_password(body.password),
        "full_name": body.full_name.strip(),
        "department_id": dept_oid,
        "role": body.role,
        "created_at": datetime.now(timezone.utc),
        "created_by_admin": ObjectId(user["_id"]),
    }
    res = await db.users.insert_one(doc)
    return {
        "id": str(res.inserted_id),
        "employee_id": emp,
        "full_name": doc["full_name"],
        "role": body.role,
        "department_id": str(dept_oid),
    }


@router.delete("/{target_id}")
async def delete_user(
    target_id: str,
    user=Depends(require_roles("admin")),
):
    db = get_db()
    try:
        oid = ObjectId(target_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    target = await db.users.find_one({"_id": oid})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if str(target["_id"]) == user["_id"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    await db.shifts.delete_many({"user_id": oid})
    await db.leave_requests.delete_many({"user_id": oid})
    await db.shift_change_requests.delete_many({"user_id": oid})
    await db.users.delete_one({"_id": oid})
    return {"ok": True}


@router.patch("/{target_id}/role")
async def set_role(
    target_id: str,
    body: dict,
    user=Depends(require_roles("admin")),
):
    role = body.get("role")
    if role not in ("admin", "manager", "employee"):
        raise HTTPException(status_code=400, detail="Invalid role")
    db = get_db()
    try:
        oid = ObjectId(target_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    r = await db.users.update_one({"_id": oid}, {"$set": {"role": role}})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True, "role": role}


@router.patch("/{target_id}/password")
async def reset_user_password(
    target_id: str,
    body: UserResetPasswordBody,
    user=Depends(require_roles("admin")),
):
    db = get_db()
    try:
        oid = ObjectId(target_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")

    target = await db.users.find_one({"_id": oid})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if str(target["_id"]) == user["_id"]:
        raise HTTPException(
            status_code=400,
            detail="You cannot reset your own password from this admin action.",
        )

    new_hash = hash_password(body.password)
    await db.users.update_one({"_id": oid}, {"$set": {"password_hash": new_hash}})
    return {"ok": True}
