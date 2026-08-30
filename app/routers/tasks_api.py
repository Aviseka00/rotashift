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


def _task_out(doc: dict, names_by_employee_id: Optional[dict] = None, creators_by_id: Optional[dict] = None) -> dict:
    assignees = doc.get("assignee_employee_ids") or []
    names_map = names_by_employee_id or {}
    names = [names_map.get(eid, eid) for eid in assignees]
    creator = (creators_by_id or {}).get(doc.get("created_by"))
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
    requested = list(dict.fromkeys((eid or "").strip().upper() for eid in employee_ids if (eid or "").strip()))
    if not requested:
        return []
    valid = {
        u["employee_id"]
        async for u in db.users.find(
            {"employee_id": {"$in": requested}, "department_id": dept_oid},
            {"employee_id": 1},
        )
    }
    return [eid for eid in requested if eid in valid]


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
    include_members: bool = Query(False),
    user=Depends(get_current_user),
):
    db = get_db()
    dept_oid = _require_dept_for_list(user, department_id)
    docs = await db.tasks.find({"department_id": dept_oid}).sort(
        [("column", 1), ("priority", -1), ("updated_at", -1)]
    ).to_list(length=None)
    employee_ids = {eid for doc in docs for eid in (doc.get("assignee_employee_ids") or [])}
    creator_ids = {doc["created_by"] for doc in docs if doc.get("created_by")}
    names_by_employee_id = {}
    creators_by_id = {}
    members = []
    if employee_ids or creator_ids or include_members:
        query_parts = []
        if include_members:
            query_parts.append({"department_id": dept_oid})
        elif employee_ids:
            query_parts.append({"employee_id": {"$in": list(employee_ids)}})
        if creator_ids:
            query_parts.append({"_id": {"$in": list(creator_ids)}})
        query = query_parts[0] if len(query_parts) == 1 else {"$or": query_parts}
        async for person in db.users.find(query, {"employee_id": 1, "full_name": 1, "department_id": 1}):
            names_by_employee_id[person.get("employee_id")] = person.get("full_name") or person.get("employee_id")
            creators_by_id[person["_id"]] = {
                "employee_id": person.get("employee_id"), "full_name": person.get("full_name")
            }
            if include_members and person.get("department_id") == dept_oid:
                members.append({"employee_id": person.get("employee_id"), "full_name": person.get("full_name")})
    members.sort(key=lambda item: ((item.get("full_name") or "").lower(), item.get("employee_id") or ""))
    return {"tasks": [_task_out(doc, names_by_employee_id, creators_by_id) for doc in docs], "members": members}


@router.post("")
@router.post("/")
async def create_task(body: TaskCreate, user=Depends(require_roles("admin"))):
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
    doc["_id"] = res.inserted_id
    creator = {ObjectId(user["_id"]): {"employee_id": user.get("employee_id"), "full_name": user.get("full_name")}}
    assignee_names = {}
    if assignees:
        async for person in db.users.find({"employee_id": {"$in": assignees}}, {"employee_id": 1, "full_name": 1}):
            assignee_names[person["employee_id"]] = person.get("full_name") or person["employee_id"]
    return _task_out(doc, assignee_names, creator)


@router.patch("/{task_id}")
async def update_task(task_id: str, body: TaskUpdate, user=Depends(require_roles("admin"))):
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
    fresh = {**doc, **updates}
    assignee_ids = fresh.get("assignee_employee_ids") or []
    assignee_names = {}
    if assignee_ids:
        async for person in db.users.find({"employee_id": {"$in": assignee_ids}}, {"employee_id": 1, "full_name": 1}):
            assignee_names[person["employee_id"]] = person.get("full_name") or person["employee_id"]
    creator = {}
    if fresh.get("created_by"):
        person = await db.users.find_one({"_id": fresh["created_by"]}, {"employee_id": 1, "full_name": 1})
        if person:
            creator[person["_id"]] = {"employee_id": person.get("employee_id"), "full_name": person.get("full_name")}
    return _task_out(fresh, assignee_names, creator)


@router.delete("/{task_id}")
async def delete_task(task_id: str, user=Depends(require_roles("admin"))):
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
