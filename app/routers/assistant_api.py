"""Ask Rota. Live roster answers stay local and instant; Groq and Gemini race for everything else."""
from __future__ import annotations

import asyncio
import re
import time
from datetime import date, timedelta
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.assistant_llm import cloud_answer, cloud_providers
from app.comp_off import credit_counts
from app.config import SHIFT_DEFINITIONS
from app.database import get_db
from app.deps import get_current_user

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

_NAME_STOP = frozenset(
    {
        "the", "and", "for", "show", "schedule", "shift", "shifts", "roster", "duty", "duties",
        "what", "whats", "who", "whose", "when", "where", "which", "is", "are", "on", "in",
        "today", "tomorrow", "tonight", "week", "next", "this", "my", "me", "mine", "our", "your",
        "her", "his", "their", "she", "he", "they", "please", "tell", "about", "working", "work",
        "leave", "task", "tasks", "assigned", "coverage", "comp", "off", "how", "does", "do",
        "can", "you", "ask", "rota", "from", "with", "have", "has", "colleague", "employee",
        "staff", "team", "department", "code", "codes", "now", "currently", "timing", "timings",
        "today", "name", "named", "called",
    }
)
_ROSTER_HINTS = (
    "shift", "schedule", "roster", "duty", "working", "timing", "timings", "on leave",
    "week off", "assigned", "duty roster",
)
_HOWTO = (
    "Apply leave, swap, or comp-off from the employee ⋮ menu. Only an administrator can approve those requests. "
    "Comp-off can be used against leave (L) or a rostered A, B, C, or G day; after approval the day shows CO. "
    "Earn a credit by working on WO, leave, or a holiday (including G), or a joint extra A+B / B+C / C+A, then get it approved. "
    "Approved dual days show A+B (or B+C / C+A) on the roster. Tap CO to see which extra-duty day paid it. Tap +CO to see a generated credit."
)
_people_cache: tuple[float, list[dict]] = (0.0, [])
_PEOPLE_TTL_S = 20.0


class AssistantQuery(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
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
    source: Literal["local", "groq", "gemini"] = "local"


def _normalize_speech(text: str) -> str:
    t = (text or "").lower()
    for mark in ("’", "‘", "ʼ", "´", "`"):
        t = t.replace(mark, "'")
    t = t.replace("what's", "what is").replace("who's", "who is").replace("whos ", "who is ")
    t = re.sub(r"\bshow me\b", "show", t)
    t = re.sub(r"\b([a-z]{3,})'s\b", r"\1", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _one_edit(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    if len(left) > len(right):
        left, right = right, left
    skip = 0
    for index, char in enumerate(left):
        if char != right[index + skip]:
            skip += 1
            if skip > 1 or char != right[index + skip]:
                return False
    return True


def _person_name_hits(message: str, full_name: str) -> bool:
    return _name_match_score(message, full_name) > 0


def _name_tokens(full_name: str) -> list[str]:
    name = re.sub(r"\s+", " ", (full_name or "").lower()).strip()
    return [part for part in re.findall(r"[a-z]{3,}", name) if part not in _NAME_STOP]


def _name_match_score(message: str, full_name: str) -> int:
    hay_words = _normalize_speech(message).split()
    words = set(hay_words)
    tokens = _name_tokens(full_name)
    if not tokens:
        return 0
    count = len(tokens)
    if count >= 2:
        for index in range(len(hay_words) - count + 1):
            if hay_words[index : index + count] == tokens:
                return 100
        if tokens[0] in words and any(tok in words for tok in tokens[1:]):
            return 80
    query_tokens = [word for word in hay_words if word not in _NAME_STOP]
    first = tokens[0]
    if first in words and (len(first) >= 4 or count == 1):
        return 40
    for query in query_tokens:
        if len(query) < 4:
            continue
        if first.startswith(query) or (len(first) >= 4 and query.startswith(first)):
            return 30
        if len(query) >= 5 and len(first) >= 5 and _one_edit(first, query):
            return 25
    return 0


def _asks_about_other_person(message: str) -> bool:
    lower = (message or "").lower()
    for mark in ("’", "‘", "ʼ", "´"):
        lower = lower.replace(mark, "'")
    if re.search(r"\b(colleague|coworker|co-worker|teammate)\b", lower):
        return True
    if re.search(r"\b[a-z]{3,}'s\b", lower):
        return True
    if re.search(r"\b(her|his|their|she|he)\s+(shift|roster|schedule|duty)\b", lower):
        return True
    return False


def _asks_about_self(message: str) -> bool:
    if _asks_about_other_person(message):
        return False
    return bool(re.search(r"\b(my|mine)\b", _normalize_speech(message)))


def _looks_like_roster(message: str) -> bool:
    lower = (message or "").lower()
    return any(hint in lower for hint in _ROSTER_HINTS)


def _shift_detail(code: str) -> str:
    raw = (code or "—").upper()
    spec = SHIFT_DEFINITIONS.get(raw) or {}
    if spec.get("start") and spec.get("end"):
        extra = " overnight" if spec.get("overnight") else ""
        return f"{raw} {spec['start']}–{spec['end']}{extra}"
    return spec.get("label") or raw


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
    if any(word in lower for word in ("today", "tonight", "now", "currently")):
        return today, today
    if "next week" in lower or "next 7" in lower or "this week" in lower:
        return today, today + timedelta(days=6)
    if any(word in lower for word in ("schedule", "roster")):
        return today, today + timedelta(days=13)
    return today, today + timedelta(days=6)


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


async def _all_people(db) -> list[dict]:
    global _people_cache
    now = time.monotonic()
    cached_at, rows = _people_cache
    if rows and now - cached_at < _PEOPLE_TTL_S:
        return rows
    rows = await db.users.find(
        {}, {"full_name": 1, "employee_id": 1, "department_id": 1}
    ).to_list(length=5000)
    _people_cache = (now, rows)
    return rows


def _match_named_users(message: str, candidates: list[dict]) -> list[dict]:
    scored = []
    for row in candidates:
        score = _name_match_score(message, row.get("full_name", ""))
        if score:
            scored.append((score, row))
    if not scored:
        return []
    best = max(score for score, _ in scored)
    hits = [row for score, row in scored if score == best]
    hits.sort(key=lambda item: len(item.get("full_name") or ""), reverse=True)
    return hits


async def _target_user(db, message: str, current: dict, people: list[dict]) -> tuple[Optional[dict], Optional[str]]:
    targets, missing = await _resolve_people(db, message, current, people)
    if missing:
        return None, missing
    if len(targets) == 1:
        return targets[0], None
    if len(targets) > 1:
        listed = ", ".join(f"{row.get('full_name')} ({row.get('employee_id')})" for row in targets[:8])
        return None, f"I found more than one teammate: {listed}. Ask again with the employee ID."
    return None, "Tell me the employee ID or name, for example: “What is Smruti’s shift today?”"


async def _resolve_people(db, message: str, current: dict, people: list[dict]) -> tuple[list[dict], Optional[str]]:
    tokens = re.findall(r"\b[A-Za-z]*\d[A-Za-z0-9_-]{2,}\b", message)
    for token in tokens:
        needle = token.upper()
        found = next((row for row in people if str(row.get("employee_id") or "").upper() == needle), None)
        if found:
            return [found], None
        found = await db.users.find_one({"employee_id": needle})
        if found:
            return [found], None
    matches = _match_named_users(message, people)
    if matches:
        return matches, None
    if _asks_about_self(message):
        return [current], None
    if _asks_about_other_person(message) or _looks_like_roster(message):
        hint = ""
        query_tokens = [tok for tok in _normalize_speech(message).split() if len(tok) >= 4 and tok not in _NAME_STOP]
        close = []
        for row in people:
            first = (row.get("full_name") or "").split()[0]
            if first and any(first.lower().startswith(tok) or tok.startswith(first.lower()) for tok in query_tokens):
                close.append(f"{row.get('full_name')} ({row.get('employee_id')})")
        if close:
            hint = f" Nearby people: {', '.join(close[:6])}."
        return [], "I could not find that person in RotaShift. Try their first name, full name, or employee ID." + hint
    if current.get("role") == "employee":
        return [current], None
    return [], "Tell me the employee ID or name, for example: “What is Amruta’s shift today?”"


async def _person_shift_items(db, target: dict, start: date, end: date) -> list[AssistantItem]:
    target_id = ObjectId(target["_id"]) if isinstance(target.get("_id"), str) else target["_id"]
    shifts = await db.shifts.find(
        {"user_id": target_id, "date": {"$gte": start.isoformat(), "$lte": end.isoformat()}},
        {"date": 1, "shift_code": 1},
    ).sort("date", 1).to_list(length=62)
    who = f"{target.get('full_name', '?')} ({target.get('employee_id', '?')})"
    return [
        AssistantItem(
            title=f"{who} · {_format_day(row['date'])}",
            detail=_shift_detail(row.get("shift_code", "—")),
            meta=target.get("employee_id"),
        )
        for row in shifts
    ]


async def _schedule_answer(db, body: AssistantQuery, user: dict, people: list[dict]) -> AssistantAnswer:
    targets, missing = await _resolve_people(db, body.message, user, people)
    if missing:
        return AssistantAnswer(intent="schedule", answer=missing, suggestions=["Show my schedule", "Who is on A shift tomorrow?"])
    if not targets:
        return AssistantAnswer(intent="schedule", answer="I could not find that person in RotaShift.")
    start, end = _date_window(body.message)
    chunks = await asyncio.gather(*[_person_shift_items(db, target, start, end) for target in targets[:8]])
    items = [item for chunk in chunks for item in chunk]
    if len(targets) > 1:
        names = ", ".join(f"{row.get('full_name')} ({row.get('employee_id')})" for row in targets[:8])
        answer = (
            f"I found {len(targets)} teammates matching that name: {names}. "
            f"Here is who is rostered from {_format_day(start.isoformat())} to {_format_day(end.isoformat())}."
        )
        if not items:
            answer = f"I found {len(targets)} teammates matching that name: {names}. None of them have a roster assignment in that date range."
        return AssistantAnswer(intent="schedule", answer=answer, items=items, suggestions=["Ask again with the employee ID"])
    target = targets[0]
    who = f"{target.get('full_name', '?')} ({target.get('employee_id', '?')})"
    first = (target.get("full_name") or "this teammate").split()[0]
    if start == end and items:
        answer = f"{first} is on {items[0].detail} on {_format_day(start.isoformat())}."
    elif items:
        answer = f"{who} has {len(items)} roster assignment{'s' if len(items) != 1 else ''} from {_format_day(start.isoformat())} to {_format_day(end.isoformat())}."
    else:
        answer = f"No roster assignments are recorded for {who} from {_format_day(start.isoformat())} to {_format_day(end.isoformat())}."
    return AssistantAnswer(
        intent="schedule",
        answer=answer,
        items=items,
        suggestions=["Show my schedule", "Who is on G shift tomorrow?"],
    )


async def _coverage_answer(db, body: AssistantQuery, dept_id: Optional[ObjectId]) -> AssistantAnswer:
    start, _ = _date_window(body.message)
    match = re.search(r"\b(?:shift\s*)?(A|B|C|G|L|WO|CO)\b", body.message, re.IGNORECASE)
    code = match.group(1).upper() if match else None
    query: dict = {"date": start.isoformat()}
    if dept_id:
        query["department_id"] = dept_id
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
        items.append(
            AssistantItem(
                title=person.get("full_name", "Unknown employee"),
                detail=_shift_detail(shift.get("shift_code", "—")),
                meta=person.get("employee_id"),
            )
        )
    label = f"shift {code}" if code else "any recorded shift"
    answer = f"{len(items)} employee{'s are' if len(items) != 1 else ' is'} assigned to {label} on {_format_day(start.isoformat())}."
    return AssistantAnswer(intent="coverage", answer=answer, items=items, suggestions=["Show my schedule next week", "Show my tasks"])


async def _tasks_answer(db, body: AssistantQuery, user: dict, dept_id: Optional[ObjectId], people: list[dict]) -> AssistantAnswer:
    target, missing = await _target_user(db, body.message, user, people)
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


async def _comp_off_answer(db, user: dict) -> AssistantAnswer:
    counts = await credit_counts(db, ObjectId(user["_id"]))
    earned = counts.get("earned") or 0
    ready = counts.get("available") or 0
    used = counts.get("used") or 0
    pending = counts.get("pending_earn") or 0
    bits = [
        f"{earned} earned",
        f"{ready} ready to use against leave",
        f"{used} already shown as CO on the roster",
    ]
    if pending:
        bits.append(f"{pending} earn request(s) waiting approval")
    return AssistantAnswer(
        intent="help",
        answer=f"Your comp-off bank: {'; '.join(bits)}. Apply CO only against approved leave (roster L).",
        suggestions=["Show my schedule", "Show my tasks", "Who is on G shift tomorrow?"],
        source="local",
    )


def _howto_answer() -> AssistantAnswer:
    return AssistantAnswer(
        intent="help",
        answer=_HOWTO,
        suggestions=["Show my schedule", "Show my tasks", "What is Amruta’s shift today?"],
        source="local",
    )


@router.post("/query", response_model=AssistantAnswer)
async def ask_assistant(body: AssistantQuery, user=Depends(get_current_user)):
    db = get_db()
    message = body.message.strip()
    lower = message.lower()
    dept_id = await _scope_department(user, body.department_id)
    people = await _all_people(db)
    named = _match_named_users(message, people)
    if any(phrase in lower for phrase in ("how do i apply", "how to apply", "apply leave", "apply swap", "apply comp")):
        return _howto_answer()
    if any(phrase in lower for phrase in ("my task", "show my task", "kanban", "assigned to me")) and not named:
        return await _tasks_answer(db, body, user, dept_id, people)
    if named and any(phrase in lower for phrase in ("task", "kanban", "assigned")):
        return await _tasks_answer(db, body, user, dept_id, people)
    if any(phrase in lower for phrase in ("who is on", "who's on", "who is working", "coverage")) and not named:
        return await _coverage_answer(db, body, dept_id)
    if any(phrase in lower for phrase in ("comp-off", "compoff", "compensatory", "comp off")) and not named:
        return await _comp_off_answer(db, user)
    wants_own = _asks_about_self(message) and _looks_like_roster(message) and not named
    named_id = bool(re.search(r"\b[A-Za-z]*\d[A-Za-z0-9_-]{2,}\b", message)) and any(
        word in lower for word in ("schedule", "roster", "shift")
    )
    if named or wants_own or named_id or _asks_about_other_person(message):
        return await _schedule_answer(db, body, user, people)
    if _looks_like_roster(message) and not any(phrase in lower for phrase in ("night shift", "what is a", "what is an")):
        return await _schedule_answer(db, body, user, people)
    if cloud_providers():
        cloud = await cloud_answer(message)
        if cloud:
            return AssistantAnswer(**cloud)
    return AssistantAnswer(
        intent="help",
        answer="Ask anything — a teammate’s shift by name, your roster, or a general question.",
        suggestions=["What is Amruta’s shift today?", "Show my schedule", "Who is on G shift tomorrow?"],
        source="local",
    )
