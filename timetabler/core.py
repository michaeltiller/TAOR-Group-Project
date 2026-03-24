"""core.py — Thin Timetabler orchestrator.

Delegates data loading to data_prep, model building to ModelBuilder,
and solution extraction to SolutionExtractor.
"""

from pathlib import Path

import pandas as pd

from utils import VET_DATA_DIR
from .data_prep import TimetablerSets, build_sets
from .model_builder import ModelBuilder
from .solution import SolutionExtractor


class Timetabler:
    """MILP timetabling model for the Vet School."""

    def __init__(self, data_dir=None, start_week: int = 9, n_weeks: int = 2):
        self.data_dir = Path(data_dir) if data_dir else VET_DATA_DIR
        self.start_week = start_week
        self.n_weeks = n_weeks
        self.target_weeks = set(range(start_week, start_week + n_weeks))

        self._sets: TimetablerSets | None = None
        self._builder: ModelBuilder | None = None
        self._extractor: SolutionExtractor | None = None
        self._solve_status: str | None = None

    # ------------------------------------------------------------------
    # Public step methods
    # ------------------------------------------------------------------

    def load(self):
        """Load data from Excel and build problem sets."""
        self._sets = build_sets(self.data_dir, self.target_weeks)

    def build_model(
        self,
        disable_c1: bool = False,
        disable_c2: bool = False,
        disable_c2_force: bool = False,
        quiet: bool = False,
    ):
        """Build the Xpress MILP problem."""
        if self._sets is None:
            raise RuntimeError("Call load() before build_model().")
        self._builder = ModelBuilder(self._sets)
        self._builder.build(
            disable_c1=disable_c1,
            disable_c2=disable_c2,
            disable_c2_force=disable_c2_force,
            quiet=quiet,
        )

    def solve(self, quiet: bool = False) -> str:
        """Solve the MILP. Returns 'optimal', 'infeasible', or 'unknown'."""
        if self._builder is None or self._builder.model is None:
            raise RuntimeError("Call build_model() before solve().")
        import xpress as xp
        if not quiet:
            print("Solving MILP...")
        solvestatus, solstatus = self._builder.model.optimize()
        status_map = {
            xp.SolStatus.OPTIMAL: "optimal",
            xp.SolStatus.INFEASIBLE: "infeasible",
        }
        self._solve_status = status_map.get(solstatus, "unknown")
        self._extractor = SolutionExtractor(self._sets, self._builder, self._solve_status)
        if not quiet:
            print(
                f"Solve complete: {self._solve_status} "
                f"(SolveStatus={solvestatus}, SolStatus={solstatus})"
            )
        return self._solve_status

    def run(self) -> str:
        """load → build_model → solve. Returns solve status."""
        self.load()
        self.build_model()
        return self.solve()

    # ------------------------------------------------------------------
    # Solution delegation
    # ------------------------------------------------------------------

    def get_solution(self) -> pd.DataFrame:
        if self._extractor is None:
            raise RuntimeError("Call solve() before get_solution().")
        return self._extractor.get_solution()

    def dump_timetable(self, path=None) -> pd.DataFrame:
        if self._extractor is None:
            raise RuntimeError("Call solve() before dump_timetable().")
        return self._extractor.dump_timetable(path=path)

    def get_unassigned_events(self) -> list:
        if self._builder is None:
            return []
        if self._extractor is not None:
            return self._extractor.get_unassigned_events()
        assigned_events = {key[0] for key in self._builder.x}
        return [e for e in self.E if e not in assigned_events]

    def summary(self):
        """Print counts, K_Y sizes, solve status, and any warnings."""
        s = self._sets
        print("=" * 60)
        print("TIMETABLER SUMMARY")
        print("=" * 60)
        print(f"  Data dir:            {self.data_dir}")
        print(f"  Target weeks:        {sorted(self.target_weeks)}")
        if s is not None and s.events is not None:
            print(f"  Event rows (filt):   {len(s.events):,}")
        print(f"  Free events (E):     {len(self.E):,}")
        print(f"  Rooms (R):           {len(self.R):,}")
        print(f"  Timeslots (T):       {len(self.T):,}")
        if s is not None:
            print(f"  Fixed-vet events:    {len(s.fixed_vet):,}")
            print(f"  Fixed-non-vet:       {len(s.fixed_non_vet):,}")
        print(f"  MILP variables:      {len(self.x):,}")
        print("\n  Compulsory year groups (K_YD):")
        year_totals: dict = {}
        for (yr, _pg), evs in self.K_YD.items():
            year_totals[yr] = year_totals.get(yr, 0) + len(evs)
        for yr in sorted(year_totals.keys()):
            print(f"    Year {yr}: {year_totals[yr]:,} event(s) across "
                  f"{sum(1 for (y,_) in self.K_YD if y==yr):,} programme(s)")
        print(f"\n  Solve status:        {self._solve_status or 'not solved'}")
        unassigned = self.get_unassigned_events()
        if unassigned:
            print(f"  Unassigned events:   {len(unassigned):,}")
        warnings = self.warnings
        if warnings:
            print(f"\n  Warnings ({len(warnings)}):")
            for w in warnings[:10]:
                print(f"    ! {w}")
            if len(warnings) > 10:
                print(f"    ... ({len(warnings) - 10} more)")

    # ------------------------------------------------------------------
    # Properties (for diagnose.py and main.py attribute access)
    # ------------------------------------------------------------------

    @property
    def E(self) -> list:
        return self._sets.E if self._sets else []

    @property
    def R(self) -> list:
        return self._sets.R if self._sets else []

    @property
    def T(self) -> list:
        return self._sets.T if self._sets else []

    @property
    def K_YD(self) -> dict:
        return self._sets.K_YD if self._sets else {}

    @property
    def events(self) -> pd.DataFrame | None:
        return self._sets.events if self._sets else None

    @property
    def x(self) -> dict:
        return self._builder.x if self._builder else {}

    @property
    def locked_core_classes(self) -> dict:
        return self._sets.locked_core_classes if self._sets else {}

    @property
    def warnings(self) -> list:
        return self._sets.warnings if self._sets else []
