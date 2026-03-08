"""
Compare Original and Proposed Timetables
=========================================
Loads the original vet school timetable and a proposed (MILP)
timetable, enriches the proposed one with missing columns, then generates
the full EDA plot suite for each and saves to separate directories.

Usage:
    python compare_timetables.py                  # default: weeks 9-10
    python compare_timetables.py --start-week 15  # weeks 15-16

Output:
    plots/original/  — 5 PNGs for the original timetable
    plots/proposed/  — 5 PNGs for the proposed timetable
"""

import argparse
import pandas as pd
from pathlib import Path

from utils import VET_DATA_DIR
from eda_visualizations import main as run_eda


def load_original(start_week: int) -> pd.DataFrame:
    end_week = start_week + 1
    path = VET_DATA_DIR / f"timetable_weeks_{start_week}_{end_week}.xlsx"
    if not path.exists():
        raise FileNotFoundError(
            f"Original timetable not found: {path}\n"
            f"Run: python build_timetable.py --start-week {start_week}"
        )
    return pd.read_excel(path)


def load_proposed(start_week: int) -> pd.DataFrame:
    end_week = start_week + 1
    path = Path("proposedTimetable") / f"solution_weeks_{start_week}_{end_week}.xlsx"
    if not path.exists():
        raise FileNotFoundError(f"Proposed timetable not found: {path}")
    return pd.read_excel(path)


def enrich_proposed(proposed: pd.DataFrame, start_week: int) -> pd.DataFrame:
    """Add the columns that the proposed timetable lacks by joining vet data sources."""
    df = proposed.copy()

    # Drop Source column if present (not needed for EDA)
    df = df.drop(columns=["Source"], errors="ignore")

    # --- Join Duration (minutes), Semester, Weeks from vet events ---
    events_path = VET_DATA_DIR / "2024-5 Event Module Room.xlsx"
    events = pd.read_excel(events_path)

    # Identify available join columns
    event_cols = ["Event ID"]
    for col in ["Duration (minutes)", "Semester", "Weeks"]:
        if col in events.columns:
            event_cols.append(col)

    event_lookup = events[event_cols].drop_duplicates(subset=["Event ID"])
    df = df.merge(event_lookup, on="Event ID", how="left")

    # Hardcode Weeks if not available from the events data
    if "Weeks" not in df.columns or df["Weeks"].isna().all():
        end_week = start_week + 1
        df["Weeks"] = f"{start_week}-{end_week}"

    # --- Join Building, Campus, Room Type (detail) from vet rooms ---
    rooms_path = VET_DATA_DIR / "Rooms and Room Types.xlsx"
    rooms = pd.read_excel(rooms_path)

    room_cols = ["Id"]
    col_renames = {"Id": "Room"}
    for src, dst in [
        ("Building", "Building"),
        ("Campus", "Campus"),
        ("Room Type", "Room Type (detail)"),
    ]:
        if src in rooms.columns:
            room_cols.append(src)
            col_renames[src] = dst

    room_lookup = (
        rooms[room_cols].rename(columns=col_renames).drop_duplicates(subset=["Room"])
    )
    df = df.merge(room_lookup, on="Room", how="left")

    # --- Compute Core flag from Programme-Course data ---
    prog_course_path = VET_DATA_DIR / "Programme-Course.xlsx"
    prog_course = pd.read_excel(prog_course_path)

    if "ModuleId" in prog_course.columns and "Compulsory" in prog_course.columns:
        compulsory_modules = set(
            prog_course.loc[prog_course["Compulsory"] == True, "ModuleId"]
        )
        df["Core"] = df["Module Code"].isin(compulsory_modules)
    else:
        df["Core"] = False

    return df


def main():
    parser = argparse.ArgumentParser(
        description="Generate EDA plots for original and proposed timetables"
    )
    parser.add_argument(
        "--start-week",
        type=int,
        default=9,
        help="First week of the 2-week window (default: 9)",
    )
    args = parser.parse_args()
    start_week = args.start_week
    end_week = start_week + 1

    print(f"Comparing timetables for weeks {start_week}–{end_week}\n")

    # --- Original timetable ---
    print(f"Loading original timetable (weeks {start_week}–{end_week})...")
    original_df = load_original(start_week)
    print(f"  {len(original_df)} events loaded.")

    print(f"\nGenerating plots for ORIGINAL timetable -> plots/original/")
    run_eda(original_df, output_dir="plots/original")

    # --- Proposed timetable ---
    print(f"\nLoading proposed timetable (weeks {start_week}–{end_week})...")
    proposed_raw = load_proposed(start_week)
    print(f"  {len(proposed_raw)} events loaded. Columns: {list(proposed_raw.columns)}")

    print("  Enriching proposed timetable with vet data joins...")
    proposed_df = enrich_proposed(proposed_raw, start_week)
    print(f"  Enriched columns: {list(proposed_df.columns)}")

    print(f"\nGenerating plots for PROPOSED timetable -> plots/proposed/")
    run_eda(proposed_df, output_dir="plots/proposed")

    print("\nDone.")
    print("  plots/original/ - EDA plots for original timetable")
    print("  plots/proposed/ -EDA plots for proposed timetable")


if __name__ == "__main__":
    main()
