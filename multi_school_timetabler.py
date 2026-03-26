#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-school central campus timetabler.

Iterates through all central campus schools, assigns rooms from the shared
central campus pool, accumulates locked (room, timeslot) slots between schools,
and saves output after each school so infeasibility onset is trackable.

Usage:
    python multi_school_timetabler.py                   # weeks 9-10 (default)
    python multi_school_timetabler.py --start-week 15   # weeks 15-16
"""

import argparse
import gc
import math
from pathlib import Path

import numpy as np
import pandas as pd
import xpress as xp

from data_loader import TimetablingData
from filter_school import SchoolCampusDict
from timetabler.data_prep import build_sets_from_frames

# =============================================================================
# Configuration
# =============================================================================

parser = argparse.ArgumentParser(description="Multi-school central campus timetabler.")
parser.add_argument("--start-week", type=int, default=9,
                    help="First week of the window (default: 9)")
parser.add_argument("--n-weeks", type=int, default=2,
                    help="Number of weeks to schedule (default: 2)")
parser.add_argument("--out-dir", type=str, default="multiSchoolOutput",
                    help="Output directory (default: multiSchoolOutput)")
parser.add_argument("--force", action="store_true",
                    help="Ignore existing output and restart from scratch")
args = parser.parse_args()

start_week = args.start_week
n_weeks = args.n_weeks
target_weeks = set(range(start_week, start_week + n_weeks))
OUT_DIR = Path(args.out_dir)

lambda_cap = 10000
lambda_underut = 100
MAX_SOLVE_SECONDS = 7200  # 2-hour wall-clock limit per school

# Issues with Bioquarter since it has no DPT data 
# College of art has 17 unassigned events -- Lauriston campus
# Campuses: 'Bioquarter', 'Central', 'Easter Bush', 'Holyrood', "King's Buildings", 'Lauriston', 'New College', 'Western General'
current_campus = "Western General"

CENTRAL_CAMPUS_SCHOOLS = [
    name for name, campuses in SchoolCampusDict.items()
    if current_campus in campuses
]

# =============================================================================
# Load Data (once for all schools)
# =============================================================================

print("Loading all university data...")
td = TimetablingData(data_dir="Data").load_all()

central_rooms = td.rooms[
    td.rooms["Campus"].astype(str).str.contains("Central", case=False, na=False)
].copy()
print(f"Central campus rooms: {len(central_rooms)}")
print(f"Central campus schools: {len(CENTRAL_CAMPUS_SCHOOLS)}")
print(f"Target weeks: {sorted(target_weeks)}")

try:
    p = pd.read_excel("penaltyTable.xlsx").iloc[:, 1]
    p = np.array(p)
    p = np.append(p, 1000)
except FileNotFoundError:
    print("WARNING: penaltyTable.xlsx not found — using uniform time penalties")
    p = np.ones(200)  # uniform weights; doesn't affect feasibility

OUT_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================================
# Helper Functions
# =============================================================================

def load_previous_results(out_dir, schools, start_week, n_weeks):
    """Scan out_dir for completed school directories in the current run.

    Returns (completed_indices, prior_log_rows) where:
    - completed_indices: set of 1-based school indices already done in this run
    - prior_log_rows: list of run_log dicts from the previous run_log.xlsx
    """
    completed = set()

    for school_idx, school in enumerate(schools, 1):
        safe_name = school.replace(" ", "_").replace(",", "").replace("/", "-")[:40]
        school_dir = out_dir / f"{school_idx:02d}_{safe_name}"
        status_file = school_dir / "status.txt"

        if not status_file.exists():
            break  # Stop at first gap — don't skip ahead

        completed.add(school_idx)

    # Restore prior run_log rows if available
    prior_log_rows = []
    log_path = out_dir / "run_log.xlsx"
    if completed and log_path.exists():
        prior_df = pd.read_excel(log_path)
        prior_df = prior_df[prior_df["index"].isin(completed)]
        prior_log_rows = prior_df.to_dict("records")

    return completed, prior_log_rows


def load_global_locks(out_dir: Path, tag: str) -> dict:
    """Load all (room, timeslot) locks from every existing solution in out_dir.

    Scans ALL subdirectories regardless of which campus run produced them, so
    a new campus run will respect rooms already booked by prior runs.
    Returns {(room, timeslot): count}.
    """
    from datetime import datetime as _dt, timedelta as _td
    _T = []
    for _day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        _cur = _dt.strptime("09:00", "%H:%M")
        _end = _dt.strptime("17:00", "%H:%M")
        while _cur <= _end:
            _T.append(f"{_day} {_cur.strftime('%H:%M')}")
            _cur += _td(hours=1)
    _t_idx = {t: i for i, t in enumerate(_T)}

    accumulated = {}
    n_files = 0
    for school_dir in sorted(out_dir.iterdir()):
        if not school_dir.is_dir():
            continue
        sol_file = school_dir / f"solution_{tag}.xlsx"
        if not sol_file.exists():
            continue
        n_files += 1
        df = pd.read_excel(sol_file)
        assigned = df[df["Source"].isin(["milp", "fixed_vet"])]
        for _, row in assigned.iterrows():
            if pd.isna(row["Room"]) or pd.isna(row["Timeslot"]):
                continue
            room = str(row["Room"])
            start_ts = str(row["Timeslot"])
            dur = row.get("Duration (minutes)", 60)
            if pd.isna(dur):
                dur = 60
            n_occ = max(1, math.ceil(dur / 60))
            if start_ts in _t_idx:
                ti = _t_idx[start_ts]
                for k in range(n_occ):
                    if ti + k < len(_T):
                        key = (room, _T[ti + k])
                        accumulated[key] = accumulated.get(key, 0) + 1
            else:
                # Snap raw timeslot (e.g. "Monday 09:30") to nearest model hour
                parts = start_ts.split()
                if len(parts) == 2:
                    try:
                        snapped_t = _dt.strptime(parts[1], "%H:%M").replace(minute=0)
                        snapped = f"{parts[0]} {snapped_t.strftime('%H:%M')}"
                        if snapped in _t_idx:
                            ti = _t_idx[snapped]
                            for k in range(n_occ):
                                if ti + k < len(_T):
                                    key = (room, _T[ti + k])
                                    accumulated[key] = accumulated.get(key, 0) + 1
                    except ValueError:
                        pass
    if n_files:
        print(f"  Pre-loaded {len(accumulated):,} locked slots from {n_files} existing solution file(s).")
    return accumulated


def filter_pc_for_school(pc_df, dpt_df, school):
    """Filter Programme-Course to school's modules via DPT join.
    Mirrors filter_school.py lines 98-101 exactly.
    """
    school_dpt = dpt_df[dpt_df["Programme School Name"] == school]
    prog_codes = set(school_dpt["Programme Code"].unique())
    prog_code_col = pc_df["CourseId"].str.extract(r"^(.+?)_YR")[0]
    return pc_df[prog_code_col.isin(prog_codes)].copy()


def room_compatible(e, r, s):
    """Return True if event e can be placed in room r.
    Identical logic to simplifiedModel.py room_compatible().
    """
    event_type = s.event_room_type.get(e)
    room_type = s.room_type.get(r)
    room_lock = s.event_room_lock.get(e)

    if room_lock == "Yes" or room_lock == "No (but must go in a room Type: Teaching Studio)":
        if event_type == "No room required" or pd.isna(event_type):
            return True

        if pd.isna(room_type):
            return False

        e_type = str(event_type).strip().lower()
        r_type = str(room_type).strip().lower()

        ROOM_TYPE_MAP = {
            "teaching studio": "general teaching",
            "centrally allocated space": "general teaching",
        }
        e_type = ROOM_TYPE_MAP.get(e_type, e_type)

        if e_type == r_type:
            return True
        if e_type == "general teaching" and r_type in [
            "general teaching", "lecture theatre", "seminar room"
        ]:
            return True
        if e_type == "laboratory" and "laboratory" in r_type:
            return True
        if e_type == "computer laboratory" and "computer" in r_type:
            return True
        if e_type == "exhibition/event space" and "exhibition" in r_type:
            return True

        return False
    else:
        return True


def room_feasible(e, r, s):
    """Room compatible AND capacity within reasonable range.

    Filters out wildly oversized rooms (>5× event size) to reduce variable count.
    Rooms up to 5× are kept to allow flexibility; overflow is penalised in objective.
    """
    if not room_compatible(e, r, s):
        return False
    # Capacity band filter: skip rooms > 5× event size (reduces model size)
    size = s.event_size.get(e, 0)
    cap = s.room_cap.get(r, 0)
    if pd.notna(size) and pd.notna(cap) and size > 0 and cap > 5 * size:
        return False
    return True


def overflow(e, r, s):
    size = s.event_size.get(e, 0)
    cap = s.room_cap.get(r, 0)
    if pd.isna(size) or pd.isna(cap):
        return 0
    return max(0, size - cap)


def underutilising(e, r, s):
    size = s.event_size.get(e, 0)
    cap = s.room_cap.get(r, 0)
    if pd.isna(size) or pd.isna(cap):
        return 0
    return max(0, cap - size)


# =============================================================================
# Resume Detection + Global Lock Pre-loading
# =============================================================================

run_log = []
completed_indices = set()
tag = f"weeks_{start_week}_{start_week + n_weeks - 1}"

# Pre-load locks from ALL existing solutions (any campus run) so this run
# never double-books a room that was already assigned elsewhere.
if not args.force and OUT_DIR.exists():
    print(f"\nPre-loading existing room locks from {OUT_DIR}/...")
    accumulated_locked = load_global_locks(OUT_DIR, tag)
else:
    accumulated_locked = {}

# Detect which schools in THIS run are already done (for resume).
if not args.force and OUT_DIR.exists():
    print(f"Checking for completed schools in current run...")
    completed_indices, prior_log_rows = load_previous_results(
        OUT_DIR, CENTRAL_CAMPUS_SCHOOLS, start_week, n_weeks
    )
    if completed_indices:
        run_log = prior_log_rows
        next_school = max(completed_indices) + 1
        print(f"  Found {len(completed_indices)} completed school(s). Resuming from school {next_school}.")
    else:
        print("  No completed schools found — starting from the beginning.")
elif args.force:
    print("Starting fresh (--force specified).")

# =============================================================================
# Multi-School Loop
# =============================================================================

for school_idx, school in enumerate(CENTRAL_CAMPUS_SCHOOLS, 1):
    print(f"\n{'='*60}")
    print(f"[{school_idx}/{len(CENTRAL_CAMPUS_SCHOOLS)}] {school}")

    if school_idx in completed_indices:
        safe_name = school.replace(" ", "_").replace(",", "").replace("/", "-")[:40]
        school_dir = OUT_DIR / f"{school_idx:02d}_{safe_name}"
        status = (school_dir / "status.txt").read_text().strip()
        print(f"  [SKIPPED — already completed: {status}]")
        continue

    print(f"  Accumulated locked slots: {len(accumulated_locked)}")

    # --- Filter data for this school ---
    school_events = td.events[td.events["Module Department"] == school].copy()
    school_pc = filter_pc_for_school(td.prog_course, td.dpt, school)

    if school_events.empty:
        print("  No events found — skipping.")
        run_log.append({
            "index": school_idx, "school": school, "status": "skipped",
            "free_events": 0, "milp_assigned": 0,
            "new_slots_locked": 0, "total_slots_locked": len(accumulated_locked),
        })
        continue

    # --- Build sets (injecting accumulated_locked from previous schools) ---
    s = build_sets_from_frames(
        school_events, central_rooms, school_pc,
        target_weeks,
        extra_locked_occupancy=accumulated_locked,
    )

    if not s.E:
        print("  No free events in target weeks — skipping.")
        run_log.append({
            "index": school_idx, "school": school, "status": "skipped",
            "free_events": 0, "milp_assigned": 0,
            "new_slots_locked": 0, "total_slots_locked": len(accumulated_locked),
        })
        continue

    # ==========================================================================
    # Index Sets
    # ==========================================================================
    E = s.E
    R = s.R
    T = s.T

    e_idx = {e: i for i, e in enumerate(E)}
    r_idx = {r: i for i, r in enumerate(R)}
    t_idx = {t: i for i, t in enumerate(T)}
    T_day = [t.split()[0] for t in T]

    # Number of 1-hour slots each event needs (duration rounded up to nearest hour)
    n_slots = {
        e: max(1, math.ceil(s.event_duration.get(e, 60) / 60))
        for e in E
    }

    # ==========================================================================
    # Initialise Model
    # ==========================================================================
    prob = xp.problem(f"Timetabler_{school_idx}")
    prob.controls.outputlog = 0
    prob.controls.maxtime = -MAX_SOLVE_SECONDS

    # ==========================================================================
    # Decision Variables (pre-filtered by room feasibility)
    # ==========================================================================
    # Pre-compute feasible rooms per event (compatibility + capacity)
    compatible_rooms = {
        e: [r for r in R if room_feasible(e, r, s)]
        for e in E
    }

    # Valid start timeslots per event: must not cross a day boundary
    valid_start_slots = {
        e: [
            t for ti, t in enumerate(T)
            if ti + n_slots[e] <= len(T)
            and T_day[ti] == T_day[ti + n_slots[e] - 1]
        ]
        for e in E
    }

    n_unfiltered = sum(len(valid_start_slots[e]) * len(compatible_rooms[e]) for e in E)

    # x[e, r, t] = 1 if event e STARTS at timeslot t in room r
    # Only created where all n_slots[e] consecutive slots are unlocked for room r
    x = {
        (e, r, t): prob.addVariable(
            vartype=xp.binary,
            name=f"x{e_idx[e]}_{r_idx[r]}_{t_idx[t]}"
        )
        for e in E
        for r in compatible_rooms[e]
        for t in valid_start_slots[e]
        if all(
            s.locked_occupancy.get((r, T[t_idx[t] + k]), 0) == 0
            for k in range(n_slots[e])
        )
    }
    compression = (1 - len(x) / n_unfiltered) * 100 if n_unfiltered else 0
    print(f"  Variables: {len(x):,} (was {n_unfiltered:,} unfiltered, {compression:.1f}% reduction)")

    # ==========================================================================
    # Auxiliary variables for C2 (aggregated formulation)
    # ==========================================================================
    # y[e, t] ∈ {0,1} = 1 if event e is assigned to timeslot t (any room)
    # Reduces C2 linking from O(|events_m| × |rooms|) to O(|events_m|) per timeslot
    all_core_modules = {m for mods in s.core_modules_YD.values() for m in mods}
    m_idx = {m: i for i, m in enumerate(sorted(all_core_modules))}

    free_module_events = {
        m: [e for e in E if s.event_module.get(e) == m]
        for m in all_core_modules
        if any(s.event_module.get(e) == m for e in E)
    }

    # Collect which core events need y variables
    core_event_set = {e for evs in free_module_events.values() for e in evs}

    # y[e, t] = 1 if event e OCCUPIES timeslot t (started at t or started earlier
    # and extends to t). Created for each (e, t) where any covering x var exists.
    y = {}
    for e in core_event_set:
        n = n_slots[e]
        for ti, t in enumerate(T):
            # event e occupies slot ti if it started at ti-k for k in [0, n-1]
            vars_covering = [
                x[(e, r, T[ti - k])]
                for k in range(n)
                if ti - k >= 0
                for r in compatible_rooms[e]
                if (e, r, T[ti - k]) in x
            ]
            if vars_covering:
                y[(e, t)] = prob.addVariable(
                    vartype=xp.binary,
                    name=f"y{e_idx[e]}_{t_idx[t]}"
                )

    # Z[module, t] = 1 if module has any event scheduled at timeslot t
    Z = {}
    for m, evs in free_module_events.items():
        for t in T:
            if any((e, t) in y for e in evs):
                Z[(m, t)] = prob.addVariable(
                    vartype=xp.binary,
                    name=f"z{m_idx[m]}_{t_idx[t]}"
                )

    print(f"  Auxiliary vars: {len(y):,} y[e,t] + {len(Z):,} Z[m,t]")

    # ==========================================================================
    # Constraints
    # ==========================================================================
    n_constraints = 0

    # Pre-build slot_usage: (r, t) -> list of x vars that occupy that slot.
    # O(|x| × max_slots) — far cheaper than iterating all R×T×E×slots.
    slot_usage: dict = {}
    for (e, r, t_start), var in x.items():
        ti_start = t_idx[t_start]
        for k in range(n_slots[e]):
            key = (r, T[ti_start + k])
            slot_usage.setdefault(key, []).append(var)

    # C1 — Room conflict: at most one event may occupy each (room, timeslot).
    for (r, t), vars_rt in slot_usage.items():
        if s.locked_occupancy.get((r, t), 0) > 0:
            continue
        if len(vars_rt) > 1:
            prob.addConstraint(xp.Sum(vars_rt) <= 1)
            n_constraints += 1

    c1_count = n_constraints

    # C2 — Core-class conflict (aggregated via y[e,t])
    # Linking y: y[e,t] = sum of x[e,r,t'] for all starts t' that cover slot t
    for (e, t), y_var in y.items():
        ti = t_idx[t]
        n = n_slots[e]
        vars_covering = [
            x[(e, r, T[ti - k])]
            for k in range(n)
            if ti - k >= 0
            for r in compatible_rooms[e]
            if (e, r, T[ti - k]) in x
        ]
        prob.addConstraint(xp.Sum(vars_covering) == y_var)
        n_constraints += 1

    # Linking Z: Z[m,t] >= y[e,t] for events e of module m
    for m, evs in free_module_events.items():
        for t in T:
            if (m, t) not in Z:
                continue
            for e in evs:
                if (e, t) in y:
                    prob.addConstraint(Z[(m, t)] >= y[(e, t)])
                    n_constraints += 1

    # Year-level: sum of Z[m, t] over core modules of year <= 1
    for (year, prog), modules in s.core_modules_YD.items():
        for t in T:
            locked_cls = s.locked_core_classes.get((year, prog, t), set())
            n_locked = len(locked_cls)
            z_vars_yt = [Z[(m, t)] for m in modules if (m, t) in Z and m not in locked_cls]
            rhs = 1 - n_locked
            if rhs <= 0:
                for z_v in z_vars_yt:
                    prob.addConstraint(z_v <= 0)
                    n_constraints += 1
            elif z_vars_yt:
                prob.addConstraint(xp.Sum(z_vars_yt) <= rhs)
                n_constraints += 1

    c2_count = n_constraints - c1_count

    # C3 — Assignment: each event assigned to at most one start (room, timeslot)
    # Soft constraint: unassigned events are penalised heavily in the objective
    lambda_unassigned = 1_000_000  # huge penalty for not assigning an event
    slack = {}  # slack[e] = 1 if event e is NOT assigned
    n_zero_var = 0
    for e in E:
        vars_e = [x[(e, r, t)] for r in compatible_rooms[e] for t in valid_start_slots[e] if (e, r, t) in x]
        if vars_e:
            slack[e] = prob.addVariable(vartype=xp.binary, name=f"slack_{e_idx[e]}")
            prob.addConstraint(xp.Sum(vars_e) + slack[e] == 1)
            n_constraints += 1
        else:
            n_zero_var += 1
            print(f"  WARNING: Event {e} has no feasible (Room, Timeslot) — will be unassigned")

    c3_count = n_constraints - c1_count - c2_count
    print(f"  Constraints: C1={c1_count:,}, C2={c2_count:,}, C3={c3_count:,}, total={n_constraints:,}")
    if n_zero_var:
        print(f"  Events with zero variables (forced unassigned): {n_zero_var}")
    print(f"  Slack variables (soft C3): {len(slack):,}")

    # ==========================================================================
    # Objective Function
    # ==========================================================================
    prob.setObjective(
        xp.Sum(
            var * (p[t_idx[t]] + lambda_cap * overflow(e, r, s) + lambda_underut * underutilising(e, r, s))
            for (e, r, t), var in x.items()
        )
        + xp.Sum(lambda_unassigned * sv for sv in slack.values()),
        sense=xp.minimize
    )

    # ==========================================================================
    # Solve
    # ==========================================================================
    solvestatus, solstatus = prob.optimize()
    status_map = {
        xp.SolStatus.OPTIMAL: "optimal",
        xp.SolStatus.FEASIBLE: "feasible",
        xp.SolStatus.INFEASIBLE: "infeasible",
        xp.SolStatus.NOTFOUND: "notfound",
        xp.SolStatus.UNBOUNDED: "unbounded",
    }
    solve_status = status_map.get(solstatus, "unknown")
    solve_status_names = {
        xp.SolveStatus.COMPLETED: "completed",
        xp.SolveStatus.STOPPED: "stopped (time/resource limit)",
        xp.SolveStatus.FAILED: "failed",
        xp.SolveStatus.UNSTARTED: "unstarted",
    }
    print(f"  Solve: {solve_status} (SolveStatus={solve_status_names.get(solvestatus, solvestatus)}, SolStatus={solve_status})")

    # ==========================================================================
    # Solution Extraction
    # ==========================================================================
    rows = []

    # Accept both optimal and feasible (incumbent) solutions
    if solve_status in ("optimal", "feasible"):
        var_keys = list(x.keys())
        var_list = list(x.values())
        sol_vals = prob.getSolution(var_list)
        for (e, r, t), val in zip(var_keys, sol_vals):
            if val > 0.5:
                rows.append({
                    "Event ID": e, "Room": r, "Timeslot": t, "Source": "milp",
                    "Event Size": s.event_size.get(e),
                    "Room Capacity": s.room_cap.get(r),
                })

        # Report unassigned events (slack = 1)
        if slack:
            slack_keys = list(slack.keys())
            slack_vals = prob.getSolution(list(slack.values()))
            unassigned = [e for e, val in zip(slack_keys, slack_vals) if val > 0.5]
            if unassigned:
                print(f"  ⚠ {len(unassigned)} events UNASSIGNED (out of {len(E)} free events)")
                for e in unassigned[:10]:
                    print(f"    Event {e}: size={s.event_size.get(e)}, "
                          f"room_type='{s.event_room_type.get(e)}'")
                if len(unassigned) > 10:
                    print(f"    ... and {len(unassigned) - 10} more")

    for e, assignments in s.fixed_vet.items():
        for r, t in assignments:
            rows.append({
                "Event ID": e, "Room": r, "Timeslot": t, "Source": "fixed_vet",
                "Event Size": s.event_size.get(e),
                "Room Capacity": s.room_cap.get(r),
            })

    for e, assignments in s.fixed_non_vet.items():
        for r, t in assignments:
            rows.append({
                "Event ID": e, "Room": r, "Timeslot": t, "Source": "fixed_non_vet",
                "Event Size": s.event_size.get(e),
                "Room Capacity": s.room_cap.get(r),
            })

    solution_df = pd.DataFrame(
        rows,
        columns=["Event ID", "Room", "Timeslot", "Source", "Event Size", "Room Capacity"],
    ) if rows else pd.DataFrame(
        columns=["Event ID", "Room", "Timeslot", "Source", "Event Size", "Room Capacity"]
    )

    # Enrich with event metadata
    if s.events_raw is not None and not solution_df.empty:
        event_meta_cols = ["Event ID", "Event Name", "Event Type", "Module Code",
                           "Module Name", "Duration (minutes)", "Weeks", "Semester"]
        available = [c for c in event_meta_cols if c in s.events_raw.columns]
        event_meta = s.events_raw[available].drop_duplicates(subset="Event ID")
        solution_df = solution_df.merge(event_meta, on="Event ID", how="left")

    # ==========================================================================
    # Save Output
    # ==========================================================================
    safe_name = school.replace(" ", "_").replace(",", "").replace("/", "-")[:40]
    school_dir = OUT_DIR / f"{school_idx:02d}_{safe_name}"
    school_dir.mkdir(parents=True, exist_ok=True)

    tag = f"weeks_{start_week}_{start_week + n_weeks - 1}"
    (school_dir / "status.txt").write_text(solve_status)

    if solve_status in ("optimal", "feasible") and not solution_df.empty:
        out_path = school_dir / f"solution_{tag}.xlsx"
        solution_df.to_excel(out_path, index=False)
        print(f"  Saved → {out_path}  ({len(solution_df):,} rows)")
    else:
        print(f"  No solution to save (status: {solve_status})")

    # ==========================================================================
    # Update accumulated_locked with this school's assignments
    # ==========================================================================
    new_slots = 0
    if solve_status in ("optimal", "feasible"):
        assigned = solution_df[solution_df["Source"].isin(["milp", "fixed_vet"])]
        for _, row in assigned.iterrows():
            if pd.notna(row["Room"]) and pd.notna(row["Timeslot"]):
                room = str(row["Room"])
                start_ts = str(row["Timeslot"])
                # Lock ALL occupied slots (not just the start slot)
                dur = row.get("Duration (minutes)", 60)
                if pd.isna(dur):
                    dur = 60
                n_occ = max(1, math.ceil(dur / 60))
                if start_ts in t_idx:
                    ti_start = t_idx[start_ts]
                    for k in range(n_occ):
                        if ti_start + k < len(T):
                            key = (room, T[ti_start + k])
                            accumulated_locked[key] = accumulated_locked.get(key, 0) + 1
                            new_slots += 1
                else:
                    # Fallback: lock just the start slot if not in model T
                    key = (room, start_ts)
                    accumulated_locked[key] = accumulated_locked.get(key, 0) + 1
                    new_slots += 1

    n_milp = len([r for r in rows if r["Source"] == "milp"])
    run_log.append({
        "index": school_idx,
        "school": school,
        "status": solve_status,
        "free_events": len(E),
        "milp_assigned": n_milp,
        "new_slots_locked": new_slots,
        "total_slots_locked": len(accumulated_locked),
    })

    # Free Xpress problem and large intermediate dicts before next school
    del prob, x, y, Z, slot_usage, s, solution_df, rows
    gc.collect()

# =============================================================================
# Save Run Log
# =============================================================================
log_df = pd.DataFrame(run_log)
log_path = OUT_DIR / "run_log.xlsx"
log_df.to_excel(log_path, index=False)
print(f"\n{'='*60}")
print(f"Multi-school run complete.")
print(f"Run log → {log_path}")
print(log_df.to_string(index=False))
