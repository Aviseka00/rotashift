"""Department project planning: admin-created projects visible to the assigned team."""

from datetime import date, datetime, timezone
import time
from typing import List, Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import get_db
from app.deps import get_current_user, require_roles

router = APIRouter(prefix="/api/projects", tags=["projects"])
_PROJECT_CACHE_TTL_S = 4.0
_project_cache: dict[tuple[str, bool], tuple[float, dict]] = {}


def invalidate_project_cache(department_id=None) -> None:
    if department_id is None:
        _project_cache.clear()
        return
    needle = str(department_id)
    for key in [key for key in _project_cache if key[0] == needle]:
        _project_cache.pop(key, None)


def _oid(raw: str, label: str = "id") -> ObjectId:
    try:
        return ObjectId(raw)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {label}") from None


def _dept(user: dict, department_id: Optional[str]) -> ObjectId:
    if user.get("role") == "admin":
        if not department_id:
            raise HTTPException(status_code=400, detail="department_id is required for administrators")
        return _oid(department_id, "department_id")
    if not user.get("department_id"):
        raise HTTPException(status_code=400, detail="User has no department")
    return _oid(str(user["department_id"]), "department_id")


def _iso(value):
    return value.isoformat() if isinstance(value, (datetime, date)) else value


async def _valid_members(db, department_id: ObjectId, ids: List[str]) -> List[str]:
    requested = list(dict.fromkeys(str(x).strip().upper() for x in ids if str(x).strip()))
    if not requested:
        return []
    valid = {
        row["employee_id"]
        async for row in db.users.find(
            {"department_id": department_id, "employee_id": {"$in": requested}}, {"employee_id": 1}
        )
    }
    return [employee_id for employee_id in requested if employee_id in valid]


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    summary: str = Field("", max_length=3000)
    start_date: date
    due_date: date
    assignee_employee_ids: List[str] = Field(default_factory=list)
    department_id: str


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=160)
    summary: Optional[str] = Field(None, max_length=3000)
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    status: Optional[Literal["active", "closed"]] = None
    assignee_employee_ids: Optional[List[str]] = None


def _project_out(doc: dict, names: dict, counts: dict) -> dict:
    project_counts = counts.get(doc["_id"], {})
    total = int(project_counts.get("total", 0))
    done = int(project_counts.get("done", 0))
    assignees = doc.get("assignee_employee_ids") or []
    return {
        "id": str(doc["_id"]),
        "department_id": str(doc["department_id"]),
        "name": doc.get("name") or "Untitled project",
        "summary": doc.get("summary") or "",
        "start_date": _iso(doc.get("start_date")),
        "due_date": _iso(doc.get("due_date")),
        "status": doc.get("status") or "active",
        "assignee_employee_ids": assignees,
        "assignee_names": [names.get(employee_id, employee_id) for employee_id in assignees],
        "task_total": total,
        "task_done": done,
        "progress": round((done / total) * 100) if total else 0,
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
    }


@router.get("")
@router.get("/")
async def list_projects(
    department_id: Optional[str] = Query(None),
    include_closed: bool = Query(True),
    user=Depends(get_current_user),
):
    db = get_db()
    dept_id = _dept(user, department_id)
    cache_key = (str(dept_id), include_closed)
    cached = _project_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _PROJECT_CACHE_TTL_S:
        return cached[1]
    query = {"department_id": dept_id}
    if not include_closed:
        query["status"] = {"$ne": "closed"}
    docs = await db.projects.find(query).sort([("status", 1), ("due_date", 1)]).to_list(length=250)
    project_ids = [doc["_id"] for doc in docs]
    ids = {employee_id for doc in docs for employee_id in (doc.get("assignee_employee_ids") or [])}
    names = {}
    if ids:
        async for person in db.users.find({"employee_id": {"$in": list(ids)}}, {"employee_id": 1, "full_name": 1}):
            names[person["employee_id"]] = person.get("full_name") or person["employee_id"]
    counts = {}
    if project_ids:
        pipeline = [
            {"$match": {"project_id": {"$in": project_ids}}},
            {"$group": {"_id": "$project_id", "total": {"$sum": 1}, "done": {"$sum": {"$cond": [{"$eq": ["$column", "done"]}, 1, 0]}}}},
        ]
        async for row in db.tasks.aggregate(pipeline):
            counts[row["_id"]] = row
    payload = {"projects": [_project_out(doc, names, counts) for doc in docs]}
    _project_cache[cache_key] = (time.monotonic(), payload)
    return payload


@router.post("")
@router.post("/")
async def create_project(body: ProjectCreate, user=Depends(require_roles("admin"))):
    db = get_db()
    dept_id = _oid(body.department_id, "department_id")
    if body.due_date < body.start_date:
        raise HTTPException(status_code=400, detail="Due date must be on or after the start date")
    if not await db.departments.find_one({"_id": dept_id}):
        raise HTTPException(status_code=404, detail="Department not found")
    members = await _valid_members(db, dept_id, body.assignee_employee_ids)
    now = datetime.now(timezone.utc)
    doc = {
        "department_id": dept_id,
        "name": body.name.strip(),
        "summary": body.summary.strip(),
        "start_date": body.start_date.isoformat(),
        "due_date": body.due_date.isoformat(),
        "status": "active",
        "assignee_employee_ids": members,
        "created_by": ObjectId(user["_id"]),
        "created_at": now,
        "updated_at": now,
    }
    result = await db.projects.insert_one(doc)
    doc["_id"] = result.inserted_id
    invalidate_project_cache(dept_id)
    return _project_out(doc, {}, {})


@router.patch("/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate, user=Depends(require_roles("admin"))):
    db = get_db()
    oid = _oid(project_id, "project id")
    doc = await db.projects.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found")
    raw = body.model_dump(exclude_unset=True)
    if not raw:
        raise HTTPException(status_code=400, detail="No changes supplied")
    start = raw.get("start_date", doc.get("start_date"))
    due = raw.get("due_date", doc.get("due_date"))
    start_text, due_text = _iso(start), _iso(due)
    if due_text < start_text:
        raise HTTPException(status_code=400, detail="Due date must be on or after the start date")
    updates = {"updated_at": datetime.now(timezone.utc)}
    for key in ("name", "summary", "status"):
        if key in raw:
            updates[key] = raw[key].strip() if isinstance(raw[key], str) else raw[key]
    if "start_date" in raw:
        updates["start_date"] = start_text
    if "due_date" in raw:
        updates["due_date"] = due_text
    if "assignee_employee_ids" in raw:
        updates["assignee_employee_ids"] = await _valid_members(db, doc["department_id"], raw["assignee_employee_ids"] or [])
    await db.projects.update_one({"_id": oid}, {"$set": updates})
    invalidate_project_cache(doc["department_id"])
    return {"ok": True}
