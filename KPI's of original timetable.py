#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Mar 29 15:23:07 2026

@author: michael
"""

"""
timetable_kpis.py — Standalone KPI calculator for any timetable Excel file.

Edit the paths at the top and run directly:
    python timetable_kpis.py
"""

from collections import Counter
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# CONFIGURE THESE PATHS
# ---------------------------------------------------------------------------

weeks = [9,10]

TIMETABLE_PATH = Path("Filtered9and10.xlsx")
PROG_COURSE_PATH = Path("Data/Programme-Course.xlsx")  # set to None to skip C2

# ---------------------------------------------------------------------------
# Cost function weights (matches improve_timetable.py)
# ---------------------------------------------------------------------------

HOUR_PENALTY: dict[str, float] = {
    "09:00": 4,
    "10:00": 1,
    "11:00": 1,
    "12:00": 0,
    "13:00": 1,
    "14:00": 1,
    "15:00": 1,
    "16:00": 3,
    "17:00": 5,
}

DAY_PENALTY: dict[str, float] = {
    "Monday":    0,
    "Tuesday":   1,
    "Wednesday": 2,
    "Thursday":  4,
    "Friday":    15,
}


def parse_timeslot(ts: str) -> tuple[str, str]:
    parts = str(ts).strip().split()
    return (parts[0], parts[1]) if len(parts) >= 2 else (ts, "00:00")


def timeslot_cost(ts: str) -> float:
    day, hour = parse_timeslot(ts)
    return DAY_PENALTY.get(day, 0) + HOUR_PENALTY.get(hour, 0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

print(f"\nLoading {TIMETABLE_PATH} ...")
maindf = pd.read_excel(TIMETABLE_PATH)
maindf.columns = [c.strip() for c in df.columns]
print(f"  Rows loaded: {len(df):,}")

# Drop rows with no timeslot or room
maindf = maindf.dropna(subset=["Timeslot", "Room"]).copy()
print(f"  Rows with Timeslot + Room: {len(df):,}")

# One row per event for cost/density metricse

for i in weeks:
    i = 10
    if i == 9:
        df = maindf[maindf['Weeks'].astype(str).str.contains(r'\b9\b')]
    elif i == 10:
        df = maindf[maindf['Weeks'].astype(str).str.contains(r'\b10\b')]
        
    
    event_df = df.drop_duplicates(subset=["Event ID"])
    n = len(event_df)
    
    # ---------------------------------------------------------------------------
    # Density cost
    # ---------------------------------------------------------------------------
    density_cost = sum(timeslot_cost(str(t)) for t in event_df["Timeslot"])
    
    print(f"\n========== TIMETABLE KPIs ==========")
    print(f"  Total events (unique):          {n}")
    print(f"  Density cost:                   {density_cost:.1f}  (avg {density_cost/n:.2f} per event)")
    
    # ---------------------------------------------------------------------------
    # C1: Room double-bookings
    # ---------------------------------------------------------------------------
    room_ts_counts = df.groupby(["Room", "Timeslot"])["Event ID"].nunique()
    c1_clashes = int((room_ts_counts > 1).sum())
    print(f"\n  C1 room clashes:                {c1_clashes}")
    # if c1_clashes > 0:
    #     for (room, ts), count in room_ts_counts[room_ts_counts > 1].items():
    #         print(f"    ({room}, {ts}): {count} events")
    
    # ---------------------------------------------------------------------------
    # C2: Core module clashes
    # ---------------------------------------------------------------------------
    if PROG_COURSE_PATH is not None and Path(PROG_COURSE_PATH).exists():
        pc = pd.read_excel(PROG_COURSE_PATH)
        pc.columns = [c.strip() for c in pc.columns]
        pc["Year"] = pc["CourseId"].str.extract(r"_YR(\d+)_", expand=False)
        pc["ProgCode"] = pc["CourseId"].str.extract(r"^(.+?)_YR", expand=False)
        pc = pc.dropna(subset=["Year", "ProgCode"])
    
        compulsory_modules = set(pc.loc[pc["Compulsory"] == True, "ModuleId"])
    
        # Build {(year, prog): set of core module codes}
        core_modules_YD: dict = {}
        for _, row in pc[pc["Compulsory"] == True].iterrows():
            key = (str(row["Year"]), str(row["ProgCode"]))
            core_modules_YD.setdefault(key, set()).add(row["ModuleId"])
    
        core_df = event_df[event_df["Module Code"].isin(compulsory_modules)].copy()
    
        c2_clashes = 0
        clashing_slots = []
        for (year, prog), mods_set in core_modules_YD.items():
            subset = core_df[core_df["Module Code"].isin(mods_set)]
            ts_mod = subset.groupby("Timeslot")["Module Code"].nunique()
            for ts, count in ts_mod[ts_mod > 1].items():
                events_at_ts = subset[subset["Timeslot"] == ts]
                # Skip if all conflicting events are tutorials — cohort is split across
                # parallel tutorial groups, so no individual student is double-booked.
                if "Event Type" in events_at_ts.columns:
                    all_tutorials = (
                        events_at_ts["Event Type"]
                        .str.strip()
                        .str.lower()
                        .str.contains("tutorial")
                        .all()
                    )
                    if all_tutorials:
                        continue
                c2_clashes += 1
                mods_at_ts = sorted(events_at_ts["Module Code"].unique())
                clashing_slots.append((year, prog, ts, mods_at_ts))
    
        print(f"\n  C2 core module clashes:         {c2_clashes}")
        # if clashing_slots:
        #     print(f"  Clashing slots:")
        #     for year, prog, ts, mods in sorted(clashing_slots):
        #         print(f"    (Year {year}, {prog}, {ts}): {mods}")
    else:
        print(f"\n  C2: Skipped — set PROG_COURSE_PATH to enable")
    
    # ---------------------------------------------------------------------------
    # Timeslot distribution
    # ---------------------------------------------------------------------------
    day_counts: Counter = Counter()
    hour_counts: Counter = Counter()
    for ts in event_df["Timeslot"]:
        day, hour = parse_timeslot(str(ts))
        day_counts[day] += 1
        hour_counts[hour] += 1
    
    print(f"\n  Events by day:")
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        print(f"    {day:<12} {day_counts.get(day, 0):>4}")
    
    print(f"\n  Events by hour:")
    for hour in ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"]:
        print(f"    {hour}   {hour_counts.get(hour, 0):>4}")
    
    # ---------------------------------------------------------------------------
    # Campus distribution
    # ---------------------------------------------------------------------------
    if "Campus" in event_df.columns:
        campus_counts: Counter = Counter(event_df["Campus"].dropna())
        print(f"\n  Events by campus:")
        for campus, count in sorted(campus_counts.items()):
            print(f"    {campus:<20} {count:>4}")
    
    # ---------------------------------------------------------------------------
    # Room capacity vs event size
    # ---------------------------------------------------------------------------
    if "Room Capacity" in event_df.columns and "Event Size" in event_df.columns:
        over_cap = event_df[
            event_df["Event Size"].notna() &
            event_df["Room Capacity"].notna() &
            (event_df["Event Size"] > event_df["Room Capacity"])
        ]
        print(f"\n  Over-capacity events:           {len(over_cap)}")
        # if len(over_cap) > 0:
        #     for _, row in over_cap.iterrows():
        #         print(f"    {row['Event ID']}: size={row['Event Size']} cap={row['Room Capacity']} room={row['Room']}")
    
    # ---------------------------------------------------------------------------
    # Online vs in-person
    # ---------------------------------------------------------------------------
    if "Online Delivery" in event_df.columns:
        online_counts = event_df["Online Delivery"].value_counts()
        print(f"\n  Online delivery breakdown:")
        for val, count in online_counts.items():
            print(f"    {str(val):<20} {count:>4}")
    
    # ---------------------------------------------------------------------------
    # Core vs non-core
    # ---------------------------------------------------------------------------
    if PROG_COURSE_PATH is not None and Path(PROG_COURSE_PATH).exists():
        core_count = int(event_df["Module Code"].isin(compulsory_modules).sum())
        print(f"\n  Core events:                    {core_count}")
        print(f"  Non-core events:                {n - core_count}")
    
    print("=====================================\n")