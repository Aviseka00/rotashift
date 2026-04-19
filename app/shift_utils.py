from datetime import date, datetime, timedelta
from typing import Any, Dict

from app.config import SHIFT_DEFINITIONS


def parse_iso_date(d: str) -> date:
    return datetime.strptime(d[:10], "%Y-%m-%d").date()


def shift_event_window(day: date, code: str) -> tuple[datetime, datetime]:
    info = SHIFT_DEFINITIONS.get(code)
    if not info:
        raise ValueError(f"Unknown shift {code}")
    sh, sm = map(int, info["start"].split(":"))
    eh, em = map(int, info["end"].split(":"))
    start = datetime(day.year, day.month, day.day, sh, sm)
    if info.get("overnight"):
        # e.g. C: 20:00 day D → 06:30 day D+1
        end = datetime(day.year, day.month, day.day, eh, em) + timedelta(days=1)
    else:
        end = datetime(day.year, day.month, day.day, eh, em)
    return start, end


def calendar_event_for_shift(
    *,
    assignment_id: str,
    user_name: str,
    employee_id: str,
    shift_code: str,
    day: date,
    kind: str = "shift",
) -> Dict[str, Any]:
    start, end = shift_event_window(day, shift_code)
    info = SHIFT_DEFINITIONS[shift_code]
    label = info["label"]
    title = f"{label} · {user_name} ({employee_id})"
    return {
        "id": assignment_id,
        "title": title,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "extendedProps": {
            "shift_code": shift_code,
            "employee_id": employee_id,
            "kind": kind,
            "user_name": user_name,
        },
        "display": "block",
        "classNames": [f"shift-{shift_code.lower()}", f"evt-{kind}"],
    }


def calendar_event_leave(
    *,
    request_id: str,
    user_name: str,
    employee_id: str,
    start_day: date,
    end_day: date,
) -> Dict[str, Any]:
    # inclusive end date for all-day
    end_plus = end_day + timedelta(days=1)
    return {
        "id": f"leave-{request_id}",
        "title": f"Leave · {user_name} ({employee_id})",
        "start": start_day.isoformat(),
        "end": end_plus.isoformat(),
        "allDay": True,
        "display": "background",
        "extendedProps": {"kind": "leave", "employee_id": employee_id, "user_name": user_name},
        "classNames": ["evt-leave"],
    }
