"""
Build a 2-Week Timetable View for the Vet School
=================================================
Loads vet-filtered data, filters events to a configurable 2-week window,
adds room details and a Core (compulsory) flag, and outputs an Excel file.
"""

import argparse
import pandas as pd
from data_loader import TimetablingData
from visualisation import main as run_eda
from utils import parse_weeks, VET_DATA_DIR


def build_timetable(start_week: int = 9):
    data_dir = VET_DATA_DIR
    data = TimetablingData(data_dir=str(data_dir))
    data.load_all()

    events = data.events
    prog_course = data.prog_course
    rooms = data.rooms

    # --- 1. Parse weeks and filter to the 2-week window ---
    target_weeks = {start_week, start_week + 1}
    events = events.copy()
    events["_parsed_weeks"] = events["Weeks"].apply(parse_weeks)
    events = events[events["_parsed_weeks"].apply(lambda ws: bool(ws & target_weeks))]
    events = events.drop(columns=["_parsed_weeks"])

    print(f"Events in weeks {start_week}-{start_week + 1}: {len(events)}")

    # --- 2. Add Core flag via Programme-Course ---
    compulsory_modules = set(
        prog_course.loc[prog_course["Compulsory"] == True, "ModuleId"]
    )
    events = events.copy()
    events["Core"] = events["Module Code"].isin(compulsory_modules)

    # --- 3. Join room capacity from rooms table ---
    room_info = rooms[["Id", "Capacity", "Room Type"]].rename(
        columns={
            "Id": "Room",
            "Capacity": "Room Capacity",
            "Room Type": "Room Type (detail)",
        }
    )
    events = events.merge(room_info, on="Room", how="left")

    # --- 4. Select output columns ---
    output_cols = [
        "Event ID",
        "Event Name",
        "Event Type",
        "Module Code",
        "Module Name",
        "Timeslot",
        "Duration (minutes)",
        "Weeks",
        "Event Size",
        "Semester",
        "Room",
        "Building",
        "Campus",
        "Room Capacity",
        "Room Type (detail)",
        "Core",
    ]
    # Keep only columns that exist
    output_cols = [c for c in output_cols if c in events.columns]
    result = events[output_cols]

    # --- 5. Save ---
    end_week = start_week + 1
    out_path = data_dir / f"timetable_weeks_{start_week}_{end_week}.xlsx"
    result.to_excel(out_path, index=False)
    print(f"Saved to {out_path}")

    # Run EDA on the timetable
    run_eda(result)

    # Summary
    print(f"\nSummary:")
    print(f"  Events: {len(result)}")
    print(f"  Unique modules: {result['Module Code'].nunique()}")
    print(f"  Core events: {result['Core'].sum()}")
    print(f"  Non-core events: {(~result['Core']).sum()}")
    print(f"  Events with room: {result['Room'].notna().sum()}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build vet school 2-week timetable")
    parser.add_argument(
        "--start-week",
        type=int,
        default=9,
        help="First week of the 2-week window (default: 9)",
    )
    args = parser.parse_args()
    build_timetable(start_week=args.start_week)
