"""Local, role-scoped RotaShift assistant. No prompts or roster data leave the server."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.database import get_db
from app.deps import get_current_user

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class AssistantQuery(BaseModel):
    message: str = Field(..., min_length=1, max_length=600)
    department_id: Optional[str] = None


class AssistantItem(BaseModel):
    title: str
    detail: str
    meta: Optional[str] = None


class AssistantAnswer(BaseModel):
    intent: Literal["schedule", "coverage", "tasks", "help"]
    answer: str
    items: list[AssistantItem] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


def _date_window(message: str) -> tuple[date, date]:
    today = date.today()
    lower = message.lower()
    iso_dates = [date.fromisoformat(value) for value in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", message)]
    if len(iso_dates) >= 2:
        return min(iso_dates[0], iso_dates[1]), max(iso_dates[0], iso_dates[1])
    if len(iso_dates) == 1:
        return iso_dates[0], iso_dates[0]
    if "tomorrow" in lower:
        day = today + timedelta(days=1)
        return day, day
    if "today" in lower:
        return today, today
    if "next week" in lower or "next 7" in lower:
        return today, today + timedelta(days=6)
    return today, today + timedelta(days=13)


def _format_day(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%a, %d %b %Y")
    except ValueError:
        return value


async def _scope_department(user: dict, requested: Optional[str]) -> Optional[ObjectId]:
    if user.get("role") != "admin":
        return ObjectId(user["department_id"]) if user.get("department_id") else None
    if requested:
        try:
            return ObjectId(requested)
        except Exception:
            return None
    return None


async def _target_user(db, message: str, current: dict, dept_id: Optional[ObjectId]) -> tuple[Optional[dict], Optional[str]]:
    lower = message.lower()
    if re.search(r"\b(my|me|mine)\b", lower):
        return current, None
    tokens = re.findall(r"\b[A-Za-z]*\d[A-Za-z0-9_-]{2,}\b", message)
    for token in tokens:
        query = {"employee_id": token.upper()}
        if current.get("role") != "admin" and dept_id:
            query["department_id"] = dept_id
        found = await db.users.find_one(query)
        if found:
            return found, None
    name_query = {"department_id": dept_id} if dept_id else {}
    candidates = await db.users.find(name_query, {"full_name": 1, "employee_id": 1, "department_id": 1}).to_list(length=500)
    normalized = re.sub(r"\s+", " ", lower)
    for candidate in sorted(candidates, key=lambda item: len(item.get("full_name", "")), reverse=True):
        name = re.sub(r"\s+", " ", candidate.get("full_name", "").lower()).strip()
        if len(name) >= 3 and name in normalized:
            return candidate, None
    if current.get("role") == "employee":
        return current, None
    return None, "Tell me the employee ID or full name, for example: “Show schedule for 80100173 next week.”"


async def _schedule_answer(db, body: AssistantQuery, user: dict, dept_id: Optional[ObjectId]) -> AssistantAnswer:
    target, missing = await _target_user(db, body.message, user, dept_id)
    if missing:
        return AssistantAnswer(intent="schedule", answer=missing, suggestions=["Show my schedule", "Who is on A shift tomorrow?"])
    if not target:
        return AssistantAnswer(intent="schedule", answer="I could not find that employee in your accessible team.")
    if user.get("role") == "manager" and str(target.get("department_id")) != str(dept_id):
        return AssistantAnswer(intent="schedule", answer="That employee is outside your department, so I cannot show their roster.")
    start, end = _date_window(body.message)
    target_id = ObjectId(target["_id"]) if isinstance(target.get("_id"), str) else target["_id"]
    shifts = await db.shifts.find(
        {"user_id": target_id, "date": {"$gte": start.isoformat(), "$lte": end.isoformat()}},
        {"date": 1, "shift_code": 1},
    ).sort("date", 1).to_list(length=62)
    items = [AssistantItem(title=_format_day(s["date"]), detail=f"Shift {s.get('shift_code', '—')}") for s in shifts]
    who = f"{target.get('full_name', '?')} ({target.get('employee_id', '?')})"
    answer = f"I found {len(items)} roster assignment{'s' if len(items) != 1 else ''} for {who} from {_format_day(start.isoformat())} to {_format_day(end.isoformat())}."
    if not items:
        answer = f"No roster assignments are recorded for {who} in that date range."
    return AssistantAnswer(intent="schedule", answer=answer, items=items, suggestions=["Show my tasks", "Who is on G shift tomorrow?"])


async def _coverage_answer(db, body: AssistantQuery, dept_id: Optional[ObjectId]) -> AssistantAnswer:
    if not dept_id:
        return AssistantAnswer(intent="coverage", answer="Choose a department first, then ask who is working a shift.")
    start, _ = _date_window(body.message)
    match = re.search(r"\b(?:shift\s*)?(A|B|C|G|L|WO)\b", body.message, re.IGNORECASE)
    code = match.group(1).upper() if match else None
    query = {"department_id": dept_id, "date": start.isoformat()}
    if code:
        query["shift_code"] = code
    shifts = await db.shifts.find(query, {"user_id": 1, "shift_code": 1}).to_list(length=500)
    user_ids = [s["user_id"] for s in shifts if s.get("user_id")]
    people = {}
    if user_ids:
        people = {u["_id"]: u for u in await db.users.find({"_id": {"$in": user_ids}}, {"full_name": 1, "employee_id": 1}).to_list(length=500)}
    items = []
    for shift in shifts:
        person = people.get(shift.get("user_id"), {})
        items.append(AssistantItem(title=person.get("full_name", "Unknown employee"), detail=f"Shift {shift.get('shift_code', '—')}", meta=person.get("employee_id")))
    label = f"shift {code}" if code else "any recorded shift"
    answer = f"{len(items)} employee{'s are' if len(items) != 1 else ' is'} assigned to {label} on {_format_day(start.isoformat())}."
    return AssistantAnswer(intent="coverage", answer=answer, items=items, suggestions=["Show my schedule next week", "Show my tasks"])


async def _tasks_answer(db, body: AssistantQuery, user: dict, dept_id: Optional[ObjectId]) -> AssistantAnswer:
    target, missing = await _target_user(db, body.message, user, dept_id)
    team_query = bool(re.search(r"\b(team|department|all)\b", body.message, re.IGNORECASE))
    if missing and not team_query:
        return AssistantAnswer(intent="tasks", answer=missing, suggestions=["Show my tasks", "Show all department tasks"])
    query = {"department_id": dept_id} if dept_id else {}
    who = "the department"
    if target and not team_query:
        query["assignee_employee_ids"] = target.get("employee_id")
        who = f"{target.get('full_name', '?')} ({target.get('employee_id', '?')})"
    elif user.get("role") == "employee":
        query["assignee_employee_ids"] = user.get("employee_id")
        who = "you"
    tasks = await db.tasks.find(query, {"title": 1, "column": 1, "priority": 1}).sort([("column", 1), ("priority", -1)]).to_list(length=100)
    items = [AssistantItem(title=t.get("title", "Untitled task"), detail=t.get("column", "todo").replace("_", " ").title(), meta=f"Priority {t.get('priority', 3)}") for t in tasks]
    active = sum(1 for t in tasks if t.get("column") != "done")
    answer = f"I found {len(tasks)} task{'s' if len(tasks) != 1 else ''} assigned to {who}; {active} still active."
    if not tasks:
        answer = f"No tasks are currently assigned to {who}."
    return AssistantAnswer(intent="tasks", answer=answer, items=items, suggestions=["Show my schedule", "Who is on shift A today?"])


@router.post("/query", response_model=AssistantAnswer)
async def ask_assistant(body: AssistantQuery, user=Depends(get_current_user)):
    db = get_db()
    message = body.message.strip()
    lower = message.lower()
    dept_id = await _scope_department(user, body.department_id)
    if any(word in lower for word in ("task", "assigned", "assignment", "work item", "kanban")):
        return await _tasks_answer(db, body, user, dept_id)
    if any(word in lower for word in ("who is on", "who's on", "coverage", "working shift", "on shift")):
        return await _coverage_answer(db, body, dept_id)
    if any(word in lower for word in ("schedule", "shift", "roster", "duty")):
        return await _schedule_answer(db, body, user, dept_id)
    return AssistantAnswer(intent="help", answer="I can answer questions about schedules, shift coverage, and assigned tasks using live RotaShift data.", suggestions=["Show my schedule", "Show my tasks", "Who is on G shift tomorrow?"])
