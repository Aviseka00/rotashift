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


def _normalized_role(user: dict) -> str:
    r = user.get("role")
    if r in ("employee", "manager", "admin"):
        return r
    return "employee"


@router.post("/register")
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
            "created_at": datetime.now(timezone.utc),
        }
        try:
            res = await db.users.insert_one(doc)
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

        print(f"RotaShift REGISTER_OK database={DB_NAME!r} employee_id={emp!r} user_id={res.inserted_id!r}")
        try:
            token = create_access_token(
                str(res.inserted_id),
                body.role,
                emp,
                str(dept["_id"]),
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Account was created but sign-in token could not be issued: {type(e).__name__}: {e}",
            ) from e

        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "id": str(res.inserted_id),
                "employee_id": emp,
                "full_name": doc["full_name"],
                "role": body.role,
                "department_id": str(dept["_id"]),
                "department_name": dept["name"],
            },
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
