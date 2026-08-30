from datetime import date, timedelta

from app.routers.assistant_api import _date_window


def test_assistant_explicit_date_range():
    assert _date_window("show schedule 2026-09-10 to 2026-09-01") == (
        date(2026, 9, 1),
        date(2026, 9, 10),
    )


def test_assistant_tomorrow_is_one_day():
    start, end = _date_window("who is on G shift tomorrow?")
    assert start == date.today() + timedelta(days=1)
    assert end == start
