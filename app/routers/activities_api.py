from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import get_db
from app.deps import get_current_user

router = APIRouter(prefix="/api/activities", tags=["activities"])


def _iso(dt: Any) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def _safe_day(raw: str) -> str:
    value = (raw or "").strip()
    if len(value) < 10:
        raise HTTPException(status_code=400, detail="Invalid date")
    try:
        datetime.strptime(value[:10], "%Y-%m-%d")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date")
    return value[:10]


def _dept_scope(user: dict, department_id: Optional[str]) -> ObjectId:
    role = user.get("role")
    if role == "admin":
        if not department_id:
            raise HTTPException(status_code=400, detail="department_id is required for administrators")
        try:
            return ObjectId(department_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid department_id")
    if not user.get("department_id"):
        raise HTTPException(status_code=400, detail="User has no department")
    return ObjectId(user["department_id"])


async def _author_view(db, uid: ObjectId) -> dict:
    doc = await db.users.find_one({"_id": uid})
    if not doc:
        return {"id": str(uid), "employee_id": "?", "full_name": "Unknown"}
    return {"id": str(uid), "employee_id": doc.get("employee_id", "?"), "full_name": doc.get("full_name", "?")}


async def _entry_out(db, doc: dict) -> dict:
    author = await _author_view(db, doc["created_by"])
    comments = []
    for c in doc.get("comments", []):
        c_author = await _author_view(db, c["created_by"])
        comments.append(
            {
                "id": str(c.get("_id") or ""),
                "comment": c.get("comment", ""),
                "created_at": _iso(c.get("created_at")),
                "created_by": c_author,
            }
        )
    return {
        "id": str(doc["_id"]),
        "department_id": str(doc["department_id"]),
        "activity_date": doc["activity_date"],
        "title": doc["title"],
        "details": doc["details"],
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
        "created_by": author,
        "comments": comments,
    }


class ActivityCreateBody(BaseModel):
    activity_date: str = Field(..., description="YYYY-MM-DD")
    title: str = Field(..., min_length=1, max_length=160)
    details: str = Field(..., min_length=1, max_length=4000)
    department_id: Optional[str] = None


class ActivityCommentBody(BaseModel):
    comment: str = Field(..., min_length=1, max_length=1200)


@router.get("")
@router.get("/")
async def list_activities(
    department_id: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    db = get_db()
    dept_oid = _dept_scope(user, department_id)
    cur = db.activities.find({"department_id": dept_oid}).sort([("activity_date", -1), ("created_at", -1)])
    out = []
    async for doc in cur:
        out.append(await _entry_out(db, doc))
    return {"entries": out}


@router.post("")
@router.post("/")
async def create_activity(body: ActivityCreateBody, user=Depends(get_current_user)):
    db = get_db()
    dept_oid = _dept_scope(user, body.department_id)
    now = datetime.now(timezone.utc)
    doc = {
        "department_id": dept_oid,
        "activity_date": _safe_day(body.activity_date),
        "title": body.title.strip(),
        "details": body.details.strip(),
        "created_by": ObjectId(user["_id"]),
        "created_at": now,
        "updated_at": now,
        "comments": [],
    }
    res = await db.activities.insert_one(doc)
    created = await db.activities.find_one({"_id": res.inserted_id})
    return await _entry_out(db, created)


@router.post("/{activity_id}/comments")
async def add_comment(activity_id: str, body: ActivityCommentBody, user=Depends(get_current_user)):
    db = get_db()
    try:
        oid = ObjectId(activity_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid activity id")
    doc = await db.activities.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Activity not found")

    if user.get("role") != "admin":
        if not user.get("department_id"):
            raise HTTPException(status_code=403, detail="No department")
        if str(doc["department_id"]) != str(user["department_id"]):
            raise HTTPException(status_code=403, detail="Wrong department")

    now = datetime.now(timezone.utc)
    comment_doc = {
        "_id": ObjectId(),
        "comment": body.comment.strip(),
        "created_by": ObjectId(user["_id"]),
        "created_at": now,
    }
    await db.activities.update_one(
        {"_id": oid},
        {"$push": {"comments": comment_doc}, "$set": {"updated_at": now}},
    )
    fresh = await db.activities.find_one({"_id": oid})
    return await _entry_out(db, fresh)
