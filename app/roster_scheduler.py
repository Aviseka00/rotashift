"""Monthly auto-roster generator (pure logic, no database).

The admin picks a mode for each month:

BATCH ("Action mode", production days)
    Fixed G (WO every Sunday): Manager, Girls, Facility-20
    Supervisors (S)           7-day cycle   A A A B B B WO          (never C)
    Incharges (I) + Workers   11-day cycle  A A A B B B WO C C C WO
    Daily target: A 5-7, B 5-7, C 3-5

NON_BATCH ("Normal mode", light days)
    Fixed G (WO every Sunday): Manager, Supervisors, Girls, Facility-20
    Incharges (I) + Workers   17-day cycle  B B B B G G G A A A A C C C WO WO WO
    Daily target: B 4, A 4, C 3 (with 17 rotators: one person per cycle day)

Common rules
    C1  WO always comes right after a C night
    C2  Batch: at most 3 days in a row of any shift; Non-Batch: at most 4 A or B,
        3 G, 3 C, 3 WO in a row
    M1  the first 7 new days must not repeat the last 7 days
    M2  no rotator starts the month on the shift they ended last month on, unless
        they simply continue their own cycle (e.g. WO WO | WO, or B B | B B)
    M3  2+ C nights in the last week -> start in B, G or WO
    GAP the first C of the month keeps the cycle's normal distance from the last C
Fixed-G staff are exempt.

Rotators keep their place in the cycle from month to month. Starting points are
then assigned so that the people in each cycle are spread as evenly as possible
over the cycle days (an optimal assignment), which keeps the daily head-count flat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

MODES = ("BATCH", "NON_BATCH")
MODE_LABELS = {"BATCH": "Batch (Action mode)", "NON_BATCH": "Non-Batch (Normal mode)"}
CATEGORIES = {
    "M": "Manager",
    "S": "Supervisor",
    "I": "Incharge",
    "G": "Girl",
    "20": "Facility-20",
    "W": "Worker",
}

BATCH_SUP_CYCLE = ["A", "A", "A", "B", "B", "B", "WO"]
BATCH_ROT_CYCLE = ["A", "A", "A", "B", "B", "B", "WO", "C", "C", "C", "WO"]
NON_BATCH_CYCLE = ["B", "B", "B", "B", "G", "G", "G", "A", "A", "A", "A", "C", "C", "C", "WO", "WO", "WO"]

# mode -> category -> (cycle name, cycle)
CYCLES: Dict[str, Dict[str, tuple[str, List[str]]]] = {
    "BATCH": {
        "S": ("batch-supervisor-7", BATCH_SUP_CYCLE),
        "I": ("batch-rotator-11", BATCH_ROT_CYCLE),
        "W": ("batch-rotator-11", BATCH_ROT_CYCLE),
    },
    "NON_BATCH": {
        "I": ("nonbatch-rotator-17", NON_BATCH_CYCLE),
        "W": ("nonbatch-rotator-17", NON_BATCH_CYCLE),
    },
}
CYCLE_TEXT = {
    "batch-supervisor-7": "A A A B B B WO",
    "batch-rotator-11": "A A A B B B WO C C C WO",
    "nonbatch-rotator-17": "B B B B G G G A A A A C C C WO WO WO",
}
DAILY_TARGETS: Dict[str, Dict[str, tuple[int, int]]] = {
    "BATCH": {"A": (5, 7), "B": (5, 7), "C": (3, 5)},
    "NON_BATCH": {"B": (4, 4), "A": (4, 4), "C": (3, 3)},
}
# Longest allowed run of one code, per mode (from the formula sheet).
MAX_RUN = {
    "BATCH": {"default": 3},
    "NON_BATCH": {"default": 4, "C": 3, "WO": 3, "G": 3},
}
# Leader presence: minimum Incharges (and Supervisors in Batch) on each shift per day.
LEADER_MIN = {
    "BATCH": {"I": {"A": 1, "B": 1, "C": 1}, "S": {"A": 1, "B": 1}},
    "NON_BATCH": {"I": {"A": 1, "B": 1, "C": 1}},
}
LEADER_WEIGHT = 40
VIOLATION_COST = 1000
OVERLOAD_COST = 40
# GAP is a strong preference, not absolute: it gives way only to keep the daily counts usable.
SOFT_COST = {"GAP": 60}
# Weight per day and shift for (count - target)^2 when balancing.
COVERAGE_WEIGHT = 15


@dataclass
class Person:
    key: str  # stable id (user id string)
    employee_id: str
    name: str
    category: str
    fixed_g_non_batch: bool = False
    # Rostered codes before the month: {iso_date: code}; ~6 weeks is enough.
    history: Dict[str, str] = field(default_factory=dict)
    # Previous month's saved state: {"mode", "cycle", "pos"}; pos = cycle index for the 1st of this month.
    prev_state: Optional[dict] = None
    # Days in the month that must not be overwritten: {day_index: existing_code}
    locked: Dict[int, str] = field(default_factory=dict)


@dataclass
class PersonResult:
    person: Person
    role: str  # "fixed" | "rotator" | "skipped"
    codes: List[Optional[str]]  # None = keep existing locked cell
    cycle: Optional[str] = None
    phase: Optional[int] = None
    end_state: Optional[dict] = None
    notes: List[str] = field(default_factory=list)


def classify(category: str, mode: str, fixed_g_non_batch: bool = False) -> str:
    cat = (category or "").strip().upper()
    if cat not in CATEGORIES:
        return "skipped"
    if mode == "NON_BATCH" and fixed_g_non_batch:
        return "fixed"
    return "rotator" if cat in CYCLES[mode] else "fixed"


def month_bounds(month: str) -> tuple[date, int]:
    """'2026-11' -> (date(2026,11,1), 30)."""
    y, m = (int(x) for x in month.split("-")[:2])
    start = date(y, m, 1)
    nxt = date(y + (m // 12), (m % 12) + 1, 1)
    return start, (nxt - start).days


def previous_month(month: str) -> str:
    start, _ = month_bounds(month)
    prev_last = start - timedelta(days=1)
    return f"{prev_last.year:04d}-{prev_last.month:02d}"


def _last_days(history: Dict[str, str], start: date, n: int) -> List[str]:
    out = []
    for back in range(n, 0, -1):
        code = history.get((start - timedelta(days=back)).isoformat())
        if code:
            out.append(code.upper())
    return out


def _last_c_date(history: Dict[str, str], start: date) -> Optional[date]:
    best = None
    for iso, code in history.items():
        if (code or "").upper() != "C":
            continue
        try:
            d = date.fromisoformat(iso[:10])
        except ValueError:
            continue
        if d < start and (best is None or d > best):
            best = d
    return best


def natural_c_gap(cycle: Sequence[str]) -> Optional[int]:
    """Clear days between the end of one C run and the start of the next, going round the cycle."""
    if "C" not in cycle:
        return None
    L = len(cycle)
    first = cycle.index("C")
    last = first
    while cycle[(last + 1) % L] == "C" and last + 1 < first + L:
        last += 1
    run = last - first + 1
    return L - run


def _walk(cycle: Sequence[str], phase: int, days: int) -> List[str]:
    L = len(cycle)
    return [cycle[(phase + d) % L] for d in range(days)]


def rule_violations(
    last7: List[str],
    new: List[str],
    *,
    start: date,
    last_c: Optional[date],
    min_gap: Optional[int],
    natural: bool = False,
    max_run: Optional[Dict[str, int]] = None,
) -> List[str]:
    """natural=True means the person simply continues their own cycle from last month."""
    problems = []
    if not new:
        return problems
    if len(last7) == 7 and new[:7] == last7:
        problems.append("M1")
    if last7 and new[0] == last7[-1] and not natural:
        problems.append("M2")
    if last7.count("C") >= 2 and new[0] not in ("B", "G", "WO"):
        problems.append("M3")
    combined = last7 + new
    for i in range(len(combined) - 1):
        if combined[i] == "C" and combined[i + 1] not in ("C", "WO"):
            problems.append("C1")
            break
    run, prev = 0, None
    for i, code in enumerate(combined):
        run = run + 1 if code == prev else 1
        prev = code
        limit = (max_run or {}).get(code, (max_run or {}).get("default", 4))
        if i >= len(last7) and run > limit:
            problems.append("C2")
            break
    if min_gap is not None and last_c is not None and "C" in new:
        first_c = start + timedelta(days=new.index("C"))
        if 1 < (first_c - last_c).days and (first_c - last_c).days - 1 < min_gap:
            problems.append("GAP")
    return problems


def _hungarian(cost: List[List[float]]) -> List[int]:
    """Minimum-cost assignment for a square matrix. Returns col index for each row."""
    n = len(cost)
    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    out = [0] * n
    for j in range(1, n + 1):
        if p[j]:
            out[p[j] - 1] = j - 1
    return out


def _fresh_order(people: List[Person]) -> List[Person]:
    """Alternate Workers and leaders (Incharges) as in the formula sheet's phase table."""
    leaders = [p for p in people if p.category.strip().upper() == "I"]
    others = [p for p in people if p.category.strip().upper() != "I"]
    out: List[Person] = []
    while leaders or others:
        if others:
            out.append(others.pop(0))
        if leaders:
            out.append(leaders.pop(0))
    return out


def _assign_group(
    people: List[Person], cycle_name: str, cycle: List[str], mode: str, start: date, days: int
) -> Dict[str, tuple[int, List[str]]]:
    """Choose a cycle phase per person: obey the rules, keep each person's rhythm where
    possible, and spread the group evenly over the cycle."""
    L = len(cycle)
    n = len(people)
    gap = natural_c_gap(cycle)
    walks = [_walk(cycle, ph, days) for ph in range(L)]
    fresh_slot = {p.key: (i * L) // max(1, n) for i, p in enumerate(_fresh_order(people))}
    # per person, per phase: (violations, cost)
    table = []
    for p in people:
        last7 = _last_days(p.history, start, 7)
        last_c = _last_c_date(p.history, start)
        st = p.prev_state or {}
        cont = int(st["pos"]) % L if st.get("mode") == mode and st.get("cycle") == cycle_name and "pos" in st else None
        row = []
        for ph in range(L):
            viol = rule_violations(
                last7, walks[ph], start=start, last_c=last_c, min_gap=gap, natural=ph == cont, max_run=MAX_RUN[mode]
            )
            c = sum(SOFT_COST.get(v, VIOLATION_COST) for v in viol)
            if cont is not None and ph != cont:
                c += 10 + min((ph - cont) % L, (cont - ph) % L)
            elif cont is None and ph != fresh_slot[p.key]:
                c += 1  # tie-break toward the formula sheet's alternating phase table
            row.append((viol, c))
        table.append(row)

    # Columns are cycle days with capacity. The first copies of each day (the even
    # spread) are free; extra people on the same day cost OVERLOAD_COST each, which
    # is far below VIOLATION_COST, so the rules always win over a perfectly flat count.
    best_total, best_pick = None, None
    base_slots = [(i * L) // n for i in range(n)] if n else []
    extra_per_day = 2
    for r in range(L):
        ideal = [0] * L
        for s0 in base_slots:
            ideal[(s0 + r) % L] += 1
        cols: List[tuple[int, int]] = []  # (phase, penalty)
        for ph in range(L):
            cols += [(ph, 0)] * ideal[ph]
            cols += [(ph, OVERLOAD_COST * (k + 1)) for k in range(extra_per_day)]
        m = len(cols)
        matrix = [[table[i][ph][1] + pen for ph, pen in cols] for i in range(n)]
        matrix += [[0] * m for _ in range(m - n)]  # dummy rows absorb unused columns
        pick = _hungarian(matrix) if n else []
        total = sum(matrix[i][pick[i]] for i in range(n))
        if best_total is None or total < best_total:
            best_total, best_pick = total, [cols[pick[i]][0] for i in range(n)]
    return {p.key: (best_pick[i], walks, table[i]) for i, p in enumerate(people)}


def generate_month(*, mode: str, start: date, days: int, people: List[Person]) -> dict:
    if mode not in MODES:
        raise ValueError("mode must be BATCH or NON_BATCH")
    warnings: List[str] = []
    results: Dict[str, PersonResult] = {}
    groups: Dict[str, List[Person]] = {}

    for p in sorted(people, key=lambda x: x.employee_id):
        role = classify(p.category, mode, p.fixed_g_non_batch)
        if role == "skipped":
            results[p.key] = PersonResult(p, "skipped", [None] * days, notes=["No roster category set"])
        elif role == "fixed":
            codes = [
                None if d in p.locked else ("WO" if (start + timedelta(days=d)).weekday() == 6 else "G")
                for d in range(days)
            ]
            results[p.key] = PersonResult(p, "fixed", codes, cycle="fixed-g")
        else:
            name, _ = CYCLES[mode][p.category.strip().upper()]
            groups.setdefault(name, []).append(p)

    # 1) Initial phases per cycle group (optimal assignment).
    state: Dict[str, dict] = {}
    for name, members in groups.items():
        cycle = next(c for _cat, (nm, c) in CYCLES[mode].items() if nm == name)
        for key, (ph, walks, row) in _assign_group(members, name, cycle, mode, start, days).items():
            state[key] = {"phase": ph, "walks": walks, "row": row, "cycle": name, "L": len(cycle)}

    # 2) Improve: move or swap people's phases while that lowers
    #    rule cost + distance of each day's A/B/C count from the target.
    by_key = {p.key: p for members in groups.values() for p in members}
    targets = {code: (lo + hi) / 2 for code, (lo, hi) in DAILY_TARGETS[mode].items()}
    counts = [dict() for _ in range(days)]
    for res in results.values():  # fixed staff contribute too (e.g. G)
        for d, c in enumerate(res.codes):
            code = c if c is not None else res.person.locked.get(d)
            if code:
                counts[d][code] = counts[d].get(code, 0) + 1

    def add(key: str, ph: int, sign: int) -> None:
        p = by_key[key]
        cat = p.category.strip().upper()
        lead = lead_counts.get(cat)
        for d, c in enumerate(state[key]["walks"][ph]):
            code = p.locked.get(d, c)
            counts[d][code] = counts[d].get(code, 0) + sign
            if lead is not None:
                lead[d][code] = lead[d].get(code, 0) + sign

    leader_min = LEADER_MIN.get(mode, {})
    lead_counts = {cat: [dict() for _ in range(days)] for cat in leader_min}

    def coverage_cost() -> float:
        total = sum(
            COVERAGE_WEIGHT * (counts[d].get(code, 0) - t) ** 2 for d in range(days) for code, t in targets.items()
        )
        for cat, need in leader_min.items():
            per_day = lead_counts[cat]
            for d in range(days):
                for code, k in need.items():
                    short = k - per_day[d].get(code, 0)
                    if short > 0:
                        total += LEADER_WEIGHT * short
        return total

    for key, st in state.items():
        add(key, st["phase"], +1)
    keys = sorted(state)
    current = coverage_cost() + sum(state[k]["row"][state[k]["phase"]][1] for k in keys)
    for _ in range(12):
        improved = False
        for key in keys:  # single moves
            st = state[key]
            old = st["phase"]
            for ph in range(st["L"]):
                if ph == old or st["row"][ph][1] >= VIOLATION_COST > st["row"][old][1]:
                    continue
                add(key, old, -1)
                add(key, ph, +1)
                cand = coverage_cost() + sum(state[k]["row"][state[k]["phase"] if k != key else ph][1] for k in keys)
                if cand < current - 1e-9:
                    st["phase"], old, current, improved = ph, ph, cand, True
                else:
                    add(key, ph, -1)
                    add(key, old, +1)
        for i, a in enumerate(keys):  # pairwise swaps within a cycle
            for b in keys[i + 1 :]:
                sa, sb = state[a], state[b]
                if sa["cycle"] != sb["cycle"] or sa["phase"] == sb["phase"]:
                    continue
                pa, pb = sa["phase"], sb["phase"]
                delta_rules = sa["row"][pb][1] + sb["row"][pa][1] - sa["row"][pa][1] - sb["row"][pb][1]
                if delta_rules >= VIOLATION_COST:
                    continue
                before = coverage_cost()
                add(a, pa, -1); add(b, pb, -1); add(a, pb, +1); add(b, pa, +1)
                if coverage_cost() - before + delta_rules < -1e-9:
                    sa["phase"], sb["phase"] = pb, pa
                    current += coverage_cost() - before + delta_rules
                    improved = True
                else:
                    add(a, pb, -1); add(b, pa, -1); add(a, pa, +1); add(b, pb, +1)
        if not improved:
            break

    gap_people: List[str] = []
    for name, members in groups.items():
        for p in members:
            st = state[p.key]
            ph = st["phase"]
            codes = st["walks"][ph]
            viol = st["row"][ph][0]
            res = PersonResult(
                p,
                "rotator",
                [None if d in p.locked else c for d, c in enumerate(codes)],
                cycle=name,
                phase=ph,
                end_state={"mode": mode, "cycle": name, "pos": (ph + days) % st["L"]},
            )
            hard = [v for v in viol if v not in SOFT_COST]
            if "GAP" in viol:
                res.notes.append("Next C comes sooner than the cycle's usual spacing")
                gap_people.append(p.name)
            if hard:
                res.notes.append("Could not satisfy " + ", ".join(hard))
                warnings.append(f"{p.name} ({p.employee_id}): could not satisfy rule(s) {', '.join(hard)}")
            results[p.key] = res
    if gap_people:
        warnings.append(
            f"{len(gap_people)} people get their first C of the month sooner than the cycle's usual spacing "
            f"(needed to keep daily numbers up): " + ", ".join(gap_people[:8]) + (" …" if len(gap_people) > 8 else "")
        )

    daily: List[Dict[str, int]] = [dict() for _ in range(days)]
    for res in results.values():
        if res.role == "skipped":
            continue
        for d in range(days):
            code = res.codes[d] if res.codes[d] is not None else res.person.locked.get(d)
            if code:
                daily[d][code] = daily[d].get(code, 0) + 1
    off_target: Dict[str, List[str]] = {}
    for code, (lo, hi) in DAILY_TARGETS[mode].items():
        low = [d for d in range(days) if daily[d].get(code, 0) < lo]
        high = [d for d in range(days) if daily[d].get(code, 0) > hi]
        if low:
            warnings.append(f"{code} below target ({lo}) on {len(low)} of {days} days")
        if high:
            warnings.append(f"{code} above target ({hi}) on {len(high)} of {days} days")
        if low or high:
            off_target[code] = [(start + timedelta(days=d)).isoformat() for d in sorted(low + high)]

    skipped = [r.person for r in results.values() if r.role == "skipped"]
    if skipped:
        warnings.append(
            f"{len(skipped)} people have no roster category and were left unchanged: "
            + ", ".join(f"{p.name} ({p.employee_id})" for p in skipped[:10])
            + (" …" if len(skipped) > 10 else "")
        )

    order = {"fixed": 0, "rotator": 1, "skipped": 2}
    ordered = sorted(results.values(), key=lambda r: (order[r.role], r.cycle or "", r.person.employee_id))
    return {
        "mode": mode,
        "start": start.isoformat(),
        "days": days,
        "dates": [(start + timedelta(days=d)).isoformat() for d in range(days)],
        "cycles": {name: CYCLE_TEXT[name] for name in groups},
        "people": ordered,
        "daily": daily,
        "targets": {k: list(v) for k, v in DAILY_TARGETS[mode].items()},
        "off_target": off_target,
        "warnings": warnings,
    }
