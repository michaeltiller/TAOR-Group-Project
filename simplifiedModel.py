#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Mar 11 15:14:04 2026

@author: michael
"""

import argparse
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from timetabler import Timetabler
from utils import DAY_ORDER, HOUR_ORDER, parse_timeslot
from typing import Any
from collections import Counter

import xpress as xp
from datetime import datetime, timedelta

from pathlib import Path

import pandas as pd

from utils import VET_DATA_DIR

from timetabler.data_prep import TimetablerSets, build_sets
from timetabler.model_builder import ModelBuilder
from timetabler.solution import SolutionExtractor
import matplotlib.pyplot as plt

import pandas as pd


school = "vet"

data_dir = f"Data/{school}/"


start_week = 9
n_weeks= 2
target_weeks = set(range(start_week, start_week + n_weeks))

# Model parameters 
lambda_cap = 10000
lambda_underut= 100

s = build_sets(data_dir, target_weeks)



p = pd.read_excel('penaltyTable.xlsx').iloc[:, 1]
p = np.array(p)
p = np.append(p, 1000)


# =============================================================================
# Index sets
# =============================================================================
E = s.E          # events
R = s.R          # rooms
T = s.T          # timeslots

e_idx = {e: i for i, e in enumerate(E)}
r_idx = {r: i for i, r in enumerate(R)}
t_idx = {t: i for i, t in enumerate(T)}

available_rt = [
    (r, t)
    for r in R
    for t in T
    if s.locked_occupancy.get((r, t), 0) == 0
]

# =============================================================================
# Initialise Model
# =============================================================================
prob = xp.problem("Vet Timetabler")
prob.controls.outputlog = 0

# =============================================================================
# Decision Variables
# =============================================================================
# x[e, r, t] = 1 if event e is assigned to room r at timeslot t
x = {
    (e, r, t): prob.addVariable(
        vartype=xp.binary,
        name=f"x{e_idx[e]}_{r_idx[r]}_{t_idx[t]}"
    )
    for e in E
    for r, t in available_rt
}

# Z[module, t] = 1 if module has any event scheduled at timeslot t (for C2)
all_core_modules = {m for mods in s.core_modules_YD.values() for m in mods}
m_idx = {m: i for i, m in enumerate(sorted(all_core_modules))}

free_module_events = {
    m: [e for e in E if s.event_module.get(e) == m]
    for m in all_core_modules
    if any(s.event_module.get(e) == m for e in E)
}

Z = {}
for m, evs in free_module_events.items():
    for t in T:
        vars_mt = [x[(e, r, t)] for e in evs for r in R if (e, r, t) in x]
        if vars_mt:
            Z[(m, t)] = prob.addVariable(
                vartype=xp.binary,
                name=f"z{m_idx[m]}_{t_idx[t]}"
            )



# C1 — Room conflict: at most one event per (room, timeslot)
for r in R:
    for t in T:
        if s.locked_occupancy.get((r, t), 0) > 0:
            continue
        vars_rt = [x[(e, r, t)] for e in E if (e, r, t) in x]
        if len(vars_rt) > 1:
            prob.addConstraint(xp.Sum(vars_rt) <= 1)

# C2 — Core-class conflict: at most one compulsory class per (year, timeslot)
# Linking constraints: Z[m, t] >= x[e, r, t] for all events e of module m
for m, evs in free_module_events.items():
    for t in T:
        if (m, t) not in Z:
            continue
        vars_mt = [x[(e, r, t)] for e in evs for r in R if (e, r, t) in x]
        prob.addConstraint([Z[(m, t)] >= v for v in vars_mt])

# Year-level: sum of Z[m, t] over core modules of year <= 1
for (year, prog), modules in s.core_modules_YD.items():
    for t in T:
        locked_cls = s.locked_core_classes.get((year, prog, t), set())
        n_locked = len(locked_cls)
        z_vars_yt = [Z[(m, t)] for m in modules if (m, t) in Z and m not in locked_cls]
        rhs = 1 - n_locked
        if rhs <= 0:
            prob.addConstraint([z_v <= 0 for z_v in z_vars_yt])
        elif z_vars_yt:
            prob.addConstraint(xp.Sum(z_vars_yt) <= rhs)

# C3 — Assignment: each event assigned to exactly one (room, timeslot)
for e in E:
    vars_e = [x[(e, r, t)] for r in R for t in T if (e, r, t) in x]
    if vars_e:
        prob.addConstraint(xp.Sum(vars_e) == 1)
    else:
        print(f"WARNING: Event {e} has no feasible (Room, Timeslot) — model will be infeasible")
        
        
# need to deal with specialty room assignments 

# will probably cause infeasibillity # need to change to only apply if lock = true from event_module_room 

def room_compatible(e, r):
    event_type = s.event_room_type.get(e)
    room_type = s.room_type.get(r)
    room_lock = s.event_room_lock.get(e)
    # 1. No requirement → allow
    if room_lock == 'Yes' or room_lock == 'No (but must go in a room Type: Teaching Studio)':
        if event_type == "No room required" or pd.isna(event_type):
            return True
    
        if pd.isna(room_type):
            return False
    
        # 2. Normalise
        e_type = event_type.strip().lower()
        r_type = room_type.strip().lower()
    
        # 3. Explicit mappings (fix your bad types)
        ROOM_TYPE_MAP = {
            "teaching studio": "general teaching", # there actually are teaching studios 
            "centrally allocated space": "general teaching",
    
        }
    
        e_type = ROOM_TYPE_MAP.get(e_type, e_type)
    
        # 4. Compatibility rules (core logic)
        if e_type == r_type:
            return True
    
        # General teaching is flexible
        if e_type == "general teaching" and r_type in [
            "general teaching",
            "lecture theatre",
            "seminar room",
        ]:
            return True
    
        # Labs hierarchy
        if e_type == "laboratory" and "laboratory" in r_type:
            return True
    
        if e_type == "computer laboratory" and "computer" in r_type:
            return True
    
        # Exhibition / special spaces
        if e_type == "exhibition/event space" and "exhibition" in r_type:
            return True

        # Fallback (strict)
        return False
    else: 
        return True 


compat = {
    (e, r): int(room_compatible(e, r))
    for e in E
    for r in R
}

for (e, r, t), var in x.items():
    prob.addConstraint(var <= compat[(e, r)])




# =============================================================================
# Objective Function
# =============================================================================

#Penalise bad times and if event exceeds room capacity 

def overflow(e, r):
    size = s.event_size.get(e, 0)
    cap = s.room_cap.get(r, 0)

    # handle NaN / None
    if pd.isna(size) or pd.isna(cap):
        return 0  # or a penalty if you prefer

    return max(0, size - cap)

def underutilising(e, r):
    size = s.event_size.get(e, 0)
    cap = s.room_cap.get(r, 0)

    # handle NaN / None
    if pd.isna(size) or pd.isna(cap):
        return 0  # or a penalty if you prefer

    return max(0, cap - size )



prob.setObjective(
    xp.Sum(x[e, r, t] *( p[t_idx[t]] + lambda_cap * overflow(e, r)  + lambda_underut * underutilising(e,r)) for e in E for r in R for t in T if (e, r, t) in x),
    sense=xp.minimize)



# =============================================================================
# Solve
# =============================================================================
#prob.setControl('miprelstop', .50) # stop once the mip gap is below 5%
#prob.controls.maxtime = -60*5

prob.solve()

solvestatus, solstatus = prob.optimize()

status_map = {
    xp.SolStatus.OPTIMAL: "optimal",
    xp.SolStatus.INFEASIBLE: "infeasible",
}
solve_status = status_map.get(solstatus, "unknown")
print(f"Solve complete: {solve_status} (SolveStatus={solvestatus}, SolStatus={solstatus})")

# =============================================================================
# Solution Extraction
# =============================================================================

rows = []

def make_row(e, r, t, source):
    return {
        "Event ID": e,
        "Room": r,
        "Timeslot": t,
        "Source": source,
        "Event Size": s.event_size.get(e),
        "Room Capacity": s.room_cap.get(r),
    }

# MILP-assigned events
if solve_status == "optimal":
    var_keys = list(x.keys())
    var_list = list(x.values())
    sol_vals = prob.getSolution(var_list)
    for (e, r, t), val in zip(var_keys, sol_vals):
        if val > 0.5:
            rows.append(make_row(e, r, t, "milp"))

# Fixed vet events
for e, assignments in s.fixed_vet.items():
    for r, t in assignments:
        rows.append(make_row(e, r, t, "fixed_vet"))

# Fixed non-vet events
for e, assignments in s.fixed_non_vet.items():
    for r, t in assignments:
        rows.append(make_row(e, r, t, "fixed_non_vet"))

solution_df = pd.DataFrame(
    rows,
    columns=["Event ID", "Room", "Timeslot", "Source", "Event Size", "Room Capacity"],
)

# =============================================================================
# Enrich with event + room metadata
# =============================================================================
if s.events_raw is not None:
    event_meta_cols = ["Event ID", "Event Name", "Event Type", "Module Code", "Module Name",
                       "Duration (minutes)", "Weeks", "Semester"]
    available = [c for c in event_meta_cols if c in s.events_raw.columns]
    event_meta = s.events_raw[available].drop_duplicates(subset="Event ID")
    solution_df = solution_df.merge(event_meta, on="Event ID", how="left")

if s.rooms_raw is not None:
    room_keep = ["Id", "Room Type", "Building", "Campus"]
    available = [c for c in room_keep if c in s.rooms_raw.columns]
    room_meta = (
        s.rooms_raw[available]
        .rename(columns={"Room Type": "Room Type (detail)", "Id": "Room"})
        .drop_duplicates(subset="Room")
    )
    solution_df = solution_df.merge(room_meta, on="Room", how="left")

if s.pc_raw is not None and "Module Code" in solution_df.columns:
    compulsory_modules = set(s.pc_raw.loc[s.pc_raw["Compulsory"] == True, "ModuleId"])
    solution_df["Core"] = solution_df["Module Code"].isin(compulsory_modules)
else:
    solution_df["Core"] = False

output_cols = [
    "Event ID", "Event Name", "Event Type", "Module Code", "Module Name",
    "Timeslot", "Duration (minutes)", "Weeks", "Event Size", "Semester",
    "Room", "Building", "Campus", "Room Capacity", "Room Type (detail)", "Core",
]

output_cols = [c for c in output_cols if c in solution_df.columns]
solution_df = solution_df[output_cols]

# =============================================================================
# Unassigned events
# =============================================================================
assigned_events = {key[0] for key in x}
unassigned = [e for e in E if e not in assigned_events]
if unassigned:
    print(f"WARNING: {len(unassigned)} unassigned events: {unassigned[:20]}")

# =============================================================================
# Save Output
# =============================================================================
solution_df.to_excel("solution2.xlsx", index=False)
print(f"Solution saved: {len(solution_df):,} rows ({len([r for r in rows if r['Source'] == 'milp']):,} MILP-assigned)")




solution_df['Exceeded Capacity'] = solution_df['Event Size'] - solution_df['Room Capacity']


data = solution_df['Exceeded Capacity'].dropna()

plt.figure()
plt.hist(data, bins=20)
plt.xlabel('Exceeded Capacity')
plt.ylabel('Frequency')
plt.title('Histogram of Exceeded Capacity')
plt.show()

# What to do about the room compatabillity???

# Room compatibillity test 



bad_events = [
    e for e in E
    if not any(room_compatible(e, r) for r in R)
]


type_counts = Counter(
    s.event_room_type.get(e, "UNKNOWN")
    for e in bad_events
)

print(type_counts)

print(f"{len(bad_events)} events have NO compatible rooms")
print(bad_events[:10])



visualiser