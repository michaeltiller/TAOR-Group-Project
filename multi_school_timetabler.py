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

CENTRAL_CAMPUS_SCHOOLS = [
    name for name, campuses in SchoolCampusDict.items()
    if "Central" in campuses
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

p = pd.read_excel("penaltyTable.xlsx").iloc[:, 1]
p = np.array(p)
p = np.append(p, 1000)

OUT_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================================
# Helper Functions
# =============================================================================

def load_previous_results(out_dir, schools, start_week, n_weeks):
    """Scan out_dir for completed school directories.

    Returns (completed_indices, accumulated_locked, prior_log_rows) where:
    - completed_indices: set of 1-based school indices already done
    - accumulated_locked: {(room, timeslot): count} reconstructed from saved solutions
    - prior_log_rows: list of run_log dicts from the previous run_log.xlsx
    """
    tag = f"weeks_{start_week}_{start_week + n_weeks - 1}"
    accumulated_locked = {}
    completed = set()

    for school_idx, school in enumerate(schools, 1):
        safe_name = school.replace(" ", "_").replace(",", "").replace("/", "-")[:40]
        school_dir = out_dir / f"{school_idx:02d}_{safe_name}"
        status_file = school_dir / "status.txt"

        if not status_file.exists():
            break  # Stop at first gap — don't skip ahead

        status = status_file.read_text().strip()
        completed.add(school_idx)

        if status == "optimal":
            sol_file = school_dir / f"solution_{tag}.xlsx"
            if sol_file.exists():
                df = pd.read_excel(sol_file)
                assigned = df[df["Source"].isin(["milp", "fixed_vet"])]
                for _, row in assigned.iterrows():
                    if pd.notna(row["Room"]) and pd.notna(row["Timeslot"]):
                        key = (str(row["Room"]), str(row["Timeslot"]))
                        accumulated_locked[key] = accumulated_locked.get(key, 0) + 1

    # Restore prior run_log rows if available
    prior_log_rows = []
    log_path = out_dir / "run_log.xlsx"
    if completed and log_path.exists():
        prior_df = pd.read_excel(log_path)
        prior_df = prior_df[prior_df["index"].isin(completed)]
        prior_log_rows = prior_df.to_dict("records")

    return completed, accumulated_locked, prior_log_rows


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
    """Room compatible AND capacity reasonable (hard overflow filter)."""
    if not room_compatible(e, r, s):
        return False
    size = s.event_size.get(e, 0)
    cap = s.room_cap.get(r, 0)
    if pd.isna(size) or pd.isna(cap):
        return True  # can't filter, allow
    if cap < size:
        return False  # hard overflow — event can't fit
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
# Resume Detection
# =============================================================================

accumulated_locked = {}
run_log = []
completed_indices = set()

if not args.force and OUT_DIR.exists():
    print(f"\nChecking for existing output in {OUT_DIR}/...")
    completed_indices, accumulated_locked, prior_log_rows = load_previous_results(
        OUT_DIR, CENTRAL_CAMPUS_SCHOOLS, start_week, n_weeks
    )
    if completed_indices:
        run_log = prior_log_rows
        next_school = max(completed_indices) + 1
        print(f"  Found {len(completed_indices)} completed school(s). Resuming from school {next_school}.")
        print(f"  Reconstructed {len(accumulated_locked)} locked (room, timeslot) slots.")
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

    available_rt = [
        (r, t)
        for r in R
        for t in T
        if s.locked_occupancy.get((r, t), 0) == 0
    ]

    # ==========================================================================
    # Initialise Model
    # ==========================================================================
    prob = xp.problem(f"Timetabler_{school_idx}")
    prob.controls.outputlog = 0

    # ==========================================================================
    # Decision Variables (pre-filtered by room feasibility)
    # ==========================================================================
    # Pre-compute feasible rooms per event (compatibility + capacity)
    compatible_rooms = {
        e: [r for r in R if room_feasible(e, r, s)]
        for e in E
    }
    n_unfiltered = len(E) * len(available_rt)

    # x[e, r, t] = 1 if event e assigned to room r at timeslot t
    x = {
        (e, r, t): prob.addVariable(
            vartype=xp.binary,
            name=f"x{e_idx[e]}_{r_idx[r]}_{t_idx[t]}"
        )
        for e in E
        for r in compatible_rooms[e]
        for t in T
        if s.locked_occupancy.get((r, t), 0) == 0
    }
    compression = (1 - len(x) / n_unfiltered) * 100 if n_unfiltered else 0
    print(f"  Variables: {len(x):,} (was {n_unfiltered:,} unfiltered, {compression:.1f}% reduction)")

    # ==========================================================================
    # Auxiliary variables for C2 (aggregated formulation)
    # ==========================================================================
    # y[e, t] ∈ {0,1} = 1 if event e is assigned to timeslot t (any room)
    # Reduces C2 linking from O(|events_m| × |rooms|) to O(|events_m|) per timeslot
    all_core_modules = {m for mods in s.core_modules_Y.values() for m in mods}
    m_idx = {m: i for i, m in enumerate(sorted(all_core_modules))}

    free_module_events = {
        m: [e for e in E if s.event_module.get(e) == m]
        for m in all_core_modules
        if any(s.event_module.get(e) == m for e in E)
    }

    # Collect which core events need y variables
    core_event_set = {e for evs in free_module_events.values() for e in evs}

    y = {}
    for e in core_event_set:
        for t in T:
            vars_et = [x[(e, r, t)] for r in compatible_rooms[e] if (e, r, t) in x]
            if vars_et:
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

    # C1 — Room conflict: at most one event per (room, timeslot)
    for r in R:
        for t in T:
            if s.locked_occupancy.get((r, t), 0) > 0:
                continue
            vars_rt = [x[(e, r, t)] for e in E if (e, r, t) in x]
            if len(vars_rt) > 1:
                prob.addConstraint(xp.Sum(vars_rt) <= 1)
                n_constraints += 1

    c1_count = n_constraints

    # C2 — Core-class conflict (aggregated via y[e,t])
    # Linking y: y[e,t] = sum_r x[e,r,t] for core events
    for (e, t), y_var in y.items():
        vars_et = [x[(e, r, t)] for r in compatible_rooms[e] if (e, r, t) in x]
        prob.addConstraint(xp.Sum(vars_et) == y_var)
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
    for year, modules in s.core_modules_Y.items():
        for t in T:
            locked_cls = s.locked_core_classes.get((year, t), set())
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

    # C3 — Assignment: each event assigned to exactly one (room, timeslot)
    for e in E:
        vars_e = [x[(e, r, t)] for r in compatible_rooms[e] for t in T if (e, r, t) in x]
        if vars_e:
            prob.addConstraint(xp.Sum(vars_e) == 1)
            n_constraints += 1
        else:
            print(f"  WARNING: Event {e} has no feasible (Room, Timeslot) — model will be infeasible")

    c3_count = n_constraints - c1_count - c2_count
    print(f"  Constraints: C1={c1_count:,}, C2={c2_count:,}, C3={c3_count:,}, total={n_constraints:,}")

    # ==========================================================================
    # Objective Function
    # ==========================================================================
    prob.setObjective(
        xp.Sum(
            var * (p[t_idx[t]] + lambda_cap * overflow(e, r, s) + lambda_underut * underutilising(e, r, s))
            for (e, r, t), var in x.items()
        ),
        sense=xp.minimize
    )

    # ==========================================================================
    # Solve
    # ==========================================================================
    prob.solve()
    solvestatus, solstatus = prob.optimize()
    status_map = {
        xp.SolStatus.OPTIMAL: "optimal",
        xp.SolStatus.INFEASIBLE: "infeasible",
    }
    solve_status = status_map.get(solstatus, "unknown")
    print(f"  Solve: {solve_status} (SolveStatus={solvestatus}, SolStatus={solstatus})")

    # ==========================================================================
    # Solution Extraction
    # ==========================================================================
    rows = []

    if solve_status == "optimal":
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

    if solve_status == "optimal" and not solution_df.empty:
        out_path = school_dir / f"solution_{tag}.xlsx"
        solution_df.to_excel(out_path, index=False)
        print(f"  Saved → {out_path}  ({len(solution_df):,} rows)")
    else:
        print(f"  No solution to save (status: {solve_status})")

    # ==========================================================================
    # Update accumulated_locked with this school's assignments
    # ==========================================================================
    new_slots = 0
    if solve_status == "optimal":
        assigned = solution_df[solution_df["Source"].isin(["milp", "fixed_vet"])]
        for _, row in assigned.iterrows():
            if pd.notna(row["Room"]) and pd.notna(row["Timeslot"]):
                key = (str(row["Room"]), str(row["Timeslot"]))
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
