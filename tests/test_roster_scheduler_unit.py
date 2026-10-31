from datetime import date, timedelta

from app.roster_scheduler import (
    BATCH_ROT_CYCLE,
    BATCH_SUP_CYCLE,
    NON_BATCH_CYCLE,
    Person,
    generate_month,
    month_bounds,
    natural_c_gap,
)

# Staff mix from the Shift Formula Reference v2 sheet.
STAFF = [("M", 1), ("G", 4), ("20", 2), ("S", 4), ("I", 6), ("W", 11)]


def _people():
    out = []
    for cat, n in STAFF:
        for i in range(n):
            out.append(Person(key=f"{cat}{i}", employee_id=f"{cat}{i:02d}", name=f"{cat} {i}", category=cat))
    return out


def _by_key(res):
    return {r.person.key: r for r in res["people"]}


def _chain(plan, people):
    """Generate months in sequence, feeding each result into the next month's history."""
    out = []
    for month, mode in plan:
        start, days = month_bounds(month)
        res = generate_month(mode=mode, start=start, days=days, people=people)
        by = _by_key(res)
        for p in people:
            r = by[p.key]
            for d, c in enumerate(r.codes):
                if c:
                    p.history[(start + timedelta(days=d)).isoformat()] = c
            p.prev_state = r.end_state
        out.append((month, mode, start, res))
    return out


def test_month_bounds():
    assert month_bounds("2026-11") == (date(2026, 11, 1), 30)
    assert month_bounds("2027-02") == (date(2027, 2, 1), 28)


def test_cycles_match_the_formula_sheet():
    assert " ".join(BATCH_SUP_CYCLE) == "A A A B B B WO"
    assert " ".join(BATCH_ROT_CYCLE) == "A A A B B B WO C C C WO"
    assert " ".join(NON_BATCH_CYCLE) == "B B B B G G G A A A A C C C WO WO WO"
    assert natural_c_gap(NON_BATCH_CYCLE) == 14
    assert natural_c_gap(BATCH_ROT_CYCLE) == 8


def test_who_rotates_in_each_mode():
    res_b = _by_key(generate_month(mode="BATCH", start=date(2026, 12, 1), days=31, people=_people()))
    res_n = _by_key(generate_month(mode="NON_BATCH", start=date(2026, 12, 1), days=31, people=_people()))
    for key in ("M0", "G0", "200"):
        assert res_b[key].role == "fixed" and res_n[key].role == "fixed"
    assert res_b["S0"].cycle == "batch-supervisor-7" and "C" not in res_b["S0"].codes
    assert res_b["I0"].cycle == "batch-rotator-11" and res_b["W0"].cycle == "batch-rotator-11"
    assert res_n["S0"].role == "fixed"
    assert res_n["I0"].cycle == "nonbatch-rotator-17" and res_n["W0"].cycle == "nonbatch-rotator-17"


def test_fixed_g_staff_rest_on_sunday():
    res = _by_key(generate_month(mode="NON_BATCH", start=date(2026, 12, 1), days=31, people=_people()))
    for d, c in enumerate(res["M0"].codes):
        assert c == ("WO" if (date(2026, 12, 1) + timedelta(days=d)).weekday() == 6 else "G")


def test_fresh_non_batch_hits_the_uniform_target():
    res = generate_month(mode="NON_BATCH", start=date(2026, 12, 1), days=31, people=_people())
    for day in res["daily"]:
        assert (day.get("B"), day.get("A"), day.get("C")) == (4, 4, 3)
    assert not res["warnings"]


def test_fresh_batch_within_targets():
    res = generate_month(mode="BATCH", start=date(2026, 12, 1), days=31, people=_people())
    for day in res["daily"]:
        assert 5 <= day.get("A", 0) <= 7 and 5 <= day.get("B", 0) <= 7 and 3 <= day.get("C", 0) <= 5


def test_rules_hold_across_months_and_mode_switches():
    people = _people()
    plan = [("2026-12", "NON_BATCH"), ("2027-01", "NON_BATCH"), ("2027-02", "BATCH"), ("2027-03", "BATCH"), ("2027-04", "NON_BATCH")]
    results = _chain(plan, people)
    for _month, _mode, _start, res in results:
        assert not [w for w in res["warnings"] if "could not satisfy" in w], res["warnings"]
    for p in people:
        if p.category not in ("I", "W"):  # Supervisors are fixed G in Non-Batch months
            continue
        seq = [p.history[k] for k in sorted(p.history)]
        for a, b in zip(seq, seq[1:]):
            if a == "C":
                assert b in ("C", "WO"), (p.key, seq)  # WO always after C
        run, prev = 0, None
        for c in seq:
            run = run + 1 if c == prev else 1
            prev = c
            assert run <= 4, (p.key, seq)
    # Steady Non-Batch month (continuing) stays close to 4/4/3.
    _, _, _, jan = results[1]
    for day in jan["daily"]:
        assert 3 <= day.get("A", 0) <= 5 and 3 <= day.get("B", 0) <= 5 and 2 <= day.get("C", 0) <= 4


def test_memory_rules_at_month_start():
    start = date(2027, 1, 1)
    people = _people()
    w = next(p for p in people if p.key == "W0")
    # Two nights at the end of the last week -> must start on B / G / WO.
    for back, code in zip(range(7, 0, -1), ["A", "A", "A", "B", "WO", "C", "C"]):
        w.history[(start - timedelta(days=back)).isoformat()] = code
    res = _by_key(generate_month(mode="NON_BATCH", start=start, days=31, people=people))
    assert res["W0"].codes[0] == "WO"


def test_continuing_people_keep_their_rhythm():
    people = _people()
    results = _chain([("2026-12", "NON_BATCH"), ("2027-01", "NON_BATCH")], people)
    dec, jan = results[0][3], results[1][3]
    moved = sum(
        1
        for r in jan["people"]
        if r.role == "rotator" and r.phase != (_by_key(dec)[r.person.key].phase + 31) % 17
    )
    assert moved <= 6  # only people forced by the memory rules move


def test_locked_leave_days_are_kept():
    people = _people()
    people[-1].locked = {5: "L", 6: "L"}
    res = _by_key(generate_month(mode="NON_BATCH", start=date(2026, 12, 1), days=31, people=people))
    r = res[people[-1].key]
    assert r.codes[5] is None and r.codes[6] is None


def test_people_without_category_are_skipped_with_warning():
    people = _people() + [Person(key="n", employee_id="X001", name="No cat", category="")]
    res = generate_month(mode="NON_BATCH", start=date(2026, 12, 1), days=31, people=people)
    assert _by_key(res)["n"].role == "skipped"
    assert any("no roster category" in w for w in res["warnings"])
