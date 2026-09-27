from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from app.comp_off import inclusive_days, validate_earn


def test_validate_earn_accepts_rest_and_joint_pairs():
    assert validate_earn("worked_wo", "A") == ("worked_wo", "A")
    assert validate_earn("joint_ab", "B") == ("joint_ab", "B")
    assert validate_earn("joint_ca", "C") == ("joint_ca", "C")


def test_validate_earn_rejects_g_and_bad_pairs():
    with pytest.raises(HTTPException) as g_err:
        validate_earn("worked_holiday", "G")
    assert g_err.value.status_code == 400
    with pytest.raises(HTTPException) as pair_err:
        validate_earn("joint_ab", "C")
    assert pair_err.value.status_code == 400
    with pytest.raises(HTTPException):
        validate_earn("unknown", "A")


def test_inclusive_days_counts_multi_day_leave():
    days = inclusive_days(date(2026, 9, 1), date(2026, 9, 4))
    assert days == ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
    with pytest.raises(HTTPException):
        inclusive_days(date(2026, 9, 4), date(2026, 9, 1))
