"""
Build 2 week timetable view
=====================================
Load data, and build two week timetable

"""

from data_loader import TimetablingData
import pandas as pd
from pathlib import Path
import argparse


def parse_weeks(weeks_value) -> set[int]:
    """Parse a weeks cell (int or comma seperated str) into a set"""
    if pd.isna(weeks_value):
        return set()
    s = str(weeks_value).strip()
    weeks = set()

    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            low, high = part.split("-", 1)
            weeks.update(range(int(low), int(high) + 1))
        else:
            weeks.add(int(part))

    return weeks


def build_timetable(
    start_week: int = 9, data_path: str = "Data/vet", save: bool = True
):
    data_dir = Path(str(data_path))
    data = TimetablingData(data_dir=str(data_dir))
    data.load_all()

    events = data.events
    prog_course = data.prog_course
    rooms = data.rooms

    # Parse weeks and filter to 2-week window
    target_weeks = {start_week, start_week + 1}
    events = events.copy()
    events["_parsed_weeks"] = events["Weeks"].apply(parse_weeks)
    events = events[events["_parsed_weeks"].apply(lambda ws: bool(ws & target_weeks))]
    events = events.drop(columns="_parsed_weeks")

    print(f"Events in week {start_week}-{start_week + 1}: {len(events)}")

    # Add core flag
    compulsory_modules = set(
        prog_course.loc[prog_course["Compulsory"] == True, "ModuleId"]
    )
    events["Core"] = events["Module Code"].isin(compulsory_modules)

    # Room info
    room_info = rooms[["Id", "Capacity", "Room Type"]].rename(
        columns={
            "Id": "Room",
            "Capacity": "Room_Capacity",
            "Room Type": "Room Type (detail)",
        }
    )
    events = events.merge(room_info, on="Room", how="left")

    # Output columns
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

    result = events[[c for c in output_cols if c in events.columns]]
    print("Dropped:", set(output_cols) - set(events.columns))

    # Save data
    if save:
        out_path = (
            Path(data_dir) / f"timetable_weeks_{start_week}_{start_week + 1}.xlsx"
        )
        result.to_excel(out_path, index=False)
        print(f"Saved to {out_path}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build 2 week timetable")

    parser.add_argument(
        "--start_week",
        type=int,
        default=9,
        help="Week to start from (default: 9)",
    )
    parser.add_argument(
        "--data_path",
        type=str,
        default="Data/vet",
        help="Data path to pull from",
    )
    parser.add_argument(
        "--save",
        type=bool,
        default=True,
        help="Save output (bool)",
    )

    args = parser.parse_args()
    build_timetable(
        start_week=args.start_week, data_path=args.data_path, save=args.save
    )
