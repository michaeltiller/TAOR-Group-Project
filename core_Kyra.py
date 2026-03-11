"""core.py — Thin Timetabler orchestrator."""

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

   
    # Public step methods
  

    def load(self):
        """Load data from Excel and build problem sets."""
        self._sets = build_sets(self.data_dir, self.target_weeks)

    def build_model(self, disable_c1=False, disable_c2=False,
                    disable_c2_force=False, quiet=False):
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

    def solve(self, quiet=False) -> str:
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
        self._extractor = SolutionExtractor(
            self._sets, self._builder, self._solve_status
        )
        if not quiet:
            print(f"Solve complete: {self._solve_status}")
        return self._solve_status

    def run(self) -> str:
        """load → build_model → solve. Returns solve status."""
        self.load()
        self.build_model()
        return self.solve()


    # Solution delegation


    def get_solution(self) -> pd.DataFrame:
        if self._extractor is None:
            raise RuntimeError("Call solve() before get_solution().")
        return self._extractor.get_solution()

    def dump_timetable(self, path=None) -> pd.DataFrame:
        if self._extractor is None:
            raise RuntimeError("Call solve() before dump_timetable().")
        return self._extractor.dump_timetable(path=path)

    def get_unassigned_events(self) -> list:
        """Return events with no assignment in the solution."""
        # FIX: Only valid to call post-solve. Removed the ambiguous pre-solve path.
        if self._extractor is None:
            raise RuntimeError("Call solve() before get_unassigned_events().")
        return self._extractor.get_unassigned_events()

    def summary(self):
        """Print a summary of the current timetable state."""
        # FIX: Guard clause — be explicit about what's available
        if self._sets is None:
            print("Timetabler not yet loaded. Call load() first.")
            return

        s = self._sets
        print("=" * 60)
        print("TIMETABLER SUMMARY")
        print("=" * 60)
        print(f"  Data dir:            {self.data_dir}")
        print(f"  Target weeks:        {sorted(self.target_weeks)}")
        print(f"  Event rows (filt):   {len(s.events):,}")
        print(f"  Free events (E):     {len(s.E):,}")
        print(f"  Rooms (R):           {len(s.R):,}")
        print(f"  Timeslots (T):       {len(s.T):,}")
        print(f"  Fixed-vet events:    {len(s.fixed_vet):,}")
        print(f"  Fixed-non-vet:       {len(s.fixed_non_vet):,}")

        if self._builder is not None:
            print(f"  MILP variables:      {len(self._builder.x):,}")

        print("\n  Compulsory year groups (K_Y):")
        for yr in sorted(s.K_Y.keys()):
            print(f"    Year {yr}: {len(s.K_Y[yr]):,} event(s)")

        print(f"\n  Solve status:        {self._solve_status or 'not solved'}")

        # FIX: Only attempt unassigned lookup if we actually have a solution
        if self._extractor is not None:
            unassigned = self.get_unassigned_events()
            if unassigned:
                print(f"  Unassigned events:   {len(unassigned):,}")

        # FIX: Warnings live on _sets, not on self — access them directly
        if s.warnings:
            print(f"\n  Warnings ({len(s.warnings)}):")
            for w in s.warnings[:10]:
                print(f"    ! {w}")
            if len(s.warnings) > 10:
                print(f"    ... ({len(s.warnings) - 10} more)")

    
    # Properties
   

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
    def K_Y(self) -> dict:
        return self._sets.K_Y if self._sets else {}

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
