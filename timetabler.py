"""Timetabler — MILP Formulation for Vet School Timetabling
==========================================================
Builds and solves a binary ILP using the Xpress Python API to assign
free events to (room, timeslot) pairs while respecting:
  C1 — room conflict   : at most one event per (room, timeslot)
  C2 — core-year clash : at most one compulsory event per (year, timeslot)
  C3 — assignment      : each free event assigned to exactly one (room, timeslot)
"""

import pandas as pd
import xpress as xp
from pathlib import Path

from utils import parse_weeks, parse_timeslot, DAY_ORDER, VET_DATA_DIR


class Timetabler:
    """MILP timetabling model for the Vet School."""

    def __init__(self, data_dir=None, start_week: int = 9, n_weeks: int = 2):
        self.data_dir = Path(data_dir) if data_dir else VET_DATA_DIR
        self.start_week = start_week
        self.n_weeks = n_weeks
        self.target_weeks = set(range(start_week, start_week + n_weeks))

        # Raw DataFrames (populated by _load_data)
        self._events_raw = None
        self._rooms_raw = None
        self._pc_raw = None

        # Week-filtered events DataFrame
        self.events = None

        # Sets (populated by _build_sets)
        self.E = []  # free event IDs entering the MILP
        self.R = []  # vet room IDs
        self.T = []  # sorted timeslot strings
        self.K_Y = {}  # {year_str: set(event_ids)} — compulsory per year
        self.room_cap = {}  # {room_id: capacity}

        # Fixed-event tracking
        self._fixed_vet = {}  # {event_id: [(room, timeslot), ...]}
        self._fixed_non_vet = {}  # {event_id: [(room, timeslot), ...]}
        self.locked_occupancy = {}  # {(r, t): count} for vet rooms
        self._locked_year_occupancy = {}  # {(year, t): count} for locked compulsory

        # Event-size cache
        self._event_size = {}  # {event_id: size or None}

        # Model artifacts (populated by build_model)
        self.model = None
        self.x = {}  # {(e, r, t): xp.var}
        self._var_keys = []  # [(e, r, t), ...] in creation order (matches getSolution)
        self._var_list = []  # [xp.var, ...]    in creation order

        # Post-solve state
        self._solve_status = None
        self.warnings = []

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_data(self):
        """Read Events, Rooms, and Programme-Course Excel files directly."""
        events_path = self.data_dir / "2024-5 Event Module Room.xlsx"
        rooms_path = self.data_dir / "Rooms and Room Types.xlsx"
        pc_path = self.data_dir / "Programme-Course.xlsx"

        print("Loading data files...")
        print(f"  Events:           {events_path}")
        self._events_raw = pd.read_excel(events_path)
        print(f"  Rooms:            {rooms_path}")
        self._rooms_raw = pd.read_excel(rooms_path)
        print(f"  Programme-Course: {pc_path}")
        self._pc_raw = pd.read_excel(pc_path)
        print(f"  Raw event rows:   {len(self._events_raw):,}")

    # ------------------------------------------------------------------
    # Set construction
    # ------------------------------------------------------------------

    def _build_sets(self):
        """Build E, R, T, K_Y and classify events as fixed or free."""

        # --- R: vet room IDs and capacities ---
        self.R = self._rooms_raw["Id"].dropna().unique().tolist()
        self.room_cap = (
            self._rooms_raw.dropna(subset=["Id"]).set_index("Id")["Capacity"].to_dict()
        )
        vet_room_set = set(self.R)

        # --- Week-filter events ---
        events = self._events_raw.copy()
        events["_pw"] = events["Weeks"].apply(parse_weeks)
        events = events[
            events["_pw"].apply(lambda ws: bool(ws & self.target_weeks))
        ].copy()
        self.events = events.drop(columns=["_pw"])
        print(
            f"Events in target weeks {sorted(self.target_weeks)}: {len(self.events):,} rows"
        )

        # Cache event sizes (one entry per Event ID)
        if "Event Size" in self.events.columns:
            size_series = (
                self.events[["Event ID", "Event Size"]]
                .drop_duplicates(subset="Event ID")
                .set_index("Event ID")["Event Size"]
            )
            self._event_size = size_series.to_dict()

        # --- T: sorted timeslots from the FULL raw dataset ---
        def _ts_sort_key(ts):
            day, hour = parse_timeslot(ts)
            try:
                day_idx = DAY_ORDER.index(day)
            except (ValueError, TypeError):
                day_idx = 99
            return (day_idx, hour or "")

        all_ts = self._events_raw["Timeslot"].dropna().unique().tolist()
        self.T = sorted(all_ts, key=_ts_sort_key)

        # --- K_Y: compulsory events grouped by year ---
        comp = self._pc_raw[self._pc_raw["Compulsory"] == True].copy()
        comp["Year"] = comp["CourseId"].str.extract(r"_YR(\d+)_", expand=False)
        comp = comp.dropna(subset=["Year"])
        mod_year = comp[["ModuleId", "Year"]].drop_duplicates()
        merged = self.events.merge(
            mod_year, left_on="Module Code", right_on="ModuleId", how="inner"
        )
        self.K_Y = merged.groupby("Year")["Event ID"].apply(set).to_dict()

        # --- Event classification ---
        for event_id, group in self.events.groupby("Event ID", sort=False):
            # Identify locked rows
            if "Room Lock" in group.columns:
                locked_mask = (
                    group["Room Lock"].fillna("").str.strip().str.upper() == "YES"
                )
                locked_rows = group[locked_mask]
            else:
                locked_rows = pd.DataFrame()

            if locked_rows.empty:
                # No locked room → free event: enters the MILP
                self.E.append(event_id)
                continue

            # Fixed event: collect room/timeslot pairs, split by vet vs non-vet
            vet_pairs = []
            non_vet_pairs = []

            for _, row in locked_rows.iterrows():
                room = row.get("Room") if "Room" in row.index else None
                ts = row.get("Timeslot") if "Timeslot" in row.index else None
                if pd.isna(room) or pd.isna(ts):
                    continue

                if room in vet_room_set:
                    vet_pairs.append((room, ts))
                    # Track room occupancy
                    self.locked_occupancy[(room, ts)] = (
                        self.locked_occupancy.get((room, ts), 0) + 1
                    )
                    # Track year occupancy for compulsory locked events
                    for year, event_set in self.K_Y.items():
                        if event_id in event_set:
                            key = (year, ts)
                            self._locked_year_occupancy[key] = (
                                self._locked_year_occupancy.get(key, 0) + 1
                            )
                else:
                    non_vet_pairs.append((room, ts))

            if vet_pairs:
                self._fixed_vet[event_id] = vet_pairs
            if non_vet_pairs:
                self._fixed_non_vet[event_id] = non_vet_pairs
                self.warnings.append(
                    f"Event {event_id}: Room Lock=Yes but room(s) not in vet set "
                    f"({[r for r, _ in non_vet_pairs]}) — excluded from MILP"
                )

        print(
            f"Event classification:  free={len(self.E):,}, "
            f"fixed_vet={len(self._fixed_vet):,}, "
            f"fixed_non_vet={len(self._fixed_non_vet):,}"
        )
        print(f"K_Y: { {y: len(s) for y, s in self.K_Y.items()} }")

    # ------------------------------------------------------------------
    # Model construction
    # ------------------------------------------------------------------

    def build_model(self):
        """Create the Xpress MILP problem with variables and constraints."""
        self.model = xp.problem()
        self.model.setLogFile("")  # suppress solver log (redirect to empty)

        # Pre-compute available (r, t) pairs — those not locked by fixed events
        available_rt = [
            (r, t)
            for r in self.R
            for t in self.T
            if self.locked_occupancy.get((r, t), 0) == 0
        ]

        # Compact indices for variable names
        e_idx = {e: i for i, e in enumerate(self.E)}
        r_idx = {r: i for i, r in enumerate(self.R)}
        t_idx = {t: i for i, t in enumerate(self.T)}

        # --- Variables x[e, r, t] ∈ {0, 1} ---
        print(
            f"Creating variables: {len(self.E):,} events × "
            f"{len(available_rt):,} available (r,t) pairs..."
        )
        for e in self.E:
            ei = e_idx[e]
            for r, t in available_rt:
                ri = r_idx[r]
                ti = t_idx[t]
                var = self.model.addVariable(vartype=xp.binary, name=f"x{ei}_{ri}_{ti}")
                self.x[(e, r, t)] = var
                self._var_keys.append((e, r, t))
                self._var_list.append(var)
        print(f"  Created {len(self.x):,} binary variables")

        # --- C1: Room conflict  ∑_E x[e,r,t] ≤ 1  ∀ r∈R, t∈T ---
        c1 = 0
        for r in self.R:
            for t in self.T:
                if self.locked_occupancy.get((r, t), 0) > 0:
                    continue
                vars_rt = [self.x[(e, r, t)] for e in self.E if (e, r, t) in self.x]
                if len(vars_rt) > 1:
                    self.model.addConstraint(xp.Sum(vars_rt) <= 1)
                    c1 += 1
        print(f"  C1 (room conflict):      {c1:,}")

        # --- C2: Core-year conflict  ∑_{e∈K_Y[y]} ∑_R x[e,r,t] ≤ 1  ∀ y, t∈T ---
        """c2 = 0
        free_set = set(self.E)
        for year, event_set in self.K_Y.items():
            free_in_year = [e for e in event_set if e in free_set]
            if not free_in_year:
                continue
            for t in self.T:
                vars_yt = [
                    self.x[(e, r, t)]
                    for e in free_in_year
                    for r in self.R
                    if (e, r, t) in self.x
                ]
                rhs = 1 - self._locked_year_occupancy.get((year, t), 0)
                if rhs <= 0:
                    # Locked compulsory event already occupies this slot: force all vars to 0
                    for v in vars_yt:
                        self.model.addConstraint(v <= 0)
                        c2 += 1
                elif vars_yt:
                    self.model.addConstraint(xp.Sum(vars_yt) <= rhs)
                    c2 += 1
        print(f"  C2 (core-year conflict): {c2:,}")"""

        # --- C3: Assignment  ∑_R ∑_T x[e,r,t] == 1  ∀ e∈E ---
        c3 = 0
        for e in self.E:
            vars_e = [
                self.x[(e, r, t)] for r in self.R for t in self.T if (e, r, t) in self.x
            ]
            if vars_e:
                self.model.addConstraint(xp.Sum(vars_e) == 1)
                c3 += 1
            else:
                self.warnings.append(
                    f"Event {e} has no feasible (Room, Timeslot) — model will be infeasible"
                )
        print(f"  C3 (assignment):         {c3:,}")

        # --- Objective: minimise 0 (pure feasibility) ---
        self.model.setObjective(0, sense=xp.minimize)
        print("Model built.")

    # ------------------------------------------------------------------
    # Solve
    # ------------------------------------------------------------------

    def solve(self) -> str:
        """Solve the MILP. Returns 'optimal', 'infeasible', or 'unknown'."""
        if self.model is None:
            raise RuntimeError("Call build_model() before solve().")
        print("Solving MILP...")
        solvestatus, solstatus = self.model.optimize()
        status_map = {
            xp.SolStatus.OPTIMAL: "optimal",
            xp.SolStatus.INFEASIBLE: "infeasible",
        }
        self._solve_status = status_map.get(solstatus, "unknown")
        print(
            f"Solve complete: {self._solve_status} (SolveStatus={solvestatus}, SolStatus={solstatus})"
        )
        return self._solve_status

    # ------------------------------------------------------------------
    # Solution extraction
    # ------------------------------------------------------------------

    def get_solution(self) -> pd.DataFrame:
        """Return DataFrame of all assigned events (MILP-assigned + fixed)."""
        rows = []

        # MILP-assigned free events
        if self._solve_status == "optimal" and self._var_list:
            sol = self.model.getSolution(self._var_list)
            for (e, r, t), val in zip(self._var_keys, sol):
                if val > 0.5:
                    rows.append(
                        {
                            "Event ID": e,
                            "Room": r,
                            "Timeslot": t,
                            "Source": "milp",
                            "Event Size": self._event_size.get(e),
                            "Room Capacity": self.room_cap.get(r),
                        }
                    )

        # Pre-assigned vet-room events
        for e, assignments in self._fixed_vet.items():
            for r, t in assignments:
                rows.append(
                    {
                        "Event ID": e,
                        "Room": r,
                        "Timeslot": t,
                        "Source": "fixed_vet",
                        "Event Size": self._event_size.get(e),
                        "Room Capacity": self.room_cap.get(r),
                    }
                )

        # Pre-assigned non-vet-room events (included for completeness)
        for e, assignments in self._fixed_non_vet.items():
            for r, t in assignments:
                rows.append(
                    {
                        "Event ID": e,
                        "Room": r,
                        "Timeslot": t,
                        "Source": "fixed_non_vet",
                        "Event Size": self._event_size.get(e),
                        "Room Capacity": self.room_cap.get(r),
                    }
                )

        return pd.DataFrame(
            rows,
            columns=[
                "Event ID",
                "Room",
                "Timeslot",
                "Source",
                "Event Size",
                "Room Capacity",
            ],
        )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_unassigned_events(self) -> list:
        """Return event IDs in E that have no feasible (Room, Timeslot) variable."""
        assigned_events = {e for e, r, t in self.x}
        return [e for e in self.E if e not in assigned_events]

    def summary(self):
        """Print counts, K_Y sizes, solve status, and any warnings."""
        print("=" * 60)
        print("TIMETABLER SUMMARY")
        print("=" * 60)
        print(f"  Data dir:            {self.data_dir}")
        print(f"  Target weeks:        {sorted(self.target_weeks)}")
        if self.events is not None:
            print(f"  Event rows (filt):   {len(self.events):,}")
        print(f"  Free events (E):     {len(self.E):,}")
        print(f"  Rooms (R):           {len(self.R):,}")
        print(f"  Timeslots (T):       {len(self.T):,}")
        print(f"  Fixed-vet events:    {len(self._fixed_vet):,}")
        print(f"  Fixed-non-vet:       {len(self._fixed_non_vet):,}")
        print(f"  MILP variables:      {len(self.x):,}")
        print("\n  Compulsory year groups (K_Y):")
        for yr in sorted(self.K_Y.keys()):
            print(f"    Year {yr}: {len(self.K_Y[yr]):,} event(s)")
        print(f"\n  Solve status:        {self._solve_status or 'not solved'}")
        unassigned = self.get_unassigned_events()
        if unassigned:
            print(f"  Unassigned events:   {len(unassigned):,}")
        if self.warnings:
            print(f"\n  Warnings ({len(self.warnings)}):")
            for w in self.warnings[:10]:
                print(f"    ! {w}")
            if len(self.warnings) > 10:
                print(f"    ... ({len(self.warnings) - 10} more)")

    # ------------------------------------------------------------------
    # Convenience entry point
    # ------------------------------------------------------------------

    def run(self) -> str:
        """Load data, build sets, build model, and solve. Returns solve status."""
        self._load_data()
        self._build_sets()
        self.build_model()
        return self.solve()
