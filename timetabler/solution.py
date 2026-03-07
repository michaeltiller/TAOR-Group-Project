"""solution.py — Solution extraction for the vet school timetabler."""

from pathlib import Path

import pandas as pd

from .data_prep import TimetablerSets
from .model_builder import ModelBuilder


class SolutionExtractor:
    """Extracts and formats the MILP solution into DataFrames."""

    def __init__(self, sets: TimetablerSets, builder: ModelBuilder, solve_status: str):
        self._sets = sets
        self._builder = builder
        self._solve_status = solve_status

    def _make_row(self, e, r, t, source: str) -> dict:
        s = self._sets
        return {
            "Event ID": e,
            "Room": r,
            "Timeslot": t,
            "Source": source,
            "Event Size": s.event_size.get(e),
            "Room Capacity": s.room_cap.get(r),
        }

    def get_solution(self) -> pd.DataFrame:
        """Return DataFrame of all assigned events (MILP-assigned + fixed)."""
        s = self._sets
        b = self._builder
        rows = []

        if self._solve_status == "optimal" and b._var_list:
            sol = b.model.getSolution(b._var_list)
            for (e, r, t), val in zip(b._var_keys, sol):
                if val > 0.5:
                    rows.append(self._make_row(e, r, t, "milp"))

        for e, assignments in s.fixed_vet.items():
            for r, t in assignments:
                rows.append(self._make_row(e, r, t, "fixed_vet"))

        for e, assignments in s.fixed_non_vet.items():
            for r, t in assignments:
                rows.append(self._make_row(e, r, t, "fixed_non_vet"))

        return pd.DataFrame(
            rows,
            columns=["Event ID", "Room", "Timeslot", "Source", "Event Size", "Room Capacity"],
        )

    def dump_timetable(self, path: Path | None = None) -> pd.DataFrame:
        """Return a rich timetable DataFrame with event and room metadata joined.

        Optionally saves to Excel if path is provided.
        """
        s = self._sets
        df = self.get_solution()

        event_meta_cols = [
            "Event ID", "Event Name", "Event Type", "Module Code", "Module Name",
            "Duration (minutes)", "Weeks", "Semester",
        ]
        if s.events_raw is not None:
            available_event_cols = [c for c in event_meta_cols if c in s.events_raw.columns]
            event_meta = (
                s.events_raw[available_event_cols]
                .drop_duplicates(subset="Event ID")
            )
            df = df.merge(event_meta, on="Event ID", how="left")

        room_keep = ["Id", "Room Type", "Building", "Campus"]
        if s.rooms_raw is not None:
            available_room_cols = [c for c in room_keep if c in s.rooms_raw.columns]
            room_meta = (
                s.rooms_raw[available_room_cols]
                .rename(columns={"Room Type": "Room Type (detail)", "Id": "Room"})
                .drop_duplicates(subset="Room")
            )
            df = df.merge(room_meta, on="Room", how="left")

        if s.pc_raw is not None and "Module Code" in df.columns:
            compulsory_modules = set(
                s.pc_raw.loc[s.pc_raw["Compulsory"] == True, "ModuleId"]
            )
            df["Core"] = df["Module Code"].isin(compulsory_modules)
        else:
            df["Core"] = False

        output_cols = [
            "Event ID", "Event Name", "Event Type", "Module Code", "Module Name",
            "Timeslot", "Duration (minutes)", "Weeks", "Event Size", "Semester",
            "Room", "Building", "Campus", "Room Capacity", "Room Type (detail)", "Core",
        ]
        output_cols = [c for c in output_cols if c in df.columns]
        df = df[output_cols]

        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_excel(path, index=False)
            print(f"Timetable saved to {path}")

        return df

    def get_unassigned_events(self) -> list:
        """Return event IDs in E that have no feasible (Room, Timeslot) variable."""
        assigned_events = {key[0] for key in self._builder.x}
        return [e for e in self._sets.E if e not in assigned_events]
