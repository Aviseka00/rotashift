from datetime import datetime, timezone
from hmac import compare_digest
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError, OperationFailure, PyMongoError

from app.config import DB_NAME, REGISTER_CODE_ADMIN, REGISTER_CODE_MANAGER
from app.database import get_db
from app.deps import (
    create_access_token,
    get_current_user,
    hash_password,
    require_roles,
    verify_password,
)
from app.seed import ensure_default_departments_exist

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _code_ok(expected: Optional[str], provided: Optional[str]) -> bool:
    if not expected:
        return False
    a = (provided or "").strip().encode("utf-8")
    b = expected.strip().encode("utf-8")
    if len(a) != len(b):
        return False
    return compare_digest(a, b)


def assert_registration_allowed(role: str, registration_code: Optional[str]) -> None:
    code = registration_code
    if role == "employee":
        return
    if role == "manager":
        if not REGISTER_CODE_MANAGER:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Manager self-registration is disabled (set ROTASHIFT_REGISTER_CODE_MANAGER on the server).",
            )
        if not _code_ok(REGISTER_CODE_MANAGER, code):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="Invalid registration code for manager signup.",
            )
        return
    if role == "admin":
        if not REGISTER_CODE_ADMIN:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Administrator self-registration is disabled (set ROTASHIFT_REGISTER_CODE_ADMIN on the server).",
            )
        if not _code_ok(REGISTER_CODE_ADMIN, code):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="Invalid registration code for administrator signup.",
            )
        return


class RegisterBody(BaseModel):
    employee_id: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=120)
    department_name: str = Field(..., min_length=1, max_length=120)
    role: Literal["employee", "manager", "admin"] = "employee"
    registration_code: Optional[str] = None


class LoginBody(BaseModel):
    employee_id: str
    password: str


class ChangePasswordBody(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


def _normalized_role(user: dict) -> str:
    r = user.get("role")
    if r in ("employee", "manager", "admin"):
        return r
    return "employee"


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
async def register(body: RegisterBody):
    emp = body.employee_id.strip().upper()
    if not emp:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Employee ID is required.")
    dept_name = body.department_name.strip().lower()
    if not dept_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Department is required.")

    try:
        assert_registration_allowed(body.role, body.registration_code)
        db = get_db()
        await ensure_default_departments_exist(db)
        dept = await db.departments.find_one({"name": dept_name})
        if not dept:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown department. Ask an administrator to create it or pick an existing one.",
            )
        existing = await db.users.find_one({"employee_id": emp})
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Employee ID already registered")
        pending = await db.registration_requests.find_one({"employee_id": emp, "status": "pending"})
        if pending:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A registration request for this Employee ID is already awaiting administrator approval.",
            )

        try:
            pw_hash = hash_password(body.password)
        except (ValueError, TypeError, OSError) as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not process password (try a shorter password or different characters): {e}",
            ) from e

        doc = {
            "employee_id": emp,
            "password_hash": pw_hash,
            "full_name": body.full_name.strip(),
            "department_id": dept["_id"],
            "role": body.role,
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
        }
        try:
            res = await db.registration_requests.insert_one(doc)
        except DuplicateKeyError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Employee ID already registered",
            ) from None
        except OperationFailure as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Database could not save the account (check MongoDB user permissions). {e}",
            ) from e

        return {
            "pending": True,
            "request_id": str(res.inserted_id),
            "message": "Registration submitted. You can sign in after an administrator approves your request.",
        }
    except HTTPException:
        raise
    except DuplicateKeyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee ID already registered",
        ) from None
    except PyMongoError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"MongoDB error during registration: {type(e).__name__}: {e}",
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Registration failed: {type(e).__name__}: {e}",
        ) from e


@router.post("/login")
async def login(body: LoginBody):
    db = get_db()
    emp = body.employee_id.strip().upper()
    if not emp:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid credentials")
    user = await db.users.find_one({"employee_id": emp})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid credentials — check Employee ID and password, or register if this is a new database.",
        )
    pw_hash = user.get("password_hash") or ""
    plain = body.password
    try:
        password_ok = bool(pw_hash) and verify_password(plain, pw_hash)
        # Accidental leading/trailing spaces in the password field (paste, mobile) are a common
        # "I'm sure it's correct" case; retry with strip only when input had outer whitespace.
        if not password_ok and plain.strip() != plain:
            password_ok = bool(pw_hash) and verify_password(plain.strip(), pw_hash)
    except Exception:
        password_ok = False
    if not password_ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid credentials")

    dept: Optional[dict] = None
    if user.get("department_id"):
        dept = await db.departments.find_one({"_id": user["department_id"]})

    role = _normalized_role(user)
    token = create_access_token(
        str(user["_id"]),
        role,
        user["employee_id"],
        str(user["department_id"]) if user.get("department_id") else None,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": str(user["_id"]),
            "employee_id": user["employee_id"],
            "full_name": user["full_name"],
            "role": role,
            "department_id": str(user["department_id"]) if user.get("department_id") else None,
            "department_name": dept["name"] if dept else None,
        },
    }


@router.get("/me")
async def me(user=Depends(get_current_user)):
    db = get_db()
    dept = None
    if user.get("department_id"):
        dept = await db.departments.find_one({"_id": ObjectId(user["department_id"])})
    return {
        "id": str(user["_id"]),
        "employee_id": user["employee_id"],
        "full_name": user["full_name"],
        "role": _normalized_role(user),
        "department_id": user["department_id"],
        "department_name": dept["name"] if dept else None,
    }


@router.post("/change-password")
async def change_password(body: ChangePasswordBody, user=Depends(get_current_user)):
    """Let any signed-in user securely replace their own password."""
    current_hash = user.get("password_hash") or ""
    try:
        current_ok = bool(current_hash) and verify_password(body.current_password, current_hash)
    except Exception:
        current_ok = False
    if not current_ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect.")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose a new password different from your current password.")

    new_hash = hash_password(body.new_password)
    db = get_db()
    result = await db.users.update_one({"_id": ObjectId(user["_id"])}, {"$set": {"password_hash": new_hash}})
    if result.matched_count != 1:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User account was not found.")
    return {"ok": True, "message": "Password changed successfully."}


@router.get("/registration-requests")
async def list_registration_requests(user=Depends(require_roles("admin"))):
    db = get_db()
    departments = {d["_id"]: d.get("name", "") async for d in db.departments.find({}, {"name": 1})}
    requests = []
    async for item in db.registration_requests.find({"status": "pending"}).sort("created_at", 1):
        requests.append({
            "id": str(item["_id"]), "employee_id": item["employee_id"], "full_name": item["full_name"],
            "role": item.get("role", "employee"), "department_name": departments.get(item.get("department_id"), ""),
            "created_at": item.get("created_at"),
        })
    return {"requests": requests}


@router.post("/registration-requests/{request_id}/approve")
async def approve_registration(request_id: str, user=Depends(require_roles("admin"))):
    db = get_db()
    try:
        oid = ObjectId(request_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid registration request id.")
    item = await db.registration_requests.find_one({"_id": oid, "status": "pending"})
    if not item:
        raise HTTPException(status_code=404, detail="Pending registration request not found.")
    if await db.users.find_one({"employee_id": item["employee_id"]}):
        raise HTTPException(status_code=409, detail="This Employee ID already has an account.")
    now = datetime.now(timezone.utc)
    try:
        result = await db.users.insert_one({
            "employee_id": item["employee_id"], "password_hash": item["password_hash"],
            "full_name": item["full_name"], "department_id": item["department_id"],
            "role": item.get("role", "employee"), "created_at": now, "approved_by": ObjectId(user["_id"]),
        })
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="This Employee ID already has an account.") from None
    await db.registration_requests.update_one(
        {"_id": oid},
        {"$set": {"status": "approved", "decided_at": now, "decided_by": ObjectId(user["_id"]), "user_id": result.inserted_id}},
    )
    return {"ok": True, "message": f"{item['full_name']} can now sign in."}


@router.post("/registration-requests/{request_id}/reject")
async def reject_registration(request_id: str, user=Depends(require_roles("admin"))):
    db = get_db()
    try:
        oid = ObjectId(request_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid registration request id.")
    now = datetime.now(timezone.utc)
    result = await db.registration_requests.update_one(
        {"_id": oid, "status": "pending"},
        {"$set": {"status": "rejected", "decided_at": now, "decided_by": ObjectId(user["_id"])}},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=404, detail="Pending registration request not found.")
    return {"ok": True, "message": "Registration request rejected."}
