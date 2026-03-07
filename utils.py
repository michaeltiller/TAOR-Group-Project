"""
Shared Utilities for the Vet School Timetabling Pipeline
=========================================================
Common constants and helper functions used across data_loader.py,
filter_vet.py, build_timetable.py, and eda_visualizations.py.
"""

from pathlib import Path
import pandas as pd
import numpy as np

# --- School identifiers ---
VET_SCHOOL = "Royal (Dick) School of Veterinary Studies"
VET_BUILDING = "Vet School"

# --- Directory paths ---
DATA_DIR = Path("Data")
VET_DATA_DIR = DATA_DIR / "vet"
PLOTS_DIR = Path("plots")

# --- Scheduling constants ---
DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
HOUR_ORDER = ['08:00', '09:00', '10:00', '11:00', '12:00', '13:00', '14:00',
              '15:00', '16:00', '17:00', '18:00', '19:00', '20:00', '21:00']


def parse_weeks(weeks_value) -> set:
    """Parse a Weeks cell (int or comma-separated string) into a set of week numbers.

    Handles NaN, plain integers, comma-separated lists, and ranges like '9-15'.
    Example: parse_weeks('9,11-13,15') -> {9, 11, 12, 13, 15}
    """
    if pd.isna(weeks_value):
        return set()
    s = str(weeks_value).strip()
    weeks = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            weeks.update(range(int(lo), int(hi) + 1))
        else:
            weeks.add(int(part))
    return weeks


def parse_timeslot(timeslot):
    """Parse a timeslot string into a (day, hour) tuple.

    Example: parse_timeslot('Monday 10:00') -> ('Monday', '10:00')
    Returns (None, None) for missing or malformed values.
    """
    if pd.isna(timeslot):
        return None, None
    parts = str(timeslot).split(' ')
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None, None


def timeslot_sort_key(ts):
    """Return a (day_index, hour_str) sort key for a timeslot string.

    Uses DAY_ORDER for day ranking. Unknown days sort last (index 99).
    Example: timeslot_sort_key('Wednesday 09:00') -> (2, '09:00')
    """
    day, hour = parse_timeslot(ts)
    day_idx = DAY_ORDER.index(day) if day is not None and day in DAY_ORDER else 99
    return (day_idx, hour or "")


def get_fill_ratio_color(fill_ratio) -> str:
    """Return a hex colour string for a room fill ratio value.

    >1.0  -> red    (#e74c3c) — overfilled
    >=0.5 -> green  (#2ecc71) — well-used
    <0.5  -> blue   (#3498db) — underfilled
    NaN   -> grey   (#999999) — no capacity data
    """
    if pd.isna(fill_ratio):
        return '#999999'
    if fill_ratio > 1.0:
        return '#e74c3c'
    if fill_ratio >= 0.5:
        return '#2ecc71'
    return '#3498db'


def calculate_fill_ratio(event_size, room_capacity) -> float:
    """Return event_size / room_capacity, or NaN for missing or zero capacity."""
    if pd.isna(event_size) or pd.isna(room_capacity) or room_capacity == 0:
        return np.nan
    return event_size / room_capacity
