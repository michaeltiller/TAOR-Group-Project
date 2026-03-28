"""
improve_timetable.py — Simulated Annealing post-processor for the vet school timetable.

Loads an existing MILP solution from Excel and reshuffles MILP-assigned events to
maximise density (cluster events Mon–Wed, 10:00–15:00). Fixed events are never moved.

Usage:
    python improve_timetable.py --start-week 9
    python improve_timetable.py --input combined_output.xlsx --start-week 9
    python improve_timetable.py --start-week 9 --t0 20 --cooling 0.9999 --max-iter 200000
"""

import argparse
import math
import random
import sys
from pathlib import Path

import pandas as pd

from timetabler.data_prep import TimetablerSets, build_sets
from utils import DATA_DIR, parse_timeslot

OUT_DIR = Path("proposedTimetable")

# ---------------------------------------------------------------------------
# Cost function weights (tunable)
# ---------------------------------------------------------------------------

HOUR_PENALTY: dict[str, float] = {
    "09:00": 4,
    "10:00": 1,
    "11:00": 0,
    "12:00": 2,
    "13:00": 1,
    "14:00": 0,
    "15:00": 1,
    "16:00": 3,
    "17:00": 5,
}

DAY_PENALTY: dict[str, float] = {
    "Monday":    0,
    "Tuesday":   1,
    "Wednesday": 2,
    "Thursday":  4,
    "Friday":    10,
}


def timeslot_cost(ts: str) -> float:
    """Return the cost of placing a single event at timeslot ts."""
    day, hour = parse_timeslot(ts)
    return DAY_PENALTY.get(day, 0) + HOUR_PENALTY.get(hour, 0)


# ---------------------------------------------------------------------------
# TimetableImprover
# ---------------------------------------------------------------------------

class TimetableImprover:
    """SA-based post-processor. Moves only MILP-assigned events."""

    def __init__(self, sets: TimetablerSets, initial_df: pd.DataFrame):
        """
        Args:
            sets:        TimetablerSets built from raw data (Phase 1).
            initial_df:  Full solution DataFrame from Excel (all sources).
        """
        self._sets = sets
    
        # --- Phase 2: extract MILP assignments ---
        milp_df = initial_df[initial_df["Source"] == "milp"].copy()
        self.milp_events: list = milp_df["Event ID"].tolist()
        self.event_assignment: dict = {
            row["Event ID"]: (row["Room"], row["Timeslot"])
            for _, row in milp_df.iterrows()
        }
    
        # --- Build vet_room_slot_used: MILP + fixed_vet occupancy ---
        self.vet_room_slot_used: set = set()
        for e, (r, t) in self.event_assignment.items():
            self.vet_room_slot_used.add((r, t))
        for e, assignments in sets.fixed_vet.items():
            for r, t in assignments:
                self.vet_room_slot_used.add((r, t))
    
        # --- Build year_ts_modules from locked_core_classes + MILP assignments ---
        # {(year, prog, ts): set(module_code)}
        self.year_ts_modules: dict = {}
        for (year, prog, ts), mods in sets.locked_core_classes.items():
            self.year_ts_modules[(year, prog, ts)] = set(mods)
    
        # Ref-counter for MILP-contributed module occupancy (not locked).
        # {(year, prog, ts, mod): count}  — needed so parallel sections (same
        # module, multiple events) don't cause premature set.discard() on move.
        self._mod_count: dict = {}
    
        for e, (r, t) in self.event_assignment.items():
            mod = sets.event_module.get(e)
            if mod is None:
                continue
            for (year, prog), core_mods in sets.core_modules_YD.items():
                if mod in core_mods:
                    key = (year, prog, t)
                    self.year_ts_modules.setdefault(key, set()).add(mod)
                    ckey = (year, prog, t, mod)
                    self._mod_count[ckey] = self._mod_count.get(ckey, 0) + 1
    
        # Snapshot C2 violations present in the initial solution so validation
        # can distinguish pre-existing from SA-introduced violations.
        self._initial_c2_violations: set = self._current_c2_violations()
    
        # --- Build campus lookup and fix each event's campus at initialisation ---
        self._campus: dict = sets.campus  # {room_id: campus_name}
        self._event_campus: dict = {
            e: self._campus.get(r)
            for e, (r, _) in self.event_assignment.items()
        }
    
        # --- Pre-compute candidates per event (filtered by capacity AND campus) ---
        self.event_candidates: dict = {}
        for e in self.milp_events:
            size = sets.event_size.get(e, 0) or 0
            event_campus = self._event_campus.get(e)
            candidates = []
            for r in sets.R:
                if event_campus is not None and self._campus.get(r) != event_campus:
                    continue
                if (sets.room_cap.get(r) or 0) >= size:
                    for t in sets.T:
                        candidates.append((r, t))
            self.event_candidates[e] = candidates
        
        self._input_meta = (
            initial_df
            .drop_duplicates(subset="Event ID")
            .set_index("Event ID")
            .to_dict(orient="index")
        )

        

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _current_c2_violations(self) -> set:
        """Return frozenset of (year, prog, ts) keys that have >1 core module."""
        violations = set()
        sets = self._sets
        ytm: dict = {}
        for e, (r, t) in self.event_assignment.items():
            mod = sets.event_module.get(e)
            if mod is None:
                continue
            for (year, prog), core_mods in sets.core_modules_YD.items():
                if mod in core_mods:
                    ytm.setdefault((year, prog, t), set()).add(mod)
        for (year, prog, ts), mods in ytm.items():
            locked = sets.locked_core_classes.get((year, prog, ts), set())
            if len(mods | locked) > 1:
                violations.add((year, prog, ts))
        return violations

    # ------------------------------------------------------------------
    # Cost
    # ------------------------------------------------------------------

    def timeslot_cost(self, ts: str) -> float:
        return timeslot_cost(ts)

    def total_cost(self) -> float:
        return sum(timeslot_cost(t) for _, t in self.event_assignment.values())

    # ------------------------------------------------------------------
    # Feasibility helpers
    # ------------------------------------------------------------------

    def _c2_feasible(self, event, ts: str) -> bool:
        """Check C2 (core-class clash) for placing event at ts."""
        mod = self._sets.event_module.get(event)
        if mod is None:
            return True
        for (year, prog), core_mods in self._sets.core_modules_YD.items():
            if mod not in core_mods:
                continue
            occupied = self.year_ts_modules.get((year, prog, ts), set())
            if occupied - {mod}:
                return False
        return True

    def is_feasible_move(self, event, room, ts: str) -> bool:
        """Check C1, C2, capacity, and campus for placing event at (room, ts)."""
        sets = self._sets
    
        # Campus constraint: new room must be on the event's original campus
        event_campus = self._event_campus.get(event)
        if event_campus is not None and self._campus.get(room) != event_campus:
            return False
    
        if (room, ts) in self.vet_room_slot_used:
            return False
        size = sets.event_size.get(event, 0) or 0
        if (sets.room_cap.get(room) or 0) < size:
            return False
        return self._c2_feasible(event, ts)

    def is_feasible_swap(self, e1, e2) -> bool:
        """Check joint feasibility of swapping timeslots between e1 and e2.

        Rooms remain fixed; only timeslots swap.
        e1: t1 -> t2,  e2: t2 -> t1.
        """
        r1, t1 = self.event_assignment[e1]
        r2, t2 = self.event_assignment[e2]

        if t1 == t2:
            return False
        if r1 == r2:
            # Same-room swaps would desync vet_room_slot_used (set can't track
            # multiplicity). They also have zero cost delta, so skip them.
            return False

        # C1: after removing both current slots, the new slots must be free
        used_without_both = self.vet_room_slot_used - {(r1, t1), (r2, t2)}
        if (r1, t2) in used_without_both:
            return False
        if (r2, t1) in used_without_both:
            return False

        mod1 = self._sets.event_module.get(e1)
        mod2 = self._sets.event_module.get(e2)

        sets = self._sets

        def c2_ok_swap(mod_arriving, ts_dest, mod_departing):
            """Can mod_arriving go to ts_dest, given mod_departing is leaving ts_dest?

            Uses _mod_count so that a parallel-section event with mod_departing
            still at ts_dest correctly keeps that module in the base set.
            """
            if mod_arriving is None:
                return True
            for (year, prog), core_mods in sets.core_modules_YD.items():
                if mod_arriving not in core_mods:
                    continue
                base = set(self.year_ts_modules.get((year, prog, ts_dest), set()))
                # Only discard mod_departing if this is truly the last MILP event
                # carrying that module at ts_dest (ref count drops to 0) and it's
                # not pinned by locked_core_classes.
                if mod_departing and mod_departing in core_mods:
                    ckey = (year, prog, ts_dest, mod_departing)
                    remaining = self._mod_count.get(ckey, 0) - 1
                    locked_has_it = mod_departing in sets.locked_core_classes.get(
                        (year, prog, ts_dest), set()
                    )
                    if remaining <= 0 and not locked_has_it:
                        base.discard(mod_departing)
                if base - {mod_arriving}:
                    return False
            return True

        # e1 arrives at t2 (e2 departs t2), e2 arrives at t1 (e1 departs t1)
        return c2_ok_swap(mod1, t2, mod2) and c2_ok_swap(mod2, t1, mod1)

    # ------------------------------------------------------------------
    # Apply moves
    # ------------------------------------------------------------------

    def apply_move(self, event, old_r, old_t: str, new_r, new_t: str) -> None:
        """Apply a single-event move, updating all bookkeeping."""
        sets = self._sets
        self.vet_room_slot_used.discard((old_r, old_t))
        self.vet_room_slot_used.add((new_r, new_t))

        mod = sets.event_module.get(event)
        if mod:
            for (year, prog), core_mods in sets.core_modules_YD.items():
                if mod in core_mods:
                    # Decrement ref count at old slot; discard from set only when last
                    # AND the module isn't also present via locked_core_classes.
                    old_ckey = (year, prog, old_t, mod)
                    old_cnt = self._mod_count.get(old_ckey, 0) - 1
                    self._mod_count[old_ckey] = max(old_cnt, 0)
                    if old_cnt <= 0:
                        locked_at_old = sets.locked_core_classes.get((year, prog, old_t), set())
                        if mod not in locked_at_old:
                            old_key = (year, prog, old_t)
                            if old_key in self.year_ts_modules:
                                self.year_ts_modules[old_key].discard(mod)
                    # Increment ref count at new slot; add to set when first
                    new_ckey = (year, prog, new_t, mod)
                    new_cnt = self._mod_count.get(new_ckey, 0)
                    self._mod_count[new_ckey] = new_cnt + 1
                    if new_cnt == 0:
                        self.year_ts_modules.setdefault((year, prog, new_t), set()).add(mod)

        self.event_assignment[event] = (new_r, new_t)

    def apply_swap(self, e1, e2) -> None:
        """Swap timeslots between e1 and e2 (rooms stay fixed)."""
        r1, t1 = self.event_assignment[e1]
        r2, t2 = self.event_assignment[e2]
        # Apply sequentially; apply_move handles bookkeeping correctly.
        self.apply_move(e1, r1, t1, r1, t2)
        self.apply_move(e2, r2, t2, r2, t1)

    # ------------------------------------------------------------------
    # SA
    # ------------------------------------------------------------------

    def run_sa(
        self,
        t0: float = 20.0,
        t_final: float = 0.01,
        cooling: float = 0.9999,
        max_iter: int = 200_000,
        rng: random.Random | None = None,
    ) -> dict:
        """Run Simulated Annealing. Returns stats dict."""
        if rng is None:
            rng = random.Random(42)

        if not self.milp_events:
            print("  No MILP events to optimise.")
            return {"cost_before": 0, "cost_after": 0, "accepted": 0, "rejected": 0}

        cost = self.total_cost()
        cost_before = cost
        temp = t0
        accepted = rejected = 0

        for iteration in range(max_iter):
            if rng.random() < 0.5:
                # --- Single move ---
                event = rng.choice(self.milp_events)
                candidates = self.event_candidates.get(event)
                if not candidates:
                    rejected += 1
                    continue
                new_r, new_t = rng.choice(candidates)
                old_r, old_t = self.event_assignment[event]
                if new_r == old_r and new_t == old_t:
                    rejected += 1
                    continue
                if not self.is_feasible_move(event, new_r, new_t):
                    rejected += 1
                    continue
                delta = timeslot_cost(new_t) - timeslot_cost(old_t)
                if delta < 0 or rng.random() < math.exp(-delta / max(temp, 1e-10)):
                    self.apply_move(event, old_r, old_t, new_r, new_t)
                    cost += delta
                    accepted += 1
                else:
                    rejected += 1

            else:
                # --- Timeslot-only swap ---
                if len(self.milp_events) < 2:
                    rejected += 1
                    continue
                e1, e2 = rng.sample(self.milp_events, 2)
                _, t1 = self.event_assignment[e1]
                _, t2 = self.event_assignment[e2]
                if not self.is_feasible_swap(e1, e2):
                    rejected += 1
                    continue
                # Swap delta is always zero (costs cancel symmetrically).
                # Swaps serve as diversification to escape local optima.
                self.apply_swap(e1, e2)
                accepted += 1

            temp = max(temp * cooling, t_final)

            if iteration % 10_000 == 0:
                print(
                    f"  iter {iteration:>7d}  T={temp:.4f}  cost={cost:.1f}  "
                    f"acc={accepted}  rej={rejected}"
                )

        cost_after = self.total_cost()
        print(
            f"\n  SA complete: cost {cost_before:.1f} -> {cost_after:.1f}  "
            f"(accepted={accepted}, rejected={rejected})"
        )
        return {
            "cost_before": cost_before,
            "cost_after": cost_after,
            "accepted": accepted,
            "rejected": rejected,
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_solution(self) -> None:
        """Assert solution is feasible. Raises ValueError with details on violation."""
        sets = self._sets

        # C1: no two MILP events share (Room, Timeslot)
        seen: dict = {}
        for e, (r, t) in self.event_assignment.items():
            key = (r, t)
            if key in seen:
                raise ValueError(
                    f"C1 violation: ({r}, {t}) used by both {seen[key]} and {e}"
                )
            seen[key] = e

        # C2: at most one distinct core module per (year, prog, ts) in MILP events
        # Build fresh from MILP assignments only (locked_core_classes covers fixed side)
        ytm_check: dict = {}
        for e, (r, t) in self.event_assignment.items():
            mod = sets.event_module.get(e)
            if mod is None:
                continue
            for (year, prog), core_mods in sets.core_modules_YD.items():
                if mod in core_mods:
                    key = (year, prog, t)
                    ytm_check.setdefault(key, set()).add(mod)

        new_c2_violations = []
        for (year, prog, ts), mods in ytm_check.items():
            locked = sets.locked_core_classes.get((year, prog, ts), set())
            all_mods = mods | locked
            if len(all_mods) > 1:
                key = (year, prog, ts)
                if key not in self._initial_c2_violations:
                    # SA-introduced violation — hard error
                    raise ValueError(
                        f"C2 violation (SA-introduced): ({year}, {prog}, {ts}) has modules {all_mods}"
                    )
                else:
                    new_c2_violations.append((year, prog, ts, all_mods))

        if new_c2_violations:
            print(
                f"  WARNING: {len(new_c2_violations)} pre-existing C2 violations inherited "
                f"from the input solution (not introduced by SA)."
            )

        # Fixed events must not appear in event_assignment
        for e in sets.fixed_vet:
            if e in self.event_assignment:
                raise ValueError(
                    f"Fixed-vet event {e} was moved (found in event_assignment)"
                )

        print("  validate_solution: all checks passed")

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def get_solution(self) -> pd.DataFrame:
        """Return DataFrame in the same schema as the input solution."""
        sets = self._sets
    
        def meta(e, col):
            return self._input_meta.get(e, {}).get(col)
    
        rows = []
    
        for e, (r, t) in self.event_assignment.items():
            rows.append({
                "Event ID":           e,
                "Room":               r,
                "Timeslot":           t,
                "Source":             "milp",
                "Event Size":         meta(e, "Event Size"),
                "Room Capacity":      sets.room_cap.get(r),
                "Event Name":         meta(e, "Event Name"),
                "Event Type":         meta(e, "Event Type"),
                "Module Code":        meta(e, "Module Code"),
                "Module Name":        meta(e, "Module Name"),
                "Duration (minutes)": meta(e, "Duration (minutes)"),
                "Weeks":              meta(e, "Weeks"),
                "Semester":           meta(e, "Semester"),
            })
    
        for e, assignments in sets.fixed_vet.items():
            for r, t in assignments:
                rows.append({
                    "Event ID":           e,
                    "Room":               r,
                    "Timeslot":           t,
                    "Source":             "fixed_vet",
                    "Event Size":         meta(e, "Event Size"),
                    "Room Capacity":      sets.room_cap.get(r),
                    "Event Name":         meta(e, "Event Name"),
                    "Event Type":         meta(e, "Event Type"),
                    "Module Code":        meta(e, "Module Code"),
                    "Module Name":        meta(e, "Module Name"),
                    "Duration (minutes)": meta(e, "Duration (minutes)"),
                    "Weeks":              meta(e, "Weeks"),
                    "Semester":           meta(e, "Semester"),
                })
    
        for e, assignments in sets.fixed_non_vet.items():
            for r, t in assignments:
                rows.append({
                    "Event ID":           e,
                    "Room":               r,
                    "Timeslot":           t,
                    "Source":             "fixed_non_vet",
                    "Event Size":         meta(e, "Event Size"),
                    "Room Capacity":      sets.room_cap.get(r),
                    "Event Name":         meta(e, "Event Name"),
                    "Event Type":         meta(e, "Event Type"),
                    "Module Code":        meta(e, "Module Code"),
                    "Module Name":        meta(e, "Module Name"),
                    "Duration (minutes)": meta(e, "Duration (minutes)"),
                    "Weeks":              meta(e, "Weeks"),
                    "Semester":           meta(e, "Semester"),
                })
    
        return pd.DataFrame(rows, columns=[
            "Event ID", "Room", "Timeslot", "Source", "Event Size", "Room Capacity",
            "Event Name", "Event Type", "Module Code", "Module Name",
            "Duration (minutes)", "Weeks", "Semester",
        ])
# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _enrich(sol: pd.DataFrame, events_df: pd.DataFrame) -> pd.DataFrame:
    want = ["Event ID", "Event Name", "Event Type", "Module Code", "Module Name"]
    available = [c for c in want if c in events_df.columns]
    meta = events_df[available].drop_duplicates(subset="Event ID")
    return sol.merge(meta, on="Event ID", how="left")


def main():
    parser = argparse.ArgumentParser(
        description="SA post-processor for the vet school timetable."
    )
    parser.add_argument(
        "--start-week", type=int, default=9,
        help="First week of the 2-week window (default: 9)",
    )
    parser.add_argument(
        "--input", type=str, default=None,
        help="Path to solution Excel (default: proposedTimetable/solution_weeks_<s>_<e>.xlsx)",
    )
    parser.add_argument("--t0",       type=float, default=20.0)
    parser.add_argument("--t-final",  type=float, default=0.01)
    parser.add_argument("--cooling",  type=float, default=0.9999)
    parser.add_argument("--max-iter", type=int,   default=200_000)
    parser.add_argument("--seed",     type=int,   default=42)
    args = parser.parse_args()

    start = args.start_week
    end   = start + 1
    tag   = f"weeks_{start}_{end}"

    input_path = Path(args.input) if args.input else OUT_DIR / f"solution_{tag}.xlsx"
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # --- Phase 1: build constraint sets from raw data ---
    print(f"\n=== Phase 1: Loading constraint data (weeks {start}-{end}) ===")
    sets = build_sets(DATA_DIR, {start, end})

    # --- Phase 2: load existing solution ---
    print(f"\n=== Phase 2: Loading initial solution from {input_path} ===")
    initial_df = pd.read_excel(input_path)
    milp_count = int((initial_df["Source"] == "milp").sum())
    print(f"  Total rows: {len(initial_df):,}  MILP rows: {milp_count:,}")

    # --- Build improver ---
    improver = TimetableImprover(sets, initial_df)
    print(f"  Candidate pool built for {len(improver.milp_events)} MILP events")
    print(f"  Initial cost: {improver.total_cost():.1f}")

    # --- Run SA ---
    print(f"\n=== Running Simulated Annealing ===")
    print(
        f"  t0={args.t0}  t_final={args.t_final}  "
        f"cooling={args.cooling}  max_iter={args.max_iter}  seed={args.seed}"
    )
    rng = random.Random(args.seed)
    stats = improver.run_sa(
        t0=args.t0,
        t_final=args.t_final,
        cooling=args.cooling,
        max_iter=args.max_iter,
        rng=rng,
    )

    print(f"\n  Cost before: {stats['cost_before']:.1f}")
    print(f"  Cost after:  {stats['cost_after']:.1f}")
    print(f"  Improvement: {stats['cost_before'] - stats['cost_after']:.1f}")

    # --- Validate ---
    print("\n=== Validating solution ===")
    improver.validate_solution()

    # --- Build output ---
    sol = improver.get_solution()
    if sets.events_raw is not None:
        sol = _enrich(sol, sets.events_raw)

    # --- Write outputs ---
    from main import write_flat_solution, write_timetable_grid

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    flat_path = OUT_DIR / f"improved_{tag}.xlsx"
    grid_path = OUT_DIR / f"improved_{tag}_grid.xlsx"

    print(f"\n=== Writing outputs to {OUT_DIR}/ ===")
    write_flat_solution(sol, flat_path)
    write_timetable_grid(sol, grid_path)

    print(f"\nDone. {len(sol):,} events written ({milp_count:,} MILP-assigned).")


if __name__ == "__main__":
    main()
