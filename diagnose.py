#!/usr/bin/env python3
"""
Diagnostic script for multi-school MILP infeasibility.

Read-only analysis — no solver needed. Reports:
1. Central campus capacity (rooms × timeslots) vs demand (event-hours per school)
2. Timeslot format mismatch between raw data and model's generated T set
3. Per-school room-type compatibility check (zero-compatible-room events)
4. Progressive lock simulation using existing timetable assignments
"""

import math
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd

from data_loader import TimetablingData
from filter_school import SchoolCampusDict
from timetabler.data_prep import build_sets_from_frames
from utils import parse_weeks

# ── Config ──────────────────────────────────────────────────────────────────
TARGET_WEEKS = {9, 10}

# ── Load data ───────────────────────────────────────────────────────────────
print("Loading data...")
td = TimetablingData(data_dir="Data").load_all()

central_rooms = td.rooms[
    td.rooms["Campus"].astype(str).str.contains("Central", case=False, na=False)
].copy()

CENTRAL_SCHOOLS = [
    name for name, campuses in SchoolCampusDict.items()
    if "Central" in campuses
]

# ── Model's timeslot set (must match multi_school_timetabler.py / data_prep.py) ──
MODEL_T = []
for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
    current = datetime.strptime("08:30", "%H:%M")
    end = datetime.strptime("18:00", "%H:%M")
    while current <= end:
        MODEL_T.append(f"{day} {current.strftime('%H:%M')}")
        current += timedelta(hours=1)

MODEL_T_SET = set(MODEL_T)

# ── Room compatibility (copied from multi_school_timetabler.py) ─────────────
def room_compatible(event_type, room_type, room_lock):
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
        if e_type == "general teaching" and r_type in ["general teaching", "lecture theatre", "seminar room"]:
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


# ============================================================================
# 1. CAPACITY ANALYSIS
# ============================================================================
print("\n" + "=" * 70)
print("1. CAPACITY ANALYSIS — Central Campus")
print("=" * 70)

room_types = central_rooms.groupby("Specialist room type").agg(
    count=("Id", "count"),
    total_cap=("Capacity", "sum"),
    min_cap=("Capacity", "min"),
    max_cap=("Capacity", "max"),
).sort_values("count", ascending=False)

print(f"\nCentral campus rooms: {len(central_rooms)}")
print(f"Model timeslots: {len(MODEL_T)} ({len(MODEL_T)//5} per day × 5 days)")
print(f"Total room-slots available: {len(central_rooms) * len(MODEL_T):,}")
print(f"\nRooms by type:")
print(room_types.to_string())

# Per-school demand
print(f"\n{'─' * 70}")
print("Per-school demand (free events in weeks {})".format(sorted(TARGET_WEEKS)))
print(f"{'─' * 70}")
print(f"{'School':<55} {'Events':>7} {'Slot-hrs':>9}")

total_demand = 0
school_demands = {}
for school in CENTRAL_SCHOOLS:
    school_events = td.events[td.events["Module Department"] == school].copy()
    if school_events.empty:
        print(f"{school:<55} {'0':>7} {'0':>9}")
        continue

    school_events["_pw"] = school_events["Weeks"].apply(parse_weeks)
    week_filtered = school_events[
        school_events["_pw"].apply(lambda ws: bool(ws & TARGET_WEEKS))
    ]

    # Count free events (Room Lock != Yes)
    free = week_filtered[
        week_filtered["Room Lock"].fillna("").str.strip().str.upper() != "YES"
    ]
    unique_events = free.drop_duplicates(subset="Event ID")
    n_events = len(unique_events)
    slot_hours = sum(
        max(1, math.ceil(d / 60))
        for d in unique_events["Duration (minutes)"].fillna(60)
    )
    total_demand += slot_hours
    school_demands[school] = {"events": n_events, "slot_hours": slot_hours}
    print(f"{school:<55} {n_events:>7} {slot_hours:>9}")

supply = len(central_rooms) * len(MODEL_T)
print(f"\n{'TOTAL DEMAND':<55} {sum(d['events'] for d in school_demands.values()):>7} {total_demand:>9}")
print(f"{'TOTAL SUPPLY (rooms × timeslots)':<55} {'':>7} {supply:>9}")
print(f"{'UTILISATION':<55} {'':>7} {total_demand/supply*100:>8.1f}%")


# ============================================================================
# 2. TIMESLOT FORMAT CHECK
# ============================================================================
print("\n" + "=" * 70)
print("2. TIMESLOT FORMAT CHECK")
print("=" * 70)

raw_timeslots = td.events["Timeslot"].dropna().unique()
raw_ts_set = set(str(t) for t in raw_timeslots)

in_model = raw_ts_set & MODEL_T_SET
not_in_model = raw_ts_set - MODEL_T_SET

print(f"Raw data unique timeslots: {len(raw_ts_set)}")
print(f"Model T set size: {len(MODEL_T_SET)}")
print(f"Raw timeslots IN model T: {len(in_model)}")
print(f"Raw timeslots NOT in model T: {len(not_in_model)}")

if not_in_model:
    # Count how many events use mismatched timeslots
    central_events = td.events[td.events["Module Department"].isin(CENTRAL_SCHOOLS)]
    locked_central = central_events[
        central_events["Room Lock"].fillna("").str.strip().str.upper() == "YES"
    ]
    mismatched_locked = locked_central[
        locked_central["Timeslot"].astype(str).isin(not_in_model)
    ]
    print(f"\nRoom-Locked events with mismatched timeslots: {len(mismatched_locked)}")

    # Show sample mismatches
    sample = sorted(not_in_model)[:20]
    print(f"\nSample mismatched timeslots (first 20):")
    for ts in sample:
        print(f"  '{ts}'")

    # Check if they're just different hours (e.g., 09:00 vs 09:30)
    model_hours = {t.split()[-1] for t in MODEL_T_SET}
    raw_hours = {str(t).split()[-1] for t in not_in_model if len(str(t).split()) >= 2}
    hour_mismatch = raw_hours - model_hours
    if hour_mismatch:
        print(f"\nHour values in raw data but NOT in model: {sorted(hour_mismatch)}")
else:
    print("\nNo format mismatch — all raw timeslots match model T set.")


# ============================================================================
# 3. PER-SCHOOL ROOM-TYPE COMPATIBILITY (isolation — no prior locks)
# ============================================================================
print("\n" + "=" * 70)
print("3. PER-SCHOOL ROOM-TYPE COMPATIBILITY (no accumulated locks)")
print("=" * 70)

room_type_dict = central_rooms.dropna(subset=["Id"]).set_index("Id")["Specialist room type"].to_dict()
room_ids = central_rooms["Id"].dropna().unique().tolist()

def filter_pc_for_school(pc_df, dpt_df, school):
    school_dpt = dpt_df[dpt_df["Programme School Name"] == school]
    prog_codes = set(school_dpt["Programme Code"].unique())
    prog_code_col = pc_df["CourseId"].str.extract(r"^(.+?)_YR")[0]
    return pc_df[prog_code_col.isin(prog_codes)].copy()


for school in CENTRAL_SCHOOLS:
    school_events = td.events[td.events["Module Department"] == school].copy()
    if school_events.empty:
        continue

    school_pc = filter_pc_for_school(td.prog_course, td.dpt, school)

    s = build_sets_from_frames(
        school_events, central_rooms, school_pc,
        TARGET_WEEKS,
        extra_locked_occupancy={},  # no locks
    )

    if not s.E:
        continue

    # Check room compatibility per event
    zero_compat = []
    compat_counts = []
    for e in s.E:
        event_type = s.event_room_type.get(e)
        lock = s.event_room_lock.get(e)
        n_compat = sum(
            1 for r in room_ids
            if room_compatible(event_type, room_type_dict.get(r), lock)
        )
        compat_counts.append(n_compat)
        if n_compat == 0:
            zero_compat.append((e, event_type, lock))

    print(f"\n{school}")
    print(f"  Free events: {len(s.E)}")
    print(f"  Avg compatible rooms/event: {sum(compat_counts)/len(compat_counts):.1f}")
    print(f"  Min compatible rooms: {min(compat_counts)}")
    print(f"  Events with ZERO compatible rooms: {len(zero_compat)}")

    if zero_compat:
        # Group by room type
        type_counts = Counter(et for _, et, _ in zero_compat)
        print(f"  Breakdown by requested room type:")
        for rt, cnt in type_counts.most_common():
            print(f"    '{rt}': {cnt} events")
        # Show first few
        for e, et, lock in zero_compat[:5]:
            print(f"    Event {e}: needs '{et}', room_lock='{lock}'")


# ============================================================================
# 4. EXISTING TIMETABLE SLOT USAGE
# ============================================================================
print("\n" + "=" * 70)
print("4. EXISTING TIMETABLE — Slot usage by school")
print("=" * 70)

# Look at Room Lock=Yes events to see how the real timetable uses rooms
central_events = td.events[td.events["Module Department"].isin(CENTRAL_SCHOOLS)].copy()
central_events["_pw"] = central_events["Weeks"].apply(parse_weeks)
central_week = central_events[
    central_events["_pw"].apply(lambda ws: bool(ws & TARGET_WEEKS))
]

locked = central_week[
    central_week["Room Lock"].fillna("").str.strip().str.upper() == "YES"
]

# Count (room, timeslot) usage by school
print(f"\nRoom-locked events in central campus (weeks {sorted(TARGET_WEEKS)}):")
print(f"{'School':<55} {'Locked':>7} {'(r,t) pairs':>12}")

cumulative_locked = set()
for school in CENTRAL_SCHOOLS:
    school_locked = locked[locked["Module Department"] == school]
    pairs = set()
    for _, row in school_locked.iterrows():
        if pd.notna(row.get("Room")) and pd.notna(row.get("Timeslot")):
            r = str(row["Room"])
            t = str(row["Timeslot"])
            if r in set(room_ids):
                pairs.add((r, t))
    cumulative_locked |= pairs
    print(f"{school:<55} {len(school_locked):>7} {len(pairs):>12}")

print(f"\n{'TOTAL unique locked (room, timeslot) pairs':<55} {'':>7} {len(cumulative_locked):>12}")
print(f"{'As % of supply':<55} {'':>7} {len(cumulative_locked)/supply*100:>11.1f}%")

# Room type breakdown of locked rooms
locked_room_types = Counter()
for r, t in cumulative_locked:
    rt = room_type_dict.get(r, "unknown")
    locked_room_types[rt] += 1

print(f"\nLocked slots by room type:")
for rt, cnt in locked_room_types.most_common():
    total_of_type = sum(1 for rid in room_ids if room_type_dict.get(rid) == rt) * len(MODEL_T)
    pct = cnt / total_of_type * 100 if total_of_type > 0 else 0
    print(f"  {rt}: {cnt} locked / {total_of_type} available ({pct:.1f}%)")

print("\n" + "=" * 70)
print("DIAGNOSIS COMPLETE")
print("=" * 70)
