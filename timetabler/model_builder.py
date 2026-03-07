"""model_builder.py — MILP model construction for the vet school timetabler.

ModelBuilder takes a TimetablerSets and builds an Xpress MILP problem with:
  C1 — room conflict:    at most one event per (room, timeslot)
  C2 — core-class clash: at most one compulsory class per (year, timeslot)
  C3 — assignment:       each free event assigned to exactly one (room, timeslot)
"""

from typing import Any

import xpress as xp

from .data_prep import TimetablerSets


class ModelBuilder:
    """Builds the Xpress MILP for the given TimetablerSets."""

    def __init__(self, sets: TimetablerSets):
        self._sets = sets

        # State populated by build()
        self.model: Any = None
        self.x: dict = {}           # {(e, r, t): xp.var}
        self._var_keys: list = []   # [(e, r, t), ...] in creation order
        self._var_list: list = []   # [xp.var, ...]    in creation order
        self._t_idx: dict = {}      # {timeslot: int} compact index for var names
        self._Z: dict = {}          # {(module, timeslot): xp.var}

    def build(
        self,
        disable_c1: bool = False,
        disable_c2: bool = False,
        disable_c2_force: bool = False,
        quiet: bool = False,
    ) -> "ModelBuilder":
        """Build MILP and return self for chaining."""
        s = self._sets

        # Reset model state so build() can be called multiple times
        self.x = {}
        self._var_keys = []
        self._var_list = []
        self._Z = {}

        self.model = xp.problem()
        self.model.setLogFile("")
        if quiet:
            self.model.controls.outputlog = 0

        available_rt = [
            (r, t)
            for r in s.R
            for t in s.T
            if s.locked_occupancy.get((r, t), 0) == 0
        ]

        e_idx = {e: i for i, e in enumerate(s.E)}
        r_idx = {r: i for i, r in enumerate(s.R)}
        self._t_idx = {t: i for i, t in enumerate(s.T)}

        self._add_variables(available_rt, e_idx, r_idx, quiet)

        if not disable_c1:
            self._add_c1_room_conflict(quiet)
        elif not quiet:
            print("  C1 (room conflict):      disabled")

        if not disable_c2:
            self._add_c2_core_class_conflict(disable_c2_force, quiet)
        elif not quiet:
            print("  C2 (core-class conflict): disabled")

        self._add_c3_assignment(quiet)

        self.model.setObjective(0, sense=xp.minimize)
        if not quiet:
            print("Model built.")

        return self

    def _add_variables(self, available_rt: list, e_idx: dict, r_idx: dict, quiet: bool):
        """Create binary decision variables x[e, r, t] ∈ {0, 1}."""
        s = self._sets
        assert self.model is not None
        if not quiet:
            print(
                f"Creating variables: {len(s.E):,} events × "
                f"{len(available_rt):,} available (r,t) pairs..."
            )
        self.x = {
            (e, r, t): self.model.addVariable(
                vartype=xp.binary,
                name=f"x{e_idx[e]}_{r_idx[r]}_{self._t_idx[t]}",
            )
            for e in s.E
            for r, t in available_rt
        }
        self._var_keys = list(self.x.keys())
        self._var_list = list(self.x.values())
        if not quiet:
            print(f"  Created {len(self.x):,} binary variables")

    def _add_c1_room_conflict(self, quiet: bool) -> int:
        """Add C1 constraints: at most one event per (room, timeslot).

        ∑_E x[e,r,t] ≤ 1  ∀ r∈R, t∈T
        """
        s = self._sets
        c1 = 0
        for r in s.R:
            for t in s.T:
                if s.locked_occupancy.get((r, t), 0) > 0:
                    continue
                vars_rt = [self.x[(e, r, t)] for e in s.E if (e, r, t) in self.x]
                if len(vars_rt) > 1:
                    self.model.addConstraint(xp.Sum(vars_rt) <= 1)
                    c1 += 1
        if not quiet:
            print(f"  C1 (room conflict):      {c1:,}")
        return c1

    def _add_c2_core_class_conflict(self, disable_c2_force: bool, quiet: bool) -> int:
        """Add C2 constraints: at most one compulsory class per (year, timeslot).

        Parallel sections of the same module may share a slot.  Z_{C,T} ∈ {0,1}
        indicates whether module C has any event scheduled at timeslot T.

          Linking:    Z_{C,T} ≥ x[e,r,t]      ∀ e∈E_C, r∈R
          Year-level: ∑_{C ∈ CoreClasses(Y)} Z_{C,T} ≤ 1   ∀ Y, T
        """
        s = self._sets
        c2 = 0
        all_core_modules = {m for mods in s.core_modules_Y.values() for m in mods}
        m_idx = {m: i for i, m in enumerate(sorted(all_core_modules))}

        free_module_events = {}
        for m in all_core_modules:
            evs = [e for e in s.E if s.event_module.get(e) == m]
            if evs:
                free_module_events[m] = evs

        self._Z = {}
        for m, evs in free_module_events.items():
            mi = m_idx[m]
            for t in s.T:
                vars_mt = [
                    self.x[(e, r, t)]
                    for e in evs
                    for r in s.R
                    if (e, r, t) in self.x
                ]
                if not vars_mt:
                    continue
                z_var = self.model.addVariable(
                    vartype=xp.binary, name=f"z{mi}_{self._t_idx[t]}"
                )
                self._Z[(m, t)] = z_var
                self.model.addConstraint([z_var >= v for v in vars_mt])
                c2 += len(vars_mt)

        for year, modules in s.core_modules_Y.items():
            for t in s.T:
                locked_cls = s.locked_core_classes.get((year, t), set())
                n_locked = len(locked_cls)
                z_vars_yt = [
                    self._Z[(m, t)]
                    for m in modules
                    if (m, t) in self._Z and m not in locked_cls
                ]
                rhs = 1 - n_locked
                if rhs <= 0:
                    if not disable_c2_force:
                        self.model.addConstraint([z_v <= 0 for z_v in z_vars_yt])
                        c2 += len(z_vars_yt)
                elif z_vars_yt:
                    self.model.addConstraint(xp.Sum(z_vars_yt) <= rhs)  # type: ignore[operator]
                    c2 += 1

        if not quiet:
            print(f"  C2 (core-class conflict): {c2:,}")
        return c2

    def _add_c3_assignment(self, quiet: bool) -> int:
        """Add C3 constraints: each free event assigned to exactly one (room, timeslot).

        ∑_R ∑_T x[e,r,t] == 1  ∀ e∈E
        """
        s = self._sets
        c3 = 0
        for e in s.E:
            vars_e = [
                self.x[(e, r, t)] for r in s.R for t in s.T if (e, r, t) in self.x
            ]
            if vars_e:
                self.model.addConstraint(xp.Sum(vars_e) == 1)  # type: ignore[operator]
                c3 += 1
            else:
                s.warnings.append(
                    f"Event {e} has no feasible (Room, Timeslot) — model will be infeasible"
                )
        if not quiet:
            print(f"  C3 (assignment):         {c3:,}")
        return c3
