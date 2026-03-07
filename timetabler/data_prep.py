"""data_prep.py — Set construction for the vet school timetabling MILP.

Exposes:
  TimetablerSets  — dataclass holding all problem-data sets
  build_sets      — load from Excel via TimetablingData, delegate to build_sets_from_frames
  build_sets_from_frames — pure logic, no file I/O (used directly in unit tests)
"""

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from utils import parse_weeks, timeslot_sort_key


@dataclass
class TimetablerSets:
    E: list                     # free event IDs entering the MILP
    R: list                     # vet room IDs
    T: list                     # sorted timeslot strings
    K_Y: dict                   # {year_str: set(event_ids)}
    room_cap: dict              # {room_id: capacity}
    fixed_vet: dict             # {event_id: [(room, ts), ...]}
    fixed_non_vet: dict         # {event_id: [(room, ts), ...]}
    locked_occupancy: dict      # {(r, t): count}
    locked_core_classes: dict   # {(year, t): set(module_codes)}
    event_module: dict          # {event_id: module_code}
    event_size: dict            # {event_id: size}
    core_modules_Y: dict        # {year: list of core module codes}
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

    # --- T: sorted timeslots from the FULL raw dataset ---
    all_ts = events_raw["Timeslot"].dropna().unique().tolist()
    T = sorted(all_ts, key=timeslot_sort_key)

    # --- K_Y: compulsory events grouped by year ---
    comp = pc_raw[pc_raw["Compulsory"]].copy()
    comp["Year"] = comp["CourseId"].str.extract(r"_YR(\d+)_", expand=False)
    comp = comp.dropna(subset=["Year"])
    mod_year = comp[["ModuleId", "Year"]].drop_duplicates()
    merged = events.merge(
        mod_year, left_on="Module Code", right_on="ModuleId", how="inner"
    )
    K_Y = merged.groupby("Year")["Event ID"].apply(set).to_dict()

    # --- event_module: event_id → module_code ---
    event_module = (
        events[["Event ID", "Module Code"]]
        .drop_duplicates(subset="Event ID")
        .set_index("Event ID")["Module Code"]
        .to_dict()
    )

    # --- core_modules_Y: year → sorted list of core module codes ---
    core_modules_Y: dict = {}
    for year, event_set in K_Y.items():
        mods = {event_module[e] for e in event_set if e in event_module}
        core_modules_Y[year] = sorted(mods)

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

            if room in vet_room_set:
                vet_pairs.append((room, ts))
                locked_occupancy[(room, ts)] = (
                    locked_occupancy.get((room, ts), 0) + 1
                )
                for year, event_set in K_Y.items():
                    if event_id in event_set:
                        mod = event_module.get(event_id)
                        if mod:
                            locked_core_classes.setdefault(
                                (year, ts), set()
                            ).add(mod)
            else:
                non_vet_pairs.append((room, ts))

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
    print(f"K_Y: { {y: len(s) for y, s in K_Y.items()} }")

    return TimetablerSets(
        E=E,
        R=R,
        T=T,
        K_Y=K_Y,
        room_cap=room_cap,
        fixed_vet=fixed_vet,
        fixed_non_vet=fixed_non_vet,
        locked_occupancy=locked_occupancy,
        locked_core_classes=locked_core_classes,
        event_module=event_module,
        event_size=event_size,
        core_modules_Y=core_modules_Y,
        events=events,
        events_raw=events_raw,
        rooms_raw=rooms_raw,
        pc_raw=pc_raw,
        warnings=warnings,
    )
