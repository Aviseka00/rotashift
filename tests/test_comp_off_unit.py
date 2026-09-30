from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from app.comp_off import dual_roster_codes, inclusive_days, pair_label_for_codes, validate_earn


def test_validate_earn_accepts_rest_joint_and_g():
    assert validate_earn("worked_wo", "A") == ("worked_wo", "A")
    assert validate_earn("worked_holiday", "G") == ("worked_holiday", "G")
    assert validate_earn("joint_ab", "B") == ("joint_ab", "B")
    assert validate_earn("joint_ca", "C") == ("joint_ca", "C")


def test_validate_earn_rejects_bad_pairs():
    with pytest.raises(HTTPException) as pair_err:
        validate_earn("joint_ab", "C")
    assert pair_err.value.status_code == 400
    with pytest.raises(HTTPException) as g_joint:
        validate_earn("joint_ab", "G")
    assert g_joint.value.status_code == 400
    with pytest.raises(HTTPException):
        validate_earn("unknown", "A")


def test_dual_roster_codes_keep_primary_and_pair_label():
    assert dual_roster_codes("joint_ab", "A", "B") == ("A", "B", "A+B")
    assert dual_roster_codes("joint_ab", "B", "A") == ("B", "A", "A+B")
    assert dual_roster_codes("joint_bc", "B", "C") == ("B", "C", "B+C")
    assert dual_roster_codes("joint_ca", "C", "A") == ("C", "A", "C+A")
    assert dual_roster_codes("joint_ab", "WO", "B") == ("A", "B", "A+B")
    assert pair_label_for_codes("C", "A") == "C+A"


def test_inclusive_days_counts_multi_day_leave():
    days = inclusive_days(date(2026, 9, 1), date(2026, 9, 4))
    assert days == ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
    with pytest.raises(HTTPException):
        inclusive_days(date(2026, 9, 4), date(2026, 9, 1))
