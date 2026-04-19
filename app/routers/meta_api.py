import re

from fastapi import APIRouter

from app.config import DB_NAME, MONGO_URI, REGISTER_CODE_ADMIN, REGISTER_CODE_MANAGER, SHIFT_DEFINITIONS
from app.database import get_db
from app.seed import DEFAULT_DEPARTMENTS

router = APIRouter(prefix="/api/meta", tags=["meta"])


def _cluster_host_from_uri(uri: str) -> str | None:
    """Hostname from MONGO_URI (no password) — compare with Atlas cluster to verify Render points here."""
    if not uri:
        return None
    m = re.search(r"@([^/?#]+)", uri)
    return m.group(1).strip() if m else None


@router.get("/shifts")
def shift_definitions():
    return {"shifts": SHIFT_DEFINITIONS}


@router.get("/seed-departments")
def seed_department_names():
    return {"default_department_names": DEFAULT_DEPARTMENTS}


@router.get("/registration")
async def registration_policy():
    """Team members never need a code; manager/admin signup is enabled when server codes are set (codes are never exposed)."""
    db = get_db()
    user_count = await db.users.count_documents({})
    dept_count = await db.departments.count_documents({})
    return {
        "manager_registration_enabled": bool(REGISTER_CODE_MANAGER),
        "admin_registration_enabled": bool(REGISTER_CODE_ADMIN),
        # Lets the sign-up form list departments even if GET /api/departments fails (cold start, stale token, etc.).
        "default_department_names": list(DEFAULT_DEPARTMENTS),
        # Atlas/Compass: open this database (not necessarily the name in your MONGO_URI path).
        "mongo_database": DB_NAME,
        "mongo_users_collection": "users",
        # Live counts from the same DB the app writes to — if these stay 0 after signup, Render is not using this cluster.
        "user_count": user_count,
        "department_count": dept_count,
        "mongo_cluster_host": _cluster_host_from_uri(MONGO_URI),
    }
