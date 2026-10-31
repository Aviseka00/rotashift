"""National / festival holidays shown as notes on the roster table and calendar."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Dict, List

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import get_db
from app.deps import get_current_user, require_roles

router = APIRouter(prefix="/api/holidays", tags=["holidays"])

# Fixed-date national holidays (repeat every year). Festival dates that move each
# year (Holi, Diwali, Eid…) are added by the admin for the specific year.
DEFAULT_HOLIDAYS = [
    ("01-26", "Republic Day"),
    ("05-01", "Labour Day"),
    ("08-15", "Independence Day"),
    ("10-02", "Gandhi Jayanti"),
]


class HolidayCreate(BaseModel):
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    name: str = Field(..., min_length=1, max_length=80)
    recurring: bool = False


async def seed_default_holidays(db) -> None:
    """Insert the national holidays once. Deleting one later is respected (not re-added)."""
    if await db.app_meta.find_one({"_id": "holidays_seeded"}):
        return
    now = datetime.now(timezone.utc)
    for md, name in DEFAULT_HOLIDAYS:
        await db.holidays.update_one(
            {"recurring": True, "month_day": md},
            {"$setOnInsert": {"recurring": True, "month_day": md, "name": name, "created_at": now}},
            upsert=True,
        )
    await db.app_meta.update_one({"_id": "holidays_seeded"}, {"$set": {"at": now}}, upsert=True)


def _parse(day: str) -> date:
    try:
        return datetime.strptime(day[:10], "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date")


async def holidays_between(db, start: date, end: date) -> Dict[str, List[str]]:
    """{iso_date: [names]} for every holiday in the inclusive range."""
    out: Dict[str, List[str]] = {}
    async for h in db.holidays.find({}):
        if h.get("recurring"):
            md = h.get("month_day") or ""
            for year in range(start.year, end.year + 1):
                try:
                    d = date.fromisoformat(f"{year}-{md}")
                except ValueError:
                    continue
                if start <= d <= end:
                    out.setdefault(d.isoformat(), []).append(h["name"])
        else:
            iso = str(h.get("date") or "")[:10]
            if iso and start.isoformat() <= iso <= end.isoformat():
                out.setdefault(iso, []).append(h["name"])
    return out


def _out(h: dict) -> dict:
    return {
        "id": str(h["_id"]),
        "name": h["name"],
        "recurring": bool(h.get("recurring")),
        "date": h.get("date"),
        "month_day": h.get("month_day"),
    }


@router.get("")
async def list_holidays(
    start: str | None = Query(None),
    end: str | None = Query(None),
    user=Depends(get_current_user),
):
    db = get_db()
    items = [_out(h) async for h in db.holidays.find({}).sort([("month_day", 1), ("date", 1)])]
    by_date = {}
    if start and end:
        by_date = await holidays_between(db, _parse(start), _parse(end))
    return {"holidays": items, "by_date": by_date}


@router.post("")
async def add_holiday(body: HolidayCreate, user=Depends(require_roles("admin"))):
    db = get_db()
    d = _parse(body.date)
    doc = {"name": body.name.strip(), "created_at": datetime.now(timezone.utc)}
    if body.recurring:
        doc.update({"recurring": True, "month_day": d.strftime("%m-%d")})
    else:
        doc.update({"recurring": False, "date": d.isoformat()})
    res = await db.holidays.insert_one(doc)
    return {"id": str(res.inserted_id), **{k: v for k, v in doc.items() if k not in ("created_at", "_id")}}


@router.delete("/{hid}")
async def delete_holiday(hid: str, user=Depends(require_roles("admin"))):
    db = get_db()
    try:
        oid = ObjectId(hid)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    r = await db.holidays.delete_one({"_id": oid})
    if not r.deleted_count:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}
