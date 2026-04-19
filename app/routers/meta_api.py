from fastapi import APIRouter

from app.config import DB_NAME, REGISTER_CODE_ADMIN, REGISTER_CODE_MANAGER, SHIFT_DEFINITIONS
from app.seed import DEFAULT_DEPARTMENTS

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("/shifts")
def shift_definitions():
    return {"shifts": SHIFT_DEFINITIONS}


@router.get("/seed-departments")
def seed_department_names():
    return {"default_department_names": DEFAULT_DEPARTMENTS}


@router.get("/registration")
def registration_policy():
    """Team members never need a code; manager/admin signup is enabled when server codes are set (codes are never exposed)."""
    return {
        "manager_registration_enabled": bool(REGISTER_CODE_MANAGER),
        "admin_registration_enabled": bool(REGISTER_CODE_ADMIN),
        # Lets the sign-up form list departments even if GET /api/departments fails (cold start, stale token, etc.).
        "default_department_names": list(DEFAULT_DEPARTMENTS),
        # Atlas/Compass: open this database (not necessarily the name in your MONGO_URI path).
        "mongo_database": DB_NAME,
        "mongo_users_collection": "users",
    }
