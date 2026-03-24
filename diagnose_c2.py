#!/usr/bin/env python3
"""
Diagnose C2 (core-class conflict) feasibility WITHOUT the solver.

Checks: for each school + year, how many distinct core modules exist,
and how many timeslots their events would consume (accounting for multi-hour events).

The C2 constraint says: at most 1 core module per (year, timeslot).
If a school has more distinct core modules than available timeslots, C2 is infeasible.
With multi-hour events, each event "blocks" n_slots timeslots for that year.
"""

import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import pandas as pd

from data_loader import TimetablingData
from filter_school import SchoolCampusDict
from timetabler.data_prep import build_sets_from_frames
from utils import parse_weeks

TARGET_WEEKS = {9, 10}

td = TimetablingData(data_dir="Data").load_all()

central_rooms = td.rooms[
    td.rooms["Campus"].astype(str).str.contains("Central", case=False, na=False)
].copy()

CENTRAL_SCHOOLS = [
    name for name, campuses in SchoolCampusDict.items()
    if "Central" in campuses
]

# Model T set
MODEL_T = []
for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
    current = datetime.strptime("08:30", "%H:%M")
    end = datetime.strptime("18:00", "%H:%M")
    while current <= end:
        MODEL_T.append(f"{day} {current.strftime('%H:%M')}")
        current += timedelta(hours=1)

N_TIMESLOTS = len(MODEL_T)  # 50

def filter_pc_for_school(pc_df, dpt_df, school):
    school_dpt = dpt_df[dpt_df["Programme School Name"] == school]
    prog_codes = set(school_dpt["Programme Code"].unique())
    prog_code_col = pc_df["CourseId"].str.extract(r"^(.+?)_YR")[0]
    return pc_df[prog_code_col.isin(prog_codes)].copy()


print("=" * 80)
print("C2 FEASIBILITY ANALYSIS — Core module constraints")
print(f"Available timeslots: {N_TIMESLOTS}")
print("=" * 80)

# Track cross-school year-module overlap
all_year_modules = defaultdict(set)  # year -> set of (school, module) pairs

for school in CENTRAL_SCHOOLS:
    school_events = td.events[td.events["Module Department"] == school].copy()
    if school_events.empty:
        continue

    school_pc = filter_pc_for_school(td.prog_course, td.dpt, school)

    s = build_sets_from_frames(
        school_events, central_rooms, school_pc,
        TARGET_WEEKS,
        extra_locked_occupancy={},
    )

    if not s.E:
        continue

    print(f"\n{'─' * 80}")
    print(f"{school}")
    print(f"  Free events: {len(s.E)}")

    if not s.core_modules_YD:
        print(f"  No core modules.")
        continue

    for (year, prog), modules in sorted(s.core_modules_YD.items()):
        # Track for cross-school analysis
        for m in modules:
            all_year_modules[year].add((school, m))

        # Get events for each module
        module_events = defaultdict(list)
        for e in s.E:
            mod = s.event_module.get(e)
            if mod in modules:
                module_events[mod].append(e)

        # For each module, compute min timeslots needed
        # (each event needs n_slots consecutive hours, and Z[m,t]=1 for all occupied slots)
        # A module with k events of n_i slots each needs: sum(n_i) timeslots
        # (if events can share timeslots within the same module)
        # Actually Z[m,t] is binary — it's 1 if ANY event of module m is at slot t
        # So multiple events of the same module at the same slot only count once
        # But the constraint is just sum_m Z[m,t] <= 1 per year
        # The binding constraint is: how many DISTINCT modules need timeslots?

        n_modules = len(modules)
        n_modules_with_events = len(module_events)

        # Each module that has events will occupy at least 1 timeslot
        # Multi-hour events of a module occupy multiple timeslots
        # The worst case: modules can't share timeslots (C2 prevents this)
        # So min timeslots needed = number of distinct modules with events

        # But: each module might have events at multiple timeslots
        # Since events are assigned by the MILP, a module's events spread across timeslots
        # Each timeslot where a module has an event, that slot is "blocked" for other modules (for that year)

        # Minimum slots consumed per module = at least n_slots for each event
        # But events of the same module CAN share timeslots (overlap)
        # Conservative: min slots = at least 1 per module (if all events fit in 1 timeslot)
        # Upper bound: sum of n_slots for all events (if no overlap)

        min_slots_needed = n_modules_with_events  # best case: 1 slot per module

        # More realistic: each event needs n_slots, and different events of same module
        # may or may not overlap. Count total event-slots per module.
        total_module_slots = 0
        module_detail = []
        for m in modules:
            evs = module_events.get(m, [])
            if not evs:
                continue
            event_slot_hours = sum(max(1, math.ceil(s.event_duration.get(e, 60) / 60)) for e in evs)
            # Each event occupies n_slots timeslots. Events of the same module at the same
            # room/timeslot could overlap, but the MILP assigns each event to different slots.
            # The module's Z[m,t]=1 for each slot where it has any event.
            # In the worst case (no overlap), the module occupies event_slot_hours timeslots.
            # In the best case (max overlap), it occupies max(n_slots) timeslots.
            max_single = max(max(1, math.ceil(s.event_duration.get(e, 60) / 60)) for e in evs)
            module_detail.append((m, len(evs), event_slot_hours, max_single))
            total_module_slots += event_slot_hours

        # The critical check: can we fit all modules' events without exceeding C2?
        # In the BEST case, events of the same module overlap maximally.
        # Each module takes min 1 timeslot. We need n_modules_with_events timeslots.
        # In the WORST case (no overlap within module, all events spread):
        # Module m takes event_slot_hours[m] timeslots, all of which are exclusive to year.

        status = "OK" if min_slots_needed <= N_TIMESLOTS else "INFEASIBLE (min)"
        if total_module_slots > N_TIMESLOTS:
            worst_status = "TIGHT"
        else:
            worst_status = "OK"

        print(f"\n  Year {year}: {n_modules} core modules ({n_modules_with_events} with free events)")
        print(f"    Min timeslots needed (best case): {min_slots_needed} / {N_TIMESLOTS} → {status}")
        print(f"    Max timeslots needed (worst case): {total_module_slots} / {N_TIMESLOTS} → {worst_status}")

        if min_slots_needed > N_TIMESLOTS * 0.8 or total_module_slots > N_TIMESLOTS:
            print(f"    ⚠ WARNING: This year is very tight!")
            print(f"    Module breakdown (top 10 by event-slot-hours):")
            module_detail.sort(key=lambda x: -x[2])
            for m, n_evs, slot_hrs, max_single in module_detail[:10]:
                print(f"      {m}: {n_evs} events, {slot_hrs} slot-hrs, longest={max_single}h")

# ── Cross-school year analysis ──
print(f"\n{'=' * 80}")
print("CROSS-SCHOOL YEAR ANALYSIS")
print("(Years where multiple schools have core modules — potential C2 conflicts)")
print(f"{'=' * 80}")

for year in sorted(all_year_modules.keys()):
    schools_in_year = set(s for s, m in all_year_modules[year])
    modules_in_year = set(m for s, m in all_year_modules[year])
    if len(schools_in_year) > 1:
        print(f"\n  Year {year}: {len(schools_in_year)} schools, {len(modules_in_year)} total core modules")
        print(f"    Schools: {', '.join(sorted(schools_in_year))}")
        print(f"    Combined modules need ≥{len(modules_in_year)} timeslots (have {N_TIMESLOTS})")
        if len(modules_in_year) > N_TIMESLOTS:
            print(f"    *** COMBINED INFEASIBILITY if C2 were enforced cross-school ***")
    else:
        school = list(schools_in_year)[0]
        print(f"\n  Year {year}: only {school} ({len(modules_in_year)} modules)")

# ── Per-school cumulative C2 analysis ──
print(f"\n{'=' * 80}")
print("CUMULATIVE C2 ANALYSIS (simulating sequential school solve)")
print("Assumes each school's core modules consume timeslots exclusively")
print(f"{'=' * 80}")

# This simulates the cross-school constraint that SHOULD exist but doesn't
# If it were enforced, how many timeslots would be consumed per year?
year_slots_used = defaultdict(int)  # year -> count of modules consuming slots

for school in CENTRAL_SCHOOLS:
    school_events = td.events[td.events["Module Department"] == school].copy()
    if school_events.empty:
        continue

    school_pc = filter_pc_for_school(td.prog_course, td.dpt, school)
    s = build_sets_from_frames(
        school_events, central_rooms, school_pc,
        TARGET_WEEKS,
        extra_locked_occupancy={},
    )

    if not s.E or not s.core_modules_YD:
        continue

    for (year, prog), modules in s.core_modules_YD.items():
        # Count modules that actually have free events
        n_with_events = sum(
            1 for m in modules
            if any(s.event_module.get(e) == m for e in s.E)
        )
        year_slots_used[year] += n_with_events

print(f"\nTotal distinct core modules per year across ALL central campus schools:")
for year in sorted(year_slots_used.keys()):
    used = year_slots_used[year]
    status = "OK" if used <= N_TIMESLOTS else "INFEASIBLE"
    pct = used / N_TIMESLOTS * 100
    print(f"  Year {year}: {used} modules need {used} slots / {N_TIMESLOTS} available ({pct:.0f}%) → {status}")
