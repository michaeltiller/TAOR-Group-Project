#!/usr/bin/env python3
"""
Quick diagnostic solve — runs multi-school with short time limits
and enhanced logging to identify exactly where/why infeasibility occurs.
"""

import math
import gc
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import xpress as xp

from data_loader import TimetablingData
from filter_school import SchoolCampusDict
from timetabler.data_prep import build_sets_from_frames
from utils import parse_weeks

# ── Config ──────────────────────────────────────────────────────────────────
TARGET_WEEKS = {9, 10}
MAX_SOLVE_SECONDS = 300  # 5 minutes per school (enough to prove infeasibility quickly)

lambda_cap = 10000
lambda_underut = 100

CENTRAL_SCHOOLS = [
    name for name, campuses in SchoolCampusDict.items()
    if "Central" in campuses
]

# ── Load data ───────────────────────────────────────────────────────────────
td = TimetablingData(data_dir="Data").load_all()

central_rooms = td.rooms[
    td.rooms["Campus"].astype(str).str.contains("Central", case=False, na=False)
].copy()

# Uniform time penalties (penaltyTable.xlsx is missing — doesn't affect feasibility)
p = np.ones(100)  # more than enough for 50 timeslots

# ── Helpers (from multi_school_timetabler.py) ───────────────────────────────
def filter_pc_for_school(pc_df, dpt_df, school):
    school_dpt = dpt_df[dpt_df["Programme School Name"] == school]
    prog_codes = set(school_dpt["Programme Code"].unique())
    prog_code_col = pc_df["CourseId"].str.extract(r"^(.+?)_YR")[0]
    return pc_df[prog_code_col.isin(prog_codes)].copy()

def room_compatible(e, r, s):
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
        ROOM_TYPE_MAP = {"teaching studio": "general teaching", "centrally allocated space": "general teaching"}
        e_type = ROOM_TYPE_MAP.get(e_type, e_type)
        if e_type == r_type: return True
        if e_type == "general teaching" and r_type in ["general teaching", "lecture theatre", "seminar room"]: return True
        if e_type == "laboratory" and "laboratory" in r_type: return True
        if e_type == "computer laboratory" and "computer" in r_type: return True
        if e_type == "exhibition/event space" and "exhibition" in r_type: return True
        return False
    return True

def room_feasible(e, r, s):
    return room_compatible(e, r, s)

def overflow(e, r, s):
    size = s.event_size.get(e, 0)
    cap = s.room_cap.get(r, 0)
    if pd.isna(size) or pd.isna(cap): return 0
    return max(0, size - cap)

def underutilising(e, r, s):
    size = s.event_size.get(e, 0)
    cap = s.room_cap.get(r, 0)
    if pd.isna(size) or pd.isna(cap): return 0
    return max(0, cap - size)

# ── Main loop ───────────────────────────────────────────────────────────────
accumulated_locked = {}

for school_idx, school in enumerate(CENTRAL_SCHOOLS, 1):
    print(f"\n{'='*70}")
    print(f"[{school_idx}/{len(CENTRAL_SCHOOLS)}] {school}")
    print(f"  Accumulated locked slots: {len(accumulated_locked)}")

    school_events = td.events[td.events["Module Department"] == school].copy()
    school_pc = filter_pc_for_school(td.prog_course, td.dpt, school)

    if school_events.empty:
        print("  No events — skipping.")
        continue

    s = build_sets_from_frames(
        school_events, central_rooms, school_pc,
        TARGET_WEEKS,
        extra_locked_occupancy=accumulated_locked,
    )

    if not s.E:
        print("  No free events — skipping.")
        continue

    E, R, T = s.E, s.R, s.T
    e_idx = {e: i for i, e in enumerate(E)}
    r_idx = {r: i for i, r in enumerate(R)}
    t_idx = {t: i for i, t in enumerate(T)}
    T_day = [t.split()[0] for t in T]

    n_slots = {e: max(1, math.ceil(s.event_duration.get(e, 60) / 60)) for e in E}

    # ── C2 feasibility pre-check ──
    all_core_modules = {m for mods in s.core_modules_YD.values() for m in mods}
    print(f"  Core modules: {len(all_core_modules)}")
    for (year, prog), modules in s.core_modules_YD.items():
        print(f"    Year {year} [{prog}]: {len(modules)} core modules, needs ≥{len(modules)} timeslots (have {len(T)})")

    # ── Build model ──
    prob = xp.problem(f"Diag_{school_idx}")
    prob.controls.outputlog = 0
    prob.controls.maxtime = -MAX_SOLVE_SECONDS

    compatible_rooms = {e: [r for r in R if room_feasible(e, r, s)] for e in E}
    valid_start_slots = {
        e: [t for ti, t in enumerate(T) if ti + n_slots[e] <= len(T) and T_day[ti] == T_day[ti + n_slots[e] - 1]]
        for e in E
    }

    x = {
        (e, r, t): prob.addVariable(vartype=xp.binary, name=f"x{e_idx[e]}_{r_idx[r]}_{t_idx[t]}")
        for e in E for r in compatible_rooms[e] for t in valid_start_slots[e]
        if all(s.locked_occupancy.get((r, T[t_idx[t] + k]), 0) == 0 for k in range(n_slots[e]))
    }

    n_unfiltered = sum(len(valid_start_slots[e]) * len(compatible_rooms[e]) for e in E)
    compression = (1 - len(x) / n_unfiltered) * 100 if n_unfiltered else 0
    print(f"  Variables: {len(x):,} (was {n_unfiltered:,}, {compression:.1f}% reduction)")

    # ── Check zero-variable events ──
    zero_var_events = []
    for e in E:
        vars_e = [(e, r, t) for r in compatible_rooms[e] for t in valid_start_slots[e] if (e, r, t) in x]
        if not vars_e:
            dur = s.event_duration.get(e, 60)
            rt = s.event_room_type.get(e)
            lock = s.event_room_lock.get(e)
            n_compat = len(compatible_rooms[e])
            n_valid = len(valid_start_slots[e])
            zero_var_events.append((e, dur, rt, lock, n_compat, n_valid))

    if zero_var_events:
        print(f"\n  *** {len(zero_var_events)} EVENTS WITH ZERO FEASIBLE VARIABLES ***")
        print(f"  These make C3 infeasible.")
        for e, dur, rt, lock, n_compat, n_valid in zero_var_events[:10]:
            print(f"    Event {e}: dur={dur}min, room_type='{rt}', lock='{lock}', "
                  f"compat_rooms={n_compat}, valid_starts={n_valid}")
            # Check why: is it locked rooms or no compatible rooms?
            if n_compat == 0:
                print(f"      → No compatible rooms at all!")
            elif n_valid == 0:
                print(f"      → No valid start slots (event too long for any day?)")
            else:
                # Check how many slots are locked
                locked_count = 0
                total_combos = 0
                for r in compatible_rooms[e]:
                    for t in valid_start_slots[e]:
                        total_combos += 1
                        if any(s.locked_occupancy.get((r, T[t_idx[t] + k]), 0) > 0 for k in range(n_slots[e])):
                            locked_count += 1
                print(f"      → {locked_count}/{total_combos} (room,slot) combos locked by prior schools")

        print(f"\n  Model is INFEASIBLE due to zero-variable events. Skipping solve.")
        # Don't solve — just accumulate what we can and move on
        # For diagnosis, we still want to see what happens at subsequent schools
        print(f"  (Would need to drop or relax these events to proceed)")
        del prob, x
        gc.collect()
        continue

    # ── Auxiliary vars for C2 ──
    free_module_events = {
        m: [e for e in E if s.event_module.get(e) == m]
        for m in all_core_modules if any(s.event_module.get(e) == m for e in E)
    }
    core_event_set = {e for evs in free_module_events.values() for e in evs}

    y = {}
    for e in core_event_set:
        n = n_slots[e]
        for ti, t in enumerate(T):
            vars_covering = [
                x[(e, r, T[ti - k])]
                for k in range(n) if ti - k >= 0
                for r in compatible_rooms[e] if (e, r, T[ti - k]) in x
            ]
            if vars_covering:
                y[(e, t)] = prob.addVariable(vartype=xp.binary, name=f"y{e_idx[e]}_{t_idx[t]}")

    Z = {}
    for m, evs in free_module_events.items():
        m_i = list(all_core_modules).index(m)
        for t in T:
            if any((e, t) in y for e in evs):
                Z[(m, t)] = prob.addVariable(vartype=xp.binary, name=f"z{m_i}_{t_idx[t]}")

    # ── Constraints ──
    slot_usage = {}
    for (e, r, t_start), var in x.items():
        for k in range(n_slots[e]):
            key = (r, T[t_idx[t_start] + k])
            slot_usage.setdefault(key, []).append(var)

    n_con = 0
    for (r, t), vars_rt in slot_usage.items():
        if s.locked_occupancy.get((r, t), 0) > 0: continue
        if len(vars_rt) > 1:
            prob.addConstraint(xp.Sum(vars_rt) <= 1)
            n_con += 1

    for (e, t), y_var in y.items():
        ti = t_idx[t]
        n = n_slots[e]
        vars_covering = [
            x[(e, r, T[ti - k])] for k in range(n) if ti - k >= 0
            for r in compatible_rooms[e] if (e, r, T[ti - k]) in x
        ]
        prob.addConstraint(xp.Sum(vars_covering) == y_var)
        n_con += 1

    for m, evs in free_module_events.items():
        for t in T:
            if (m, t) not in Z: continue
            for e in evs:
                if (e, t) in y:
                    prob.addConstraint(Z[(m, t)] >= y[(e, t)])
                    n_con += 1

    for (year, prog), modules in s.core_modules_YD.items():
        for t in T:
            locked_cls = s.locked_core_classes.get((year, prog, t), set())
            n_locked = len(locked_cls)
            z_vars_yt = [Z[(m, t)] for m in modules if (m, t) in Z and m not in locked_cls]
            rhs = 1 - n_locked
            if rhs <= 0:
                for z_v in z_vars_yt:
                    prob.addConstraint(z_v <= 0)
                    n_con += 1
            elif z_vars_yt:
                prob.addConstraint(xp.Sum(z_vars_yt) <= rhs)
                n_con += 1

    for e in E:
        vars_e = [x[(e, r, t)] for r in compatible_rooms[e] for t in valid_start_slots[e] if (e, r, t) in x]
        if vars_e:
            prob.addConstraint(xp.Sum(vars_e) == 1)
            n_con += 1

    print(f"  Constraints: {n_con:,}")

    # ── Objective ──
    prob.setObjective(
        xp.Sum(
            var * (p[t_idx[t]] + lambda_cap * overflow(e, r, s) + lambda_underut * underutilising(e, r, s))
            for (e, r, t), var in x.items()
        ),
        sense=xp.minimize
    )

    # ── Solve ──
    print(f"  Solving (max {MAX_SOLVE_SECONDS}s)...")
    solvestatus, solstatus = prob.optimize()

    status_names = {
        xp.SolStatus.OPTIMAL: "OPTIMAL",
        xp.SolStatus.FEASIBLE: "FEASIBLE (not proven optimal)",
        xp.SolStatus.INFEASIBLE: "INFEASIBLE",
        xp.SolStatus.NOTFOUND: "NOTFOUND (no solution yet)",
        xp.SolStatus.UNBOUNDED: "UNBOUNDED",
    }
    solve_names = {
        xp.SolveStatus.COMPLETED: "COMPLETED",
        xp.SolveStatus.STOPPED: "STOPPED (time/resource limit)",
        xp.SolveStatus.FAILED: "FAILED",
        xp.SolveStatus.UNSTARTED: "UNSTARTED",
    }
    print(f"  SolveStatus: {solve_names.get(solvestatus, solvestatus)}")
    print(f"  SolStatus:   {status_names.get(solstatus, solstatus)}")

    # ── Accumulate locks if solved ──
    if solstatus in (xp.SolStatus.OPTIMAL, xp.SolStatus.FEASIBLE):
        var_keys = list(x.keys())
        var_list = list(x.values())
        sol_vals = prob.getSolution(var_list)
        assigned = [(e, r, t) for (e, r, t), val in zip(var_keys, sol_vals) if val > 0.5]
        print(f"  Assigned: {len(assigned)} events")

        # Accumulate ALL occupied slots (fix for multi-hour events)
        new_locks = 0
        for e, r, t_start in assigned:
            ti = t_idx[t_start]
            for k in range(n_slots[e]):
                key = (r, T[ti + k])
                accumulated_locked[key] = accumulated_locked.get(key, 0) + 1
                new_locks += 1
        print(f"  New locked slots: {new_locks} (total: {len(accumulated_locked)})")

        # Also accumulate fixed_vet
        for e, assignments in s.fixed_vet.items():
            for r, t in assignments:
                if pd.notna(r) and pd.notna(t):
                    key = (str(r), str(t))
                    accumulated_locked[key] = accumulated_locked.get(key, 0) + 1
    else:
        print(f"  No solution to accumulate.")
        if solstatus == xp.SolStatus.INFEASIBLE:
            print(f"\n  *** INFEASIBILITY DETECTED ***")
            print(f"  Stopping here to investigate.")
            break

    del prob, x, y, Z, slot_usage, s
    gc.collect()

print(f"\n{'='*70}")
print("Diagnostic solve complete.")
print(f"Final accumulated locked slots: {len(accumulated_locked)}")
