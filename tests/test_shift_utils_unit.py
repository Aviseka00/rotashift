from __future__ import annotations

from datetime import date

import pytest

from app.shift_utils import parse_iso_date


@pytest.mark.parametrize(
    "s,expected",
    [
        ("2026-05-01", date(2026, 5, 1)),
        ("2026-12-31T00:00:00Z", date(2026, 12, 31)),
    ],
)
def test_parse_iso_date(s: str, expected: date):
    assert parse_iso_date(s) == expected
