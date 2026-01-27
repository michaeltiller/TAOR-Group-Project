"""
University Timetabling Dataset Loader and Explorer
===================================================
Loads and provides utilities for analyzing the 2024-5 university timetabling data.
"""

import pandas as pd
import os
from pathlib import Path

DATA_DIR = Path("Data")

class TimetablingData:
    """Container for university timetabling dataset."""

    def __init__(self, data_dir: str = "Data"):
        self.data_dir = Path(data_dir)
        self.dpt = None           # Degree Programme Table
        self.events = None        # Event-Module-Room data
        self.enrollments = None   # Student-Programme-Module-Event data
        self.prog_course = None   # Programme-Course mapping
        self.rooms = None         # Rooms and Room Types

    def load_all(self):
        """Load all datasets into memory."""
        print("Loading datasets...")

        print("  - DPT Data (programmes & courses)...")
        self.dpt = pd.read_excel(self.data_dir / "2024-5 DPT Data.xlsx")

        print("  - Event Module Room (scheduling)...")
        self.events = pd.read_excel(self.data_dir / "2024-5 Event Module Room.xlsx")

        print("  - Student Programme Module Event (enrollments)...")
        self.enrollments = pd.read_excel(self.data_dir / "2024-5 Student Programme Module Event.xlsx")

        print("  - Programme-Course mapping...")
        self.prog_course = pd.read_excel(self.data_dir / "Programme-Course.xlsx")

        print("  - Rooms and Room Types...")
        self.rooms = pd.read_excel(self.data_dir / "Rooms and Room Types.xlsx")

        print("All datasets loaded!\n")
        return self

    def summary(self):
        """Print summary statistics for all datasets."""
        print("=" * 60)
        print("DATASET SUMMARY")
        print("=" * 60)

        datasets = [
            ("DPT Data", self.dpt),
            ("Events", self.events),
            ("Enrollments", self.enrollments),
            ("Programme-Course", self.prog_course),
            ("Rooms", self.rooms),
        ]

        for name, df in datasets:
            if df is not None:
                print(f"\n{name}: {df.shape[0]:,} rows, {df.shape[1]} columns")

        if self.events is not None:
            print("\n" + "-" * 40)
            print("EVENT STATISTICS:")
            print(f"  Unique modules: {self.events['Module Code'].nunique():,}")
            print(f"  Unique events: {self.events['Event ID'].nunique():,}")
            print(f"  Event types: {self.events['Event Type'].unique().tolist()}")
            print(f"  Semesters: {self.events['Semester'].unique().tolist()}")

        if self.rooms is not None:
            print("\n" + "-" * 40)
            print("ROOM STATISTICS:")
            print(f"  Total rooms: {self.rooms.shape[0]}")
            print(f"  Campuses: {self.rooms['Campus'].unique().tolist()}")
            print(f"  Capacity range: {self.rooms['Capacity'].min()} - {self.rooms['Capacity'].max()}")
            print(f"  Room types: {self.rooms['Room Type'].unique().tolist()}")

        if self.enrollments is not None:
            print("\n" + "-" * 40)
            print("ENROLLMENT STATISTICS:")
            print(f"  Unique students: {self.enrollments['AnonID'].nunique():,}")
            print(f"  Unique programmes: {self.enrollments['Programme'].nunique():,}")
            print(f"  Unique courses: {self.enrollments['Course ID'].nunique():,}")

    def get_timeslots(self):
        """Extract unique timeslots from events."""
        if self.events is None:
            return None
        return sorted(self.events['Timeslot'].dropna().unique().tolist())

    def get_events_by_module(self, module_code: str):
        """Get all events for a specific module."""
        if self.events is None:
            return None
        return self.events[self.events['Module Code'].str.contains(module_code, na=False)]

    def get_room_by_capacity(self, min_capacity: int, max_capacity: int = None):
        """Get rooms within a capacity range."""
        if self.rooms is None:
            return None
        mask = self.rooms['Capacity'] >= min_capacity
        if max_capacity:
            mask &= self.rooms['Capacity'] <= max_capacity
        return self.rooms[mask]

    def get_student_schedule(self, student_id: str):
        """Get all events for a specific student."""
        if self.enrollments is None or self.events is None:
            return None
        student_events = self.enrollments[self.enrollments['AnonID'] == student_id]['Event ID'].tolist()
        return self.events[self.events['Event ID'].isin(student_events)]


def analyze_conflicts(data: TimetablingData):
    """Analyze potential scheduling conflicts in the dataset."""
    if data.events is None:
        print("Events data not loaded!")
        return

    print("\n" + "=" * 60)
    print("CONFLICT ANALYSIS")
    print("=" * 60)

    # Room double-booking check
    room_time = data.events.groupby(['Room', 'Timeslot', 'Semester']).size()
    double_booked = room_time[room_time > 1]
    print(f"\nPotential room double-bookings: {len(double_booked)}")
    if len(double_booked) > 0:
        print("  (Note: May be valid if different weeks)")

    # Events without rooms
    no_room = data.events[data.events['Room'].isna()]
    print(f"Events without assigned rooms: {len(no_room)}")

    # Online events
    online = data.events[data.events['Online Delivery'].notna()]
    print(f"Online delivery events: {len(online)}")


if __name__ == "__main__":
    # Load and explore the data
    data = TimetablingData()
    data.load_all()
    data.summary()

    # Show timeslot distribution
    print("\n" + "=" * 60)
    print("TIMESLOT DISTRIBUTION")
    print("=" * 60)
    timeslots = data.get_timeslots()
    print(f"Total unique timeslots: {len(timeslots)}")
    print("\nSample timeslots:")
    for ts in timeslots[:10]:
        print(f"  {ts}")

    # Analyze potential conflicts
    analyze_conflicts(data)
