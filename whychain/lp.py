"""A small transportation linear program: build it from data, solve it, check the plan and prove it optimal.

    minimize    sum_r cost_r * x_r
    subject to  sum of x_r arriving at destination d  = demand_d     (every destination)
                sum of x_r leaving source s            <= supply_s    (every source)
                x_r >= 0

The model never comes from a language model: callers pass sources, destinations and routes read from data.

Every plan is checked before it is used: the solver's status, bounds, integrality, supply and demand, the objective
recomputed from the data, and a **duality certificate**. With the dual prices y_d (destinations) and u_s <= 0
(sources), every route must satisfy cost_r - y_d - u_s >= 0, and the dual objective
sum(y_d * demand_d) + sum(u_s * supply_s) must equal the plan's cost. By weak duality no feasible plan can cost less
than that dual objective, so the plan is optimal. The proof uses only the data and the prices: it doesn't trust the
solver. OR-Tools GLOP solves first; if its dual prices don't close the proof (they can be loose on degenerate
problems), HiGHS solves again and its prices are tried.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

TOL = 1e-6


@dataclass
class Shipment:
    source: str
    destination: str
    quantity: float
    unit_cost: float

    @property
    def cost(self) -> float:
        return self.quantity * self.unit_cost


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Result:
    verified: bool                 # every check passed, including the optimality certificate
    objective: float | None
    shipments: list[Shipment]
    checks: list[Check]
    certified_with: str | None
    stats: dict
    insights: list[str] = field(default_factory=list)


class Transport:
    """sources {id: supply}, destinations {id: demand}, routes [(source, destination, unit cost)]."""

    def __init__(self, sources: dict[str, float], destinations: dict[str, float],
                 routes: list[tuple[str, str, float]]):
        self.sources = dict(sources)
        self.destinations = dict(destinations)
        self.routes = [(s, d, float(c)) for s, d, c in routes]

    def stats(self) -> dict:
        return {"variables": len(self.routes), "constraints": len(self.sources) + len(self.destinations),
                "nonzeros": 2 * len(self.routes), "is_mip": False}

    # ------------------------------------------------------------------ solvers
    def solve_glop(self, demand=None, supply=None):
        """(status ok, x per route, objective, destination prices, source prices) with OR-Tools GLOP."""
        from ortools.linear_solver import pywraplp

        demand, supply = demand or self.destinations, supply or self.sources
        m = pywraplp.Solver.CreateSolver("GLOP")
        x = [m.NumVar(0.0, m.infinity(), f"x{i}") for i in range(len(self.routes))]
        dst = {d: m.Constraint(v, v, f"d:{d}") for d, v in demand.items()}
        src = {s: m.Constraint(-m.infinity(), v, f"s:{s}") for s, v in supply.items()}
        for i, (s, d, _) in enumerate(self.routes):
            dst[d].SetCoefficient(x[i], 1.0)
            src[s].SetCoefficient(x[i], 1.0)
        obj = m.Objective()
        for i, (_, _, c) in enumerate(self.routes):
            obj.SetCoefficient(x[i], c)
        obj.SetMinimization()
        ok = m.Solve() == pywraplp.Solver.OPTIMAL
        if not ok:
            return False, None, None, None, None
        return (True, [v.solution_value() for v in x], obj.Value(),
                {d: c.dual_value() for d, c in dst.items()}, {s: c.dual_value() for s, c in src.items()})

    def solve_highs(self):
        """The same model with HiGHS, for a second set of dual prices."""
        import highspy
        import numpy as np

        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        dests, srcs = list(self.destinations), list(self.sources)
        row = {("d", d): i for i, d in enumerate(dests)} | {("s", s): len(dests) + i for i, s in enumerate(srcs)}
        lp = highspy.HighsLp()
        n = len(self.routes)
        lp.num_col_, lp.num_row_ = n, len(row)
        lp.col_cost_ = np.array([c for _, _, c in self.routes], dtype=float)
        lp.col_lower_ = np.zeros(n)
        lp.col_upper_ = np.full(n, highspy.kHighsInf)
        lp.row_lower_ = np.array([self.destinations[d] for d in dests] + [-highspy.kHighsInf] * len(srcs))
        lp.row_upper_ = np.array([self.destinations[d] for d in dests] + [self.sources[s] for s in srcs])
        starts, index, value = [], [], []
        for s, d, _ in self.routes:
            starts.append(len(index))
            index += [row[("d", d)], row[("s", s)]]
            value += [1.0, 1.0]
        lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        lp.a_matrix_.start_ = np.array(starts + [len(index)], dtype=np.int32)
        lp.a_matrix_.index_ = np.array(index, dtype=np.int32)
        lp.a_matrix_.value_ = np.array(value, dtype=float)
        h.passModel(lp)
        h.run()
        if h.getModelStatus() != highspy.HighsModelStatus.kOptimal:
            return False, None, None, None, None
        sol = h.getSolution()
        duals = list(sol.row_dual)
        return (True, list(sol.col_value), h.getInfo().objective_function_value,
                {d: duals[row[("d", d)]] for d in dests}, {s: duals[row[("s", s)]] for s in srcs})

    # ------------------------------------------------------------------ checks
    def certificate(self, x: list[float], y: dict, u: dict) -> Check:
        cost = sum(c * q for (_, _, c), q in zip(self.routes, x))
        scale = max(1.0, abs(cost))
        worst = min((c - y.get(d, 0.0) - u.get(s, 0.0) for s, d, c in self.routes), default=0.0)
        bad_u = max((v for v in u.values()), default=0.0)
        dual_obj = sum(y[d] * v for d, v in self.destinations.items()) + sum(u[s] * v for s, v in self.sources.items())
        gap = abs(cost - dual_obj)
        ok = worst >= -1e-6 * scale and bad_u <= 1e-6 * scale and gap <= 1e-6 * scale
        return Check("plan_optimality_certificate", ok,
                     f"dual objective {dual_obj:,.2f} vs plan cost {cost:,.2f} (gap {gap:.2g}); "
                     f"smallest reduced cost {worst:.3g}; largest source price {bad_u:.3g}")

    def checks(self, ok: bool, x: list[float] | None, objective: float | None) -> list[Check]:
        if not ok or x is None:
            return [Check("solver_status", False, "the solver did not report an optimal solution")]
        out = [Check("solver_status", True, "optimal"),
               Check("all_variables_reported", len(x) == len(self.routes), f"{len(x)} of {len(self.routes)} values")]
        out.append(Check("plan_values_finite", all(math.isfinite(v) for v in x), "every value is a finite number"))
        out.append(Check("variable_bounds", all(v >= -TOL for v in x), "no route carries a negative quantity"))
        out.append(Check("integrality", all(abs(v - round(v)) <= 1e-6 for v in x),
                         "whole units (supplies and demands are whole, so an optimal corner is too)"))
        allowed = {(s, d) for s, d, _ in self.routes}
        out.append(Check("plan_covers_allowed_pairs_only",
                         all((s, d) in allowed and s in self.sources and d in self.destinations
                             for s, d, _ in self.routes), "every shipment uses a route built from the data"))
        sent: dict[str, float] = {}
        got: dict[str, float] = {}
        for (s, d, _), q in zip(self.routes, x):
            sent[s] = sent.get(s, 0.0) + q
            got[d] = got.get(d, 0.0) + q
        over = [s for s, v in self.sources.items() if sent.get(s, 0.0) > v + 1e-6]
        out.append(Check("plan_supply_respected", not over, "no source sends more than it has" if not over
                         else f"over supply: {over[:5]}"))
        short = [d for d, v in self.destinations.items() if abs(got.get(d, 0.0) - v) > 1e-6]
        out.append(Check("plan_demand_met", not short, "every destination receives exactly its need" if not short
                         else f"not met: {short[:5]}"))
        recomputed = sum(c * q for (_, _, c), q in zip(self.routes, x))
        out.append(Check("plan_objective_from_data", abs(recomputed - (objective or 0.0)) <= 1e-6 * max(1.0, abs(recomputed)),
                         f"cost recomputed from the data: {recomputed:,.2f}"))
        return out

    # ------------------------------------------------------------------ sensitivity
    def insights(self, objective: float, y: dict, u: dict, limit: int = 6) -> list[str]:
        """What one unit more or less of a binding demand or supply would do, by re-solving (so the two directions
        can differ when the plan sits on a corner)."""
        cands = [("demand", d, abs(v)) for d, v in y.items() if abs(v) > 1e-9] + \
                [("supply", s, abs(v)) for s, v in u.items() if abs(v) > 1e-9]
        cands.sort(key=lambda c: -c[2])
        out = []
        for kind, key, _ in cands[:limit]:
            deltas = []
            for step in (1.0, -1.0):
                dem, sup = dict(self.destinations), dict(self.sources)
                (dem if kind == "demand" else sup)[key] += step
                if (dem if kind == "demand" else sup)[key] < 0:
                    deltas.append(None)
                    continue
                ok, _, obj, _, _ = self.solve_glop(dem, sup)
                deltas.append(obj - objective if ok else None)
            more, less = deltas
            what = f"demand at {key}" if kind == "demand" else f"supply {key}"
            parts = [_effect("One more unit of " + what, more), _effect("one less", less)]
            text = "; ".join(p for p in parts if p) + "."
            if more is not None and less is not None and abs(more + less) > 1e-6 * max(1.0, abs(objective)):
                text += " The effect differs by direction because the optimal plan sits on a corner."
            out.append(text)
        return out

    # ------------------------------------------------------------------ all together
    def solve(self) -> Result:
        ok, x, obj, y, u = self.solve_glop()
        checks = self.checks(ok, x, obj)
        certified_with = None
        if ok:
            cert = self.certificate(x, y, u)
            certified_with = "OR-Tools GLOP dual prices" if cert.passed else None
            if not cert.passed:  # loose prices on a degenerate problem: HiGHS solves again
                ok2, x2, obj2, y2, u2 = self.solve_highs()
                if ok2:
                    alt = self.certificate(x, y2, u2)
                    if alt.passed:
                        cert, certified_with = alt, "HiGHS dual prices (second solver)"
                    else:  # HiGHS's own plan and prices
                        alt2 = self.certificate(x2, y2, u2)
                        if alt2.passed and abs(obj2 - obj) <= 1e-6 * max(1.0, abs(obj)):
                            x, obj, y, u = x2, obj2, y2, u2
                            checks = self.checks(True, x, obj)
                            cert, certified_with = alt2, "HiGHS (second solver)"
            checks.append(cert)
        verified = bool(checks) and all(c.passed for c in checks)
        shipments = [Shipment(s, d, round(q), c) for (s, d, c), q in zip(self.routes, x or []) if q > 1e-6] \
            if verified else []
        return Result(verified, obj if ok else None, shipments, checks, certified_with, self.stats(),
                      self.insights(obj, y, u) if verified else [])


def _effect(lead: str, delta: float | None) -> str:
    if delta is None:
        return f"{lead} is not possible"
    if abs(delta) <= 1e-9:
        return f"{lead} would not change the total cost"
    return f"{lead} would {'worsen' if delta > 0 else 'improve'} the total cost by {abs(delta):,.6g}"
