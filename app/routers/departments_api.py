from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.database import get_db
from app.deps import get_current_user, require_roles

router = APIRouter(prefix="/api/departments", tags=["departments"])


class DepartmentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


@router.get("")
async def list_departments():
    db = get_db()
    items = []
    async for d in db.departments.find().sort("name", 1):
        items.append({"id": str(d["_id"]), "name": d["name"], "created_at": d.get("created_at")})
    return {"departments": items}


@router.post("")
async def create_department(body: DepartmentCreate, user=Depends(require_roles("admin"))):
    db = get_db()
    name = body.name.strip().lower()
    if await db.departments.find_one({"name": name}):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Department already exists")
    doc = {"name": name, "created_at": datetime.now(timezone.utc)}
    res = await db.departments.insert_one(doc)
    return {"id": str(res.inserted_id), "name": name}


@router.delete("/{dept_id}")
async def delete_department(dept_id: str, user=Depends(require_roles("admin"))):
    db = get_db()
    try:
        oid = ObjectId(dept_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    r = await db.departments.delete_one({"_id": oid})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    await db.users.update_many({"department_id": oid}, {"$set": {"department_id": None}})
    return {"ok": True}
