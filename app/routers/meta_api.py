import re
from typing import Optional

from fastapi import APIRouter

from app.config import DB_NAME, MONGO_URI, REGISTER_CODE_ADMIN, REGISTER_CODE_MANAGER, SHIFT_DEFINITIONS
from app.database import get_db
from app.seed import DEFAULT_DEPARTMENTS

router = APIRouter(prefix="/api/meta", tags=["meta"])


def _cluster_host_from_uri(uri: str) -> Optional[str]:
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


@router.get("/features")
def app_features():
    """Hints for the SPA (which optional APIs exist on this server build)."""
    return {"kanban_tasks": True, "kanban_health": "/api/tasks/health"}


@router.get("/registration")
async def registration_policy():
    """Team members never need a code; manager/admin signup is enabled when server codes are set (codes are never exposed)."""
    user_count: Optional[int] = None
    dept_count: Optional[int] = None
    counts_error: Optional[str] = None
    try:
        db = get_db()
        user_count = await db.users.count_documents({})
        dept_count = await db.departments.count_documents({})
    except Exception as e:
        # Never return HTTP 500 here — the SPA needs this JSON even if Mongo is slow or counts are denied.
        counts_error = f"{type(e).__name__}: {e}"
        print(f"RotaShift /api/meta/registration counts skipped: {counts_error}")

    out = {
        "manager_registration_enabled": bool(REGISTER_CODE_MANAGER),
        "admin_registration_enabled": bool(REGISTER_CODE_ADMIN),
        "default_department_names": list(DEFAULT_DEPARTMENTS),
        "mongo_database": DB_NAME,
        "mongo_users_collection": "users",
        "mongo_cluster_host": _cluster_host_from_uri(MONGO_URI),
        "user_count": user_count,
        "department_count": dept_count,
    }
    if counts_error:
        out["counts_error"] = counts_error
    return out
