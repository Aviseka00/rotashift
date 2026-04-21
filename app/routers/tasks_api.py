"""Department-scoped Kanban tasks (manager/admin create; all department members can view)."""

from datetime import datetime, timezone
from typing import Any, List, Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.database import get_db
from app.deps import get_current_user, require_roles

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

COLUMN_VALUES = ("todo", "in_progress", "done")


def _iso(dt: Any) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


async def _task_out(db, doc: dict) -> dict:
    assignees = doc.get("assignee_employee_ids") or []
    names = []
    for eid in assignees:
        u = await db.users.find_one({"employee_id": eid})
        names.append(u["full_name"] if u else eid)
    creator = None
    if doc.get("created_by"):
        cu = await db.users.find_one({"_id": doc["created_by"]})
        if cu:
            creator = {"employee_id": cu.get("employee_id"), "full_name": cu.get("full_name")}
    return {
        "id": str(doc["_id"]),
        "department_id": str(doc["department_id"]),
        "title": doc["title"],
        "description": doc.get("description") or "",
        "column": doc["column"],
        "priority": int(doc.get("priority") or 3),
        "assignee_employee_ids": list(assignees),
        "assignee_names": names,
        "created_by": creator,
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
    }


async def _valid_assignees_in_department(db, dept_oid: ObjectId, employee_ids: List[str]) -> List[str]:
    if not employee_ids:
        return []
    out: List[str] = []
    for eid in employee_ids:
        eid = (eid or "").strip()
        if not eid:
            continue
        u = await db.users.find_one({"employee_id": eid, "department_id": dept_oid})
        if u:
            out.append(u["employee_id"])
    return out


def _require_dept_for_list(user, department_id: Optional[str]) -> ObjectId:
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
    try:
        return ObjectId(user["department_id"])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user department")


async def _assert_task_department_access(user, task_dept_id: ObjectId) -> None:
    if user.get("role") == "admin":
        return
    if not user.get("department_id"):
        raise HTTPException(status_code=403, detail="No department")
    if str(task_dept_id) != str(user["department_id"]):
        raise HTTPException(status_code=403, detail="Wrong department")


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=4000)
    column: Literal["todo", "in_progress", "done"] = "todo"
    priority: int = Field(3, ge=1, le=5)
    assignee_employee_ids: List[str] = Field(default_factory=list)
    department_id: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=4000)
    column: Optional[Literal["todo", "in_progress", "done"]] = None
    priority: Optional[int] = Field(None, ge=1, le=5)
    assignee_employee_ids: Optional[List[str]] = None


@router.get("/health")
async def tasks_kanban_health():
    """Probe that the My Kanban API is deployed (load balancers / old images often miss new routes)."""
    return {"ok": True, "kanban": True}


@router.get("")
@router.get("/")
async def list_tasks(
    department_id: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    db = get_db()
    dept_oid = _require_dept_for_list(user, department_id)
    cur = db.tasks.find({"department_id": dept_oid}).sort([("column", 1), ("priority", -1), ("updated_at", -1)])
    items = []
    async for doc in cur:
        items.append(await _task_out(db, doc))
    return {"tasks": items}


@router.post("")
@router.post("/")
async def create_task(body: TaskCreate, user=Depends(require_roles("manager", "admin"))):
    db = get_db()
    if user["role"] == "admin":
        if not body.department_id:
            raise HTTPException(status_code=400, detail="department_id is required for administrators")
        try:
            dept_oid = ObjectId(body.department_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid department_id")
        if not await db.departments.find_one({"_id": dept_oid}):
            raise HTTPException(status_code=400, detail="Department not found")
    else:
        if not user.get("department_id"):
            raise HTTPException(status_code=400, detail="Manager has no department")
        dept_oid = ObjectId(user["department_id"])

    if body.column not in COLUMN_VALUES:
        raise HTTPException(status_code=400, detail="Invalid column")

    assignees = await _valid_assignees_in_department(db, dept_oid, body.assignee_employee_ids)
    now = datetime.now(timezone.utc)
    doc = {
        "department_id": dept_oid,
        "title": body.title.strip(),
        "description": (body.description or "").strip(),
        "column": body.column,
        "priority": body.priority,
        "assignee_employee_ids": assignees,
        "created_by": ObjectId(user["_id"]),
        "created_at": now,
        "updated_at": now,
    }
    res = await db.tasks.insert_one(doc)
    created = await db.tasks.find_one({"_id": res.inserted_id})
    return await _task_out(db, created)


@router.patch("/{task_id}")
async def update_task(task_id: str, body: TaskUpdate, user=Depends(require_roles("manager", "admin"))):
    db = get_db()
    try:
        oid = ObjectId(task_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid task id")
    doc = await db.tasks.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Task not found")
    await _assert_task_department_access(user, doc["department_id"])

    raw = body.model_dump(exclude_unset=True)
    if not raw:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates: dict = {"updated_at": datetime.now(timezone.utc)}
    if "title" in raw:
        updates["title"] = str(raw["title"]).strip()
    if "description" in raw:
        updates["description"] = str(raw["description"] or "").strip()
    if "column" in raw:
        col = raw["column"]
        if col not in COLUMN_VALUES:
            raise HTTPException(status_code=400, detail="Invalid column")
        updates["column"] = col
    if "priority" in raw:
        updates["priority"] = int(raw["priority"])
    if "assignee_employee_ids" in raw:
        updates["assignee_employee_ids"] = await _valid_assignees_in_department(
            db, doc["department_id"], raw["assignee_employee_ids"] or []
        )

    await db.tasks.update_one({"_id": oid}, {"$set": updates})
    fresh = await db.tasks.find_one({"_id": oid})
    return await _task_out(db, fresh)


@router.delete("/{task_id}")
async def delete_task(task_id: str, user=Depends(require_roles("manager", "admin"))):
    db = get_db()
    try:
        oid = ObjectId(task_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid task id")
    doc = await db.tasks.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    await _assert_task_department_access(user, doc["department_id"])
    await db.tasks.delete_one({"_id": oid})
    return {"ok": True}
