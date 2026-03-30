"""
improve_timetable.py — Simulated Annealing post-processor for the vet school timetable.

Loads an existing MILP solution from Excel and reshuffles MILP-assigned events to
maximise density (cluster events Mon–Wed, 10:00–15:00). Fixed events are never moved.
Pre-existing C2 violations inherited from the input are resolved where possible;
new C2 violations are never introduced.

Usage:
    python improve_timetable.py --start-week 9
    python improve_timetable.py --input combined_output.xlsx --start-week 9
    python improve_timetable.py --start-week 9 --t0 20 --cooling 0.9999 --max-iter 200000
"""

import argparse
import math
import random
import sys
from collections import Counter, defaultdict
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
    "11:00": 1,
    "12:00": 0,
    "13:00": 1,
    "14:00": 1,
    "15:00": 1,
    "16:00": 3,
    "17:00": 5,
}

DAY_PENALTY: dict[str, float] = {
    "Monday":    0,
    "Tuesday":   1,
    "Wednesday": 2,
    "Thursday":  4,
    "Friday":    15,
}

# Penalty per remaining fixable C2 clash — must dominate any timeslot cost gain
C2_PENALTY: float = 1000.0


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
        self._sets = sets

        # --- Extract MILP assignments ---
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
        self.year_ts_modules: dict = {}
        for (year, prog, ts), mods in sets.locked_core_classes.items():
            self.year_ts_modules[(year, prog, ts)] = set(mods)

        # Ref-counter for MILP-contributed module occupancy
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

        # --- Reverse index: timeslot -> list of MILP events ---
        self._ts_to_milp_events: dict = defaultdict(list)
        for e, (r, t) in self.event_assignment.items():
            self._ts_to_milp_events[t].append(e)

        # --- Snapshot C2 violations ---
        all_initial = self._compute_c2_violations()
        self._initial_c2_violations: set = all_initial

        self._initial_c2_sizes: dict = {}
        for (year, prog, ts) in all_initial:
            mods = self.year_ts_modules.get((year, prog, ts), set()).copy()
            locked = sets.locked_core_classes.get((year, prog, ts), set())
            self._initial_c2_sizes[(year, prog, ts)] = len(mods | locked)

        self._fixable_c2_violations: set = set()
        self._unfixable_c2_violations: set = set()
        for (year, prog, ts) in all_initial:
            milp_mods_at_ts = set()
            for e in self._ts_to_milp_events.get(ts, []):
                mod = sets.event_module.get(e)
                if mod and mod in sets.core_modules_YD.get((year, prog), []):
                    milp_mods_at_ts.add(mod)
            if milp_mods_at_ts:
                self._fixable_c2_violations.add((year, prog, ts))
            else:
                self._unfixable_c2_violations.add((year, prog, ts))

        # --- Incrementally maintained cost state ---
        # Track fixable clashes as a live set for O(1) count
        self._live_fixable_clashes: set = set(self._fixable_c2_violations)
        # Track per-event timeslot cost for O(1) delta on move
        self._event_ts_cost: dict = {
            e: timeslot_cost(t) for e, (r, t) in self.event_assignment.items()
        }
        self._density_cost: float = sum(self._event_ts_cost.values())
        self._clash_count: int = len(self._live_fixable_clashes)

        # --- Build campus lookup ---
        self._campus: dict = sets.campus
        self._event_campus: dict = {
            e: self._campus.get(r)
            for e, (r, _) in self.event_assignment.items()
        }

        # --- Pre-compute candidates per event ---
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

        # --- Store input metadata ---
        self._input_meta = (
            initial_df
            .drop_duplicates(subset="Event ID")
            .set_index("Event ID")
            .to_dict(orient="index")
        )

        print(f"  Total initial C2 violations:    {len(all_initial)}")
        print(f"  Fixable by SA (MILP involved):  {len(self._fixable_c2_violations)}")
        print(f"  Unfixable (locked events only): {len(self._unfixable_c2_violations)}")

    # ------------------------------------------------------------------
    # Incremental cost helpers
    # ------------------------------------------------------------------

    def _total_cost_incremental(self) -> float:
        return self._density_cost + C2_PENALTY * self._clash_count

    def _fixable_clash_at(self, year, prog, ts) -> bool:
        """Is there currently a fixable clash at (year, prog, ts)?"""
        mods = self.year_ts_modules.get((year, prog, ts), set())
        locked = self._sets.locked_core_classes.get((year, prog, ts), set())
        key = (year, prog, ts)
        return (
            len(mods | locked) > 1
            and key not in self._unfixable_c2_violations
        )

    def _update_clash_state(self, year, prog, ts) -> None:
        """Update _live_fixable_clashes and _clash_count for a single slot."""
        key = (year, prog, ts)
        if key in self._unfixable_c2_violations:
            return
        if self._fixable_clash_at(year, prog, ts):
            if key not in self._live_fixable_clashes:
                self._live_fixable_clashes.add(key)
                self._clash_count += 1
        else:
            if key in self._live_fixable_clashes:
                self._live_fixable_clashes.discard(key)
                self._clash_count -= 1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _compute_c2_violations(self) -> set:
        """Compute all current C2 violations from scratch."""
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
    # Feasibility helpers
    # ------------------------------------------------------------------

    def _c2_feasible(self, event, ts: str) -> bool:
        """Hard-rejects new clashes; never allows worsening of existing ones."""
        mod = self._sets.event_module.get(event)
        if mod is None:
            return True
        for (year, prog), core_mods in self._sets.core_modules_YD.items():
            if mod not in core_mods:
                continue
            occupied = self.year_ts_modules.get((year, prog, ts), set())
            new_mods = occupied | {mod}
            locked = self._sets.locked_core_classes.get((year, prog, ts), set())
            total = len(new_mods | locked)
            initial_size = self._initial_c2_sizes.get((year, prog, ts), 1)
            if (year, prog, ts) not in self._initial_c2_sizes:
                if total > 1:
                    return False
            else:
                if total > initial_size:
                    return False
        return True

    def is_feasible_move(self, event, room, ts: str) -> bool:
        """Check C1, C2, capacity, and campus."""
        sets = self._sets
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
        """Check feasibility of swapping timeslots between e1 and e2."""
        r1, t1 = self.event_assignment[e1]
        r2, t2 = self.event_assignment[e2]
        if t1 == t2 or r1 == r2:
            return False
        used_without_both = self.vet_room_slot_used - {(r1, t1), (r2, t2)}
        if (r1, t2) in used_without_both or (r2, t1) in used_without_both:
            return False

        mod1 = self._sets.event_module.get(e1)
        mod2 = self._sets.event_module.get(e2)
        sets = self._sets

        def c2_ok_swap(mod_arriving, ts_dest, mod_departing, year, prog):
            if mod_arriving is None:
                return True
            base = set(self.year_ts_modules.get((year, prog, ts_dest), set()))
            if mod_departing and mod_departing in sets.core_modules_YD.get((year, prog), []):
                ckey = (year, prog, ts_dest, mod_departing)
                remaining = self._mod_count.get(ckey, 0) - 1
                locked_has_it = mod_departing in sets.locked_core_classes.get(
                    (year, prog, ts_dest), set()
                )
                if remaining <= 0 and not locked_has_it:
                    base.discard(mod_departing)
            new_mods = base | {mod_arriving}
            locked = sets.locked_core_classes.get((year, prog, ts_dest), set())
            total = len(new_mods | locked)
            initial_size = self._initial_c2_sizes.get((year, prog, ts_dest), 1)
            if (year, prog, ts_dest) not in self._initial_c2_sizes:
                if total > 1:
                    return False
            else:
                if total > initial_size:
                    return False
            return True

        for (year, prog), core_mods in sets.core_modules_YD.items():
            if mod1 in core_mods:
                if not c2_ok_swap(mod1, t2, mod2, year, prog):
                    return False
            if mod2 in core_mods:
                if not c2_ok_swap(mod2, t1, mod1, year, prog):
                    return False
        return True

    # ------------------------------------------------------------------
    # Apply moves (with incremental cost update)
    # ------------------------------------------------------------------

    def apply_move(self, event, old_r, old_t: str, new_r, new_t: str) -> None:
        """Apply a move and update all bookkeeping + incremental cost."""
        sets = self._sets

        self.vet_room_slot_used.discard((old_r, old_t))
        self.vet_room_slot_used.add((new_r, new_t))

        # Update reverse timeslot index
        ts_list = self._ts_to_milp_events[old_t]
        if event in ts_list:
            ts_list.remove(event)
        self._ts_to_milp_events[new_t].append(event)

        # Update incremental density cost
        old_tc = timeslot_cost(old_t)
        new_tc = timeslot_cost(new_t)
        self._density_cost += new_tc - old_tc
        self._event_ts_cost[event] = new_tc

        mod = sets.event_module.get(event)
        affected_slots: set = set()

        if mod:
            for (year, prog), core_mods in sets.core_modules_YD.items():
                if mod in core_mods:
                    # Decrement old slot
                    old_ckey = (year, prog, old_t, mod)
                    old_cnt = self._mod_count.get(old_ckey, 0) - 1
                    self._mod_count[old_ckey] = max(old_cnt, 0)
                    if old_cnt <= 0:
                        locked_at_old = sets.locked_core_classes.get((year, prog, old_t), set())
                        if mod not in locked_at_old:
                            old_key = (year, prog, old_t)
                            if old_key in self.year_ts_modules:
                                self.year_ts_modules[old_key].discard(mod)
                    affected_slots.add((year, prog, old_t))

                    # Increment new slot
                    new_ckey = (year, prog, new_t, mod)
                    new_cnt = self._mod_count.get(new_ckey, 0)
                    self._mod_count[new_ckey] = new_cnt + 1
                    if new_cnt == 0:
                        self.year_ts_modules.setdefault((year, prog, new_t), set()).add(mod)
                    affected_slots.add((year, prog, new_t))

        self.event_assignment[event] = (new_r, new_t)

        # Update clash state for affected slots only
        for (year, prog, ts) in affected_slots:
            self._update_clash_state(year, prog, ts)

    def apply_swap(self, e1, e2) -> None:
        """Swap timeslots between e1 and e2 (rooms stay fixed)."""
        r1, t1 = self.event_assignment[e1]
        r2, t2 = self.event_assignment[e2]
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

        cost = self._total_cost_incremental()
        cost_before = cost
        temp = t0
        accepted = rejected = 0

        # Pre-build clash event list; refresh periodically
        clash_events_list: list = self._get_clash_events_list()
        CLASH_REFRESH = 5_000

        for iteration in range(max_iter):

            if iteration % CLASH_REFRESH == 0 and iteration > 0:
                clash_events_list = self._get_clash_events_list()

            if rng.random() < 0.5:
                # --- Single move ---
                if clash_events_list and rng.random() < 0.7:
                    event = rng.choice(clash_events_list)
                else:
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

                old_cost = self._total_cost_incremental()
                self.apply_move(event, old_r, old_t, new_r, new_t)
                new_cost = self._total_cost_incremental()
                delta = new_cost - old_cost

                if delta < 0 or rng.random() < math.exp(-delta / max(temp, 1e-10)):
                    cost = new_cost
                    accepted += 1
                else:
                    self.apply_move(event, new_r, new_t, old_r, old_t)
                    rejected += 1

            else:
                # --- Timeslot-only swap ---
                if len(self.milp_events) < 2:
                    rejected += 1
                    continue
                e1, e2 = rng.sample(self.milp_events, 2)
                if not self.is_feasible_swap(e1, e2):
                    rejected += 1
                    continue

                old_cost = self._total_cost_incremental()
                self.apply_swap(e1, e2)
                new_cost = self._total_cost_incremental()
                delta = new_cost - old_cost

                if delta < 0 or rng.random() < math.exp(-delta / max(temp, 1e-10)):
                    cost = new_cost
                    accepted += 1
                else:
                    self.apply_swap(e1, e2)
                    rejected += 1

            temp = max(temp * cooling, t_final)

            if iteration % 10_000 == 0:
                print(
                    f"  iter {iteration:>7d}  T={temp:.4f}  cost={cost:.1f}  "
                    f"fixable_clashes={self._clash_count}  acc={accepted}  rej={rejected}"
                )

        cost_after = self._total_cost_incremental()
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

    def _get_clash_events_list(self) -> list:
        """Return list of MILP events currently in fixable clashes."""
        sets = self._sets
        result = []
        for (year, prog, ts) in self._live_fixable_clashes:
            for e in self._ts_to_milp_events.get(ts, []):
                mod = sets.event_module.get(e)
                if mod and mod in sets.core_modules_YD.get((year, prog), []):
                    result.append(e)
        return result

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_solution(self) -> None:
        """Assert solution is feasible."""
        sets = self._sets

        # C1
        seen: dict = {}
        for e, (r, t) in self.event_assignment.items():
            key = (r, t)
            if key in seen:
                raise ValueError(f"C1 violation: ({r}, {t}) used by {seen[key]} and {e}")
            seen[key] = e

        # C2
        ytm_check: dict = {}
        for e, (r, t) in self.event_assignment.items():
            mod = sets.event_module.get(e)
            if mod is None:
                continue
            for (year, prog), core_mods in sets.core_modules_YD.items():
                if mod in core_mods:
                    ytm_check.setdefault((year, prog, t), set()).add(mod)

        remaining_fixable = []
        sa_introduced = []
        for (year, prog, ts), mods in ytm_check.items():
            locked = sets.locked_core_classes.get((year, prog, ts), set())
            all_mods = mods | locked
            current_size = len(all_mods)
            if current_size > 1:
                key = (year, prog, ts)
                initial_size = self._initial_c2_sizes.get(key, 1)
                if key not in self._initial_c2_violations or current_size > initial_size:
                    sa_introduced.append((year, prog, ts, all_mods))
                elif key in self._fixable_c2_violations:
                    remaining_fixable.append((year, prog, ts, all_mods))

        if sa_introduced:
            raise ValueError(
                "C2 violation(s) introduced or worsened by SA:\n" +
                "\n".join(f"  ({y}, {p}, {ts}): {sorted(m)}" for y, p, ts, m in sa_introduced)
            )
        if remaining_fixable:
            print(f"  WARNING: {len(remaining_fixable)} fixable C2 violation(s) remain:")
            for year, prog, ts, mods in sorted(remaining_fixable):
                print(f"    ({year}, {prog}, {ts}): {sorted(mods)}")

        for e in sets.fixed_vet:
            if e in self.event_assignment:
                raise ValueError(f"Fixed-vet event {e} was moved")

        print("  validate_solution: all checks passed")

    # ------------------------------------------------------------------
    # KPIs
    # ------------------------------------------------------------------

    def print_kpis(self) -> None:
        """Print key timetabling KPIs."""
        sets = self._sets

        ytm: dict = {}
        for e, (r, t) in self.event_assignment.items():
            mod = sets.event_module.get(e)
            if mod is None:
                continue
            for (year, prog), core_mods in sets.core_modules_YD.items():
                if mod in core_mods:
                    ytm.setdefault((year, prog, t), set()).add(mod)

        fixable_remaining = []
        unfixable_remaining = []
        for (year, prog, ts), mods in ytm.items():
            locked = sets.locked_core_classes.get((year, prog, ts), set())
            all_mods = mods | locked
            if len(all_mods) > 1:
                key = (year, prog, ts)
                if key in self._unfixable_c2_violations:
                    unfixable_remaining.append((year, prog, ts, all_mods))
                else:
                    fixable_remaining.append((year, prog, ts, all_mods))

        fixable_resolved = len(self._fixable_c2_violations) - len(fixable_remaining)

        seen: dict = {}
        c1_clashes = 0
        for e, (r, t) in self.event_assignment.items():
            key = (r, t)
            if key in seen:
                c1_clashes += 1
            seen[key] = e

        day_counts: Counter = Counter()
        hour_counts: Counter = Counter()
        for _, t in self.event_assignment.values():
            day, hour = parse_timeslot(t)
            day_counts[day] += 1
            hour_counts[hour] += 1

        campus_counts: Counter = Counter()
        for e, (r, _) in self.event_assignment.items():
            campus_counts[self._campus.get(r, "Unknown")] += 1

        n = len(self.event_assignment)

        print("\n========== TIMETABLE KPIs ==========")
        print(f"  Total MILP events:              {n}")
        print(f"  Density cost:                   {self._density_cost:.1f}  (avg {self._density_cost/n:.2f} per event)")
        print(f"\n  C1 room clashes:                {c1_clashes}")
        print(f"\n  C2 summary:")
        print(f"    Initial violations (total):   {len(self._initial_c2_violations)}")
        print(f"    -- of which fixable by SA:    {len(self._fixable_c2_violations)}")
        print(f"    -- of which unfixable:        {len(self._unfixable_c2_violations)}")
        print(f"    Fixable violations resolved:  {fixable_resolved} of {len(self._fixable_c2_violations)}")
        print(f"    Fixable violations remaining: {len(fixable_remaining)}")
        if fixable_remaining:
            print(f"    Unresolved fixable clashes:")
            for year, prog, ts, mods in sorted(fixable_remaining):
                print(f"      ({year}, {prog}, {ts}): {sorted(mods)}")

        print(f"\n  Events by day:")
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
            print(f"    {day:<12} {day_counts.get(day, 0):>4}")
        print(f"\n  Events by hour:")
        for hour in ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"]:
            print(f"    {hour}   {hour_counts.get(hour, 0):>4}")
        print(f"\n  Events by campus:")
        for campus, count in sorted(campus_counts.items()):
            print(f"    {campus:<20} {count:>4}")
        print("=====================================\n")

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def get_solution(self) -> pd.DataFrame:
        """Return DataFrame preserving all input columns."""
        sets = self._sets
        rows = []

        for e, (r, t) in self.event_assignment.items():
            row = dict(self._input_meta.get(e, {}))
            row["Event ID"] = e
            row["Room"] = r
            row["Timeslot"] = t
            row["Room Capacity"] = sets.room_cap.get(r)
            row["Source"] = "milp"
            rows.append(row)

        for e, assignments in sets.fixed_vet.items():
            for r, t in assignments:
                row = dict(self._input_meta.get(e, {}))
                row["Event ID"] = e
                row["Room"] = r
                row["Timeslot"] = t
                row["Room Capacity"] = sets.room_cap.get(r)
                row["Source"] = "fixed_vet"
                rows.append(row)

        for e, assignments in sets.fixed_non_vet.items():
            for r, t in assignments:
                row = dict(self._input_meta.get(e, {}))
                row["Event ID"] = e
                row["Room"] = r
                row["Timeslot"] = t
                row["Room Capacity"] = sets.room_cap.get(r)
                row["Source"] = "fixed_non_vet"
                rows.append(row)

        first_event = next(iter(self._input_meta))
        input_cols = list(self._input_meta[first_event].keys())
        col_order = ["Event ID"] + [c for c in input_cols if c != "Event ID"]
        return pd.DataFrame(rows, columns=col_order)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="SA post-processor for the vet school timetable."
    )
    parser.add_argument("--start-week", type=int, default=9)
    parser.add_argument("--input",      type=str, default=None)
    parser.add_argument("--t0",         type=float, default=20.0)
    parser.add_argument("--t-final",    type=float, default=0.01)
    parser.add_argument("--cooling",    type=float, default=0.9999)
    parser.add_argument("--max-iter",   type=int,   default=200_000)
    parser.add_argument("--seed",       type=int,   default=42)
    args = parser.parse_args()

    start = args.start_week
    end   = start + 1
    tag   = f"weeks_{start}_{end}"

    input_path = Path(args.input) if args.input else OUT_DIR / f"solution_{tag}.xlsx"
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"\n=== Phase 1: Loading constraint data (weeks {start}-{end}) ===")
    sets = build_sets(DATA_DIR, {start, end})

    print(f"\n=== Phase 2: Loading initial solution from {input_path} ===")
    initial_df = pd.read_excel(input_path)
    milp_count = int((initial_df["Source"] == "milp").sum())
    print(f"  Total rows: {len(initial_df):,}  MILP rows: {milp_count:,}")

    improver = TimetableImprover(sets, initial_df)
    print(f"  Candidate pool built for {len(improver.milp_events)} MILP events")
    print(f"  Initial density cost: {improver._density_cost:.1f}")
    print(f"  Initial total cost:   {improver._total_cost_incremental():.1f}")

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

    print("\n=== Validating solution ===")
    improver.validate_solution()

    improver.print_kpis()

    sol = improver.get_solution()

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