from datetime import datetime, timezone
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from app.database import get_db
from app.deps import get_current_user, hash_password, require_roles

router = APIRouter(prefix="/api/users", tags=["users"])


class UserCreateBody(BaseModel):
    employee_id: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=120)
    department_id: str
    role: Literal["employee", "manager", "admin"]
    gmail: Optional[str] = Field(None, max_length=254)

    @field_validator("gmail")
    @classmethod
    def validate_gmail(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_gmail(value)


class UserResetPasswordBody(BaseModel):
    password: str = Field(..., min_length=6, max_length=128)


class GmailBody(BaseModel):
    gmail: Optional[str] = Field(None, max_length=254)

    @field_validator("gmail")
    @classmethod
    def validate_gmail(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_gmail(value)


def _normalize_gmail(value: Optional[str]) -> Optional[str]:
    email = (value or "").strip().lower()
    if not email:
        return None
    local, separator, domain = email.rpartition("@")
    if not separator or not local or domain not in {"gmail.com", "googlemail.com"}:
        raise ValueError("Enter a valid Gmail address ending in @gmail.com")
    if any(ch.isspace() for ch in email) or "." not in domain:
        raise ValueError("Enter a valid Gmail address")
    return email


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

    users = await db.users.find(
        q,
        {
            "employee_id": 1,
            "full_name": 1,
            "role": 1,
            "department_id": 1,
            "gmail": 1,
            "roster_category": 1,
            "roster_fixed_g_non_batch": 1,
        },
    ).sort("employee_id", 1).to_list(length=None)
    dept_ids = {u["department_id"] for u in users if u.get("department_id")}
    departments = {}
    if dept_ids:
        departments = {
            d["_id"]: d.get("name", "")
            for d in await db.departments.find({"_id": {"$in": list(dept_ids)}}, {"name": 1}).to_list(length=None)
        }

    out = []
    for u in users:
        dept_name = departments.get(u.get("department_id"))
        out.append(
            {
                "id": str(u["_id"]),
                "employee_id": u["employee_id"],
                "full_name": u["full_name"],
                "role": u["role"],
                "department_id": str(u["department_id"]) if u.get("department_id") else None,
                "department_name": dept_name,
                "gmail": u.get("gmail"),
                "roster_category": u.get("roster_category") or None,
                "roster_fixed_g_non_batch": bool(u.get("roster_fixed_g_non_batch")),
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
        "gmail": body.gmail,
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
        "gmail": body.gmail,
    }


@router.get("/meet-directory")
async def meet_directory(user=Depends(get_current_user)):
    """Return callable colleagues without exposing anyone outside the user's permitted scope."""
    db = get_db()
    if user.get("role") == "admin":
        query = {"gmail": {"$nin": [None, ""]}}
    elif user.get("department_id"):
        query = {
            "department_id": ObjectId(user["department_id"]),
            "gmail": {"$nin": [None, ""]},
        }
    else:
        return {"users": []}
    rows = await db.users.find(
        query, {"employee_id": 1, "full_name": 1, "department_id": 1, "gmail": 1}
    ).sort("full_name", 1).to_list(length=500)
    return {
        "users": [
            {
                "id": str(row["_id"]),
                "employee_id": row.get("employee_id", ""),
                "full_name": row.get("full_name", ""),
                "gmail": row.get("gmail"),
                "is_me": str(row["_id"]) == str(user["_id"]),
            }
            for row in rows
        ]
    }


@router.patch("/me/gmail")
async def update_my_gmail(body: GmailBody, user=Depends(get_current_user)):
    db = get_db()
    await db.users.update_one({"_id": ObjectId(user["_id"])}, {"$set": {"gmail": body.gmail}})
    return {"ok": True, "gmail": body.gmail}


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


ROSTER_CATEGORY_CODES = ("M", "S", "I", "G", "20", "W")


class RosterProfileBody(BaseModel):
    roster_category: Optional[str] = None
    roster_fixed_g_non_batch: Optional[bool] = None


class RosterCategoryItem(BaseModel):
    employee_id: str
    roster_category: str
    roster_fixed_g_non_batch: Optional[bool] = None


class RosterCategoryBulkBody(BaseModel):
    items: list[RosterCategoryItem]


def _clean_category(value: Optional[str]) -> Optional[str]:
    cat = (value or "").strip().upper()
    if not cat:
        return None
    if cat not in ROSTER_CATEGORY_CODES:
        raise HTTPException(status_code=400, detail="Category must be one of M, S, I, G, 20, W")
    return cat


@router.patch("/{target_id}/roster-profile")
async def set_roster_profile(
    target_id: str,
    body: RosterProfileBody,
    user=Depends(require_roles("admin")),
):
    """Admin sets the auto-roster category (M/S/I/G/20/W) and the G-in-Non-Batch flag."""
    db = get_db()
    try:
        oid = ObjectId(target_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    update: dict = {}
    if body.roster_category is not None:
        update["roster_category"] = _clean_category(body.roster_category)
    if body.roster_fixed_g_non_batch is not None:
        update["roster_fixed_g_non_batch"] = bool(body.roster_fixed_g_non_batch)
    if not update:
        raise HTTPException(status_code=400, detail="Nothing to update")
    r = await db.users.update_one({"_id": oid}, {"$set": update})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True, **update}


@router.post("/roster-categories")
async def bulk_set_roster_categories(
    body: RosterCategoryBulkBody,
    user=Depends(require_roles("admin")),
):
    """Set many categories at once (e.g. pasted from the staff sheet)."""
    db = get_db()
    updated, missing = 0, []
    for item in body.items:
        update: dict = {"roster_category": _clean_category(item.roster_category)}
        if item.roster_fixed_g_non_batch is not None:
            update["roster_fixed_g_non_batch"] = bool(item.roster_fixed_g_non_batch)
        r = await db.users.update_one({"employee_id": item.employee_id.strip().upper()}, {"$set": update})
        if r.matched_count:
            updated += 1
        else:
            missing.append(item.employee_id)
    return {"updated": updated, "missing": missing}
