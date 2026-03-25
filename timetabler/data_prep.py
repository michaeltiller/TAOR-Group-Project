"""data_prep.py — Set construction for the vet school timetabling MILP.

Exposes:
  TimetablerSets  — dataclass holding all problem-data sets
  build_sets      — load from Excel via TimetablingData, delegate to build_sets_from_frames
  build_sets_from_frames — pure logic, no file I/O (used directly in unit tests)
"""

from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd

from utils import parse_weeks, timeslot_sort_key


@dataclass
class TimetablerSets:
    E: list                     # free event IDs entering the MILP
    R: list                     # vet room IDs
    T: list                     # sorted timeslot strings
    K_YD: dict                  # {(year_str, prog_code): set(event_ids)}
    room_cap: dict              # {room_id: capacity}
    room_type: dict             # {room_id: room_type}
    fixed_vet: dict             # {event_id: [(room, ts), ...]}
    fixed_non_vet: dict         # {event_id: [(room, ts), ...]}
    locked_occupancy: dict      # {(r, t): count}
    locked_core_classes: dict   # {(year, prog_code, t): set(module_codes)}
    event_module: dict          # {event_id: module_code}
    event_size: dict            # {event_id: size}
    event_duration: dict        # {event_id: duration_minutes}
    event_room_type: dict       # Type of room required
    event_room_lock: dict
    core_modules_YD: dict       # {(year, prog_code): list of core module codes}
    events: pd.DataFrame        # week-filtered events
    events_raw: pd.DataFrame    # for metadata joins in SolutionExtractor
    rooms_raw: pd.DataFrame
    pc_raw: pd.DataFrame
    warnings: list = field(default_factory=list)


def build_sets(data_dir: Path, target_weeks: set) -> TimetablerSets:
    """Load from Excel via TimetablingData, delegate to build_sets_from_frames."""
    from data_loader import TimetablingData

    td = TimetablingData(data_dir=str(data_dir)).load_scheduling()
    assert td.events is not None, "Events failed to load"
    print(f"  Raw event rows:   {len(td.events):,}")
    return build_sets_from_frames(td.events, td.rooms, td.prog_course, target_weeks)


def build_sets_from_frames(
    events_raw: pd.DataFrame,
    rooms_raw: pd.DataFrame,
    pc_raw: pd.DataFrame,
    target_weeks: set,
) -> TimetablerSets:
    """Pure logic, no file I/O — used directly in unit tests."""
    warnings: list = []

    # --- R: vet room IDs and capacities ---
    R = rooms_raw["Id"].dropna().unique().tolist()
    room_cap = (
        rooms_raw.dropna(subset=["Id"]).set_index("Id")["Capacity"].to_dict()
    )
    
    room_type = (rooms_raw.dropna(subset=["Id"]).set_index("Id")["Specialist room type"].to_dict()) 
    
    vet_room_set = set(R)

    # --- Week-filter events ---
    events = events_raw.copy()
    events["_pw"] = events["Weeks"].apply(parse_weeks)
    events = events[
        events["_pw"].apply(lambda ws: bool(ws & target_weeks))
    ].copy()
    events = events.drop(columns=["_pw"])
    assert isinstance(events, pd.DataFrame)
    print(
        f"Events in target weeks {sorted(target_weeks)}: {len(events):,} rows"
    )

    # Cache event sizes (one entry per Event ID)
    event_size: dict = {}
    if "Event Size" in events.columns:
        size_series = (
            events[["Event ID", "Event Size"]]
            .drop_duplicates(subset="Event ID")
            .set_index("Event ID")["Event Size"]
        )
        event_size = size_series.to_dict()

    event_duration: dict = {}
    if "Duration (minutes)" in events.columns:
        dur_series = (
            events[["Event ID", "Duration (minutes)"]]
            .drop_duplicates(subset="Event ID")
            .set_index("Event ID")["Duration (minutes)"]
        )
        event_duration = dur_series.to_dict()
        
    
    event_room_type =  (
        events[["Event ID", "Room type 2"]]
        .drop_duplicates(subset="Event ID")
        .set_index("Event ID")["Room type 2"]
    ).to_dict()
    
    event_room_lock = (
        events[["Event ID", "Room Lock"]]
        .drop_duplicates(subset="Event ID")
        .set_index("Event ID")["Room Lock"]
    ).to_dict()

    # --- T: Fixed 1-hour timeslot grid (on-the-hour, matching real data) ---
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    start_time = "09:00"
    end_time = "17:00"

    T = []

    for day in days:
        current = datetime.strptime(start_time, "%H:%M")
        end = datetime.strptime(end_time, "%H:%M")

        while current <= end:
            T.append(f"{day} {current.strftime('%H:%M')}")
            current += timedelta(hours=1)

    T_set = set(T)

    # Build snap map: raw timeslot string → nearest model timeslot (round down)
    # E.g., "Monday 09:30" → "Monday 09:00"
    def snap_to_model_slot(ts_str):
        """Snap a raw timeslot to the containing model 1-hour slot."""
        if ts_str in T_set:
            return ts_str
        parts = str(ts_str).split()
        if len(parts) < 2:
            return None
        day, time_str = parts[0], parts[1]
        try:
            t = datetime.strptime(time_str, "%H:%M")
            # Round down to nearest hour
            snapped = t.replace(minute=0)
            candidate = f"{day} {snapped.strftime('%H:%M')}"
            if candidate in T_set:
                return candidate
        except ValueError:
            pass
        return None
    

    # --- K_YD: compulsory events grouped by (year, programme) ---
    comp = pc_raw[pc_raw["Compulsory"]].copy()
    comp["Year"] = comp["CourseId"].str.extract(r"_YR(\d+)_", expand=False)
    comp["ProgCode"] = comp["CourseId"].str.extract(r"^(.+?)_YR", expand=False)
    comp = comp.dropna(subset=["Year", "ProgCode"])
    mod_year_prog = comp[["ModuleId", "Year", "ProgCode"]].drop_duplicates()
    merged = events.merge(
        mod_year_prog, left_on="Module Code", right_on="ModuleId", how="inner"
    )
    K_YD = merged.groupby(["Year", "ProgCode"])["Event ID"].apply(set).to_dict()

    # --- event_module: event_id → module_code ---
    event_module = (
        events[["Event ID", "Module Code"]]
        .drop_duplicates(subset="Event ID")
        .set_index("Event ID")["Module Code"]
        .to_dict()
    )

    # --- core_modules_YD: (year, prog) → sorted list of core module codes ---
    core_modules_YD: dict = {}
    for (year, prog), event_set in K_YD.items():
        mods = {event_module[e] for e in event_set if e in event_module}
        core_modules_YD[(year, prog)] = sorted(mods)

    # --- Event classification ---
    E: list = []
    fixed_vet: dict = {}
    fixed_non_vet: dict = {}
    locked_occupancy: dict = {}
    locked_core_classes: dict = {}

    for event_id, group in events.groupby("Event ID", sort=False):
        if "Room Lock" in group.columns:
            locked_mask = (
                group["Room Lock"].fillna("").str.strip().str.upper() == "YES"
            )
            locked_rows = group[locked_mask]
        else:
            locked_rows = pd.DataFrame()

        if locked_rows.empty:
            E.append(event_id)
            continue

        vet_pairs = []
        non_vet_pairs = []

        for _, row in locked_rows.iterrows():
            room = row["Room"]
            ts = row["Timeslot"]
            if pd.isna(room) or pd.isna(ts):
                continue

            # Snap raw timeslot to model grid for locking
            ts_str = str(ts)
            model_ts = snap_to_model_slot(ts_str)

            if room in vet_room_set:
                vet_pairs.append((room, ts_str))
                # Lock using the model-aligned timeslot
                if model_ts:
                    locked_occupancy[(room, model_ts)] = (
                        locked_occupancy.get((room, model_ts), 0) + 1
                    )
                    for (year, prog), event_set in K_YD.items():
                        if event_id in event_set:
                            mod = event_module.get(event_id)
                            if mod:
                                locked_core_classes.setdefault(
                                    (year, prog, model_ts), set()
                                ).add(mod)
            else:
                non_vet_pairs.append((room, ts_str))

        if vet_pairs:
            fixed_vet[event_id] = vet_pairs
        if non_vet_pairs:
            fixed_non_vet[event_id] = non_vet_pairs
            warnings.append(
                f"Event {event_id}: Room Lock=Yes but room(s) not in vet set "
                f"({[r for r, _ in non_vet_pairs]}) — excluded from MILP"
            )

    print(
        f"Event classification:  free={len(E):,}, "
        f"fixed_vet={len(fixed_vet):,}, "
        f"fixed_non_vet={len(fixed_non_vet):,}"
    )
    year_totals: dict = {}
    for (yr, _pg), s_set in K_YD.items():
        year_totals[yr] = year_totals.get(yr, 0) + len(s_set)
    print(f"K_YD: {len(K_YD)} (year, prog) pairs; year totals: {year_totals}")

    return TimetablerSets(
        E=E,
        R=R,
        T=T,
        K_YD=K_YD,
        room_cap=room_cap,
        room_type = room_type,
        fixed_vet=fixed_vet,
        fixed_non_vet=fixed_non_vet,
        locked_occupancy=locked_occupancy,
        locked_core_classes=locked_core_classes,
        event_module=event_module,
        event_size=event_size,
        event_duration=event_duration,
        event_room_type=event_room_type,
        event_room_lock = event_room_lock,
        core_modules_YD=core_modules_YD,
        events=events,
        events_raw=events_raw,
        rooms_raw=rooms_raw,
        pc_raw=pc_raw,
        warnings=warnings,
    )
