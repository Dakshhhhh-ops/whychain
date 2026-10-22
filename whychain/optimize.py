"""Optimal stock rebalancing, solved and proven optimal by the Optiagent engine.

Optiagent (github.com/Dakshhhhh-ops/Optiagent, MIT) is a declared pre-existing component: its
`optiagent_core` builds the linear program, solves it, re-checks the plan against the original data
and attaches a duality certificate that proves the plan optimal.

Following Optiagent's rule, the agent only chooses the SCOPE (which SKUs, which DCs, how soon).
Every number in the model comes from the company's data through the data harness:

  need    units on backordered lines due within the deadline, per DC and SKU          (ERP)
  spare   on hand - already allocated - policy cover (days x baseline demand)         (WMS)
  lanes   inter-DC truck cost per unit and transit days                               (WMS)
  air     supplier air-freight surcharge and lead time, per SKU category              (procurement)
  unfilled  a unit that can't be covered in time costs its list price (lost sale)     (ERP)

The model is a transportation problem: sources are (DC, SKU) spare stock, air freight and
"unfilled"; destinations are (DC, SKU) needs; only same-SKU routes that arrive before the deadline
exist. Minimising cost gives the cheapest way to release the backorders.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

from whychain.harness.base import DataHarness

SUPPLIER = re.compile(r"^S-\d{2}$")
SKU = re.compile(r"^[A-Z]{3}-\d{4}$")
CATEGORY = re.compile(r"^[A-Za-z &]{3,40}$")
DC = re.compile(r"^[A-Z]{3}$")


class OptimizeError(ValueError):
    pass


def _list(v) -> list[str]:
    if v is None:
        return []
    return [str(x).strip() for x in (v if isinstance(v, (list, tuple)) else [v]) if str(x).strip()]


def _in(col: str, values: list[str], pattern: re.Pattern, what: str) -> str:
    bad = [v for v in values if not pattern.match(v)]
    if bad:
        raise OptimizeError(f"invalid {what}: {bad}")
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def scope_where(scope: dict) -> str:
    conds = []
    if s := _list(scope.get("supplier_id") or scope.get("supplier_ids")):
        conds.append(_in("s.supplier_id", s, SUPPLIER, "supplier_id"))
    if c := _list(scope.get("category") or scope.get("categories")):
        conds.append(_in("s.category", c, CATEGORY, "category"))
    if k := _list(scope.get("skus") or scope.get("sku")):
        conds.append(_in("s.sku", k, SKU, "sku"))
    if not conds:
        raise OptimizeError('scope needs at least one of "supplier_id", "category" or "skus"')
    return " AND ".join(conds)


def _rows(h: DataHarness, sql: str) -> list[dict]:
    r = h.query(sql, limit=5000)
    if not r.ok:
        raise OptimizeError(f"data query failed: {r.error}")
    return [dict(zip(r.columns, row)) for row in r.rows]


def build_inputs(h: DataHarness, scope: dict, to_warehouses=None, deadline_days: int = 7, cover_days: int = 14) -> dict:
    w = scope_where(scope)
    AS_OF = getattr(h, "as_of", None) or "2026-10-21"
    dcs = _list(to_warehouses)
    dc_filter = f" AND {_in('o.warehouse_id', dcs, DC, 'warehouse')}" if dcs else ""
    deadline_days, cover_days = int(deadline_days), int(cover_days)
    q = {
        "need": f"""SELECT o.warehouse_id, ol.sku, SUM(ol.backordered_qty) AS units FROM erp.order_lines ol
            JOIN erp.sales_orders o ON o.order_id = ol.order_id JOIN erp.skus s ON s.sku = ol.sku
            WHERE o.status = 'BACKORDERED' AND ol.backordered_qty > 0 AND {w}
              AND CAST(o.promised_date AS DATE) <= date_add(DATE'{AS_OF}', {deadline_days}){dc_filter}
            GROUP BY o.warehouse_id, ol.sku""",
        "spare": f"""SELECT i.warehouse_id, i.sku, i.on_hand, COALESCE(a.allocated, 0) AS allocated,
              CAST(CEIL(r.avg_daily_demand_baseline * {cover_days}) AS INT) AS cover
            FROM wms.inventory_snapshots i JOIN erp.skus s ON s.sku = i.sku
            JOIN wms.reorder_policy r ON r.sku = i.sku AND r.warehouse_id = i.warehouse_id
            LEFT JOIN (SELECT o.warehouse_id, ol.sku, SUM(ol.allocated_qty) AS allocated FROM erp.order_lines ol
                       JOIN erp.sales_orders o ON o.order_id = ol.order_id
                       WHERE o.status IN ('OPEN', 'BACKORDERED') GROUP BY o.warehouse_id, ol.sku) a
              ON a.warehouse_id = i.warehouse_id AND a.sku = i.sku
            WHERE CAST(i.snapshot_date AS DATE) = DATE'{AS_OF}' AND {w}""",
        "lanes": "SELECT from_warehouse, to_warehouse, transit_days, cost_per_truck_inr, units_per_truck FROM wms.transfer_lanes",
        "air": f"""SELECT s.sku, e.arrive_warehouse, e.lead_days, e.surcharge_inr_per_unit, e.min_units
            FROM erp.skus s JOIN procurement.expedite_options e ON e.supplier_id = s.supplier_id AND e.category = s.category
            WHERE e.mode = 'AIR' AND {w}""",
        "skus": f"SELECT s.sku, s.name, s.list_price FROM erp.skus s WHERE {w}",
    }
    data = {k: _rows(h, " ".join(v.split())) for k, v in q.items()}
    return {"queries": {k: " ".join(v.split()) for k, v in q.items()}, "data": data, "deadline_days": deadline_days,
            "cover_days": cover_days, "to_warehouses": dcs, "scope": scope}


MIN_TRUCK_FILL = 0.15  # a lane carrying less than this share of a truck isn't worth a truck: drop it and re-solve


def _certify(problem, rep):
    """Optiagent's verdict, plus one fallback: if ONLY the optimality certificate failed (the first solver's
    dual prices were valid but loose, as happens on degenerate problems), re-run Optiagent's own certificate
    with the second solver's (HiGHS) dual prices. Weak duality means the proof doesn't trust either solver."""
    from optiagent_core.pipeline import interpret
    from optiagent_core.plancheck import check_optimality

    checks = list(rep.verification.checks)
    if rep.verified:
        return True, rep.solution, checks, "OR-Tools GLOP dual prices"
    failed = [c.name for c in checks if not c.passed]
    sens = rep.sensitivity
    if failed == ["plan_optimality_certificate"] and rep.result.has_solution and sens is not None and sens.available:
        prices = {c.name: c.shadow_price for c in sens.constraints}
        alt = check_optimality(problem, rep.result.values, prices)
        if alt.passed:
            checks = [c for c in checks if c.name != "plan_optimality_certificate"] + [alt]
            return True, interpret(problem, rep.result.values), checks, "HiGHS dual prices (second solver)"
    return False, None, checks, None


def optimize_rebalance(h: DataHarness, scope: dict, to_warehouses=None, deadline_days: int = 7,
                       cover_days: int = 14) -> dict:
    inp = build_inputs(h, scope, to_warehouses, deadline_days, cover_days)
    excluded: set[tuple[str, str]] = set()
    notes: list[str] = []
    for _ in range(3):
        res = _solve(inp, excluded)
        small = [t for t in res.get("summary", {}).get("trucks", []) if t["units"] < MIN_TRUCK_FILL * t["capacity"]]
        if not res.get("verified") or not small:
            break
        for t in small:
            excluded.add((t["from"], t["to"]))
            notes.append(f"Dropped lane {t['lane']}: {t['units']} units would fill only "
                         f"{t['units'] / t['capacity']:.0%} of a truck (INR {t['cost_inr']:,.0f}); re-solved without it.")
    res["notes"] = notes
    return res


def _solve_with_analysis(problem, solve_problem):
    """Solve with sensitivity analysis (HiGHS runs in a worker process). If that worker breaks, reset it and
    retry once; if it still fails, solve without the analysis (the plan is still verified by the first solver)."""
    from concurrent.futures.process import BrokenProcessPool

    import optiagent_core.analysis as analysis

    for _ in range(2):
        try:
            return solve_problem(problem, analyze=True)
        except (BrokenProcessPool, OSError, RuntimeError):
            analysis._pool = None  # Optiagent creates a fresh worker pool on the next call
    return solve_problem(problem, analyze=False)


def _solve(inp: dict, excluded: set[tuple[str, str]]) -> dict:
    from optiagent_core.pipeline import solve_problem
    from optiagent_core.schema import Destination, Route, Source, TransportationProblem

    scope = inp["scope"]
    d = inp["data"]
    need = {(r["warehouse_id"], r["sku"]): float(r["units"]) for r in d["need"] if float(r["units"] or 0) > 0}
    if not need:
        return {"outcome": "nothing_to_do", "verified": True, "certificate": False, "certified_with": None,
                "message": "No backordered units in scope within the deadline.", "checks": [], "stats": {},
                "summary": {"need_units": 0, "skus": 0, "trucks": []}, "insights": [], "scope": scope,
                "queries": inp["queries"], "columns": [], "rows": [], "needs": [], "spares": [],
                "deadline_days": inp["deadline_days"], "cover_days": inp["cover_days"], "to_warehouses": inp["to_warehouses"]}
    spare = {}
    for r in d["spare"]:
        units = float(r["on_hand"]) - float(r["allocated"]) - float(r["cover"])
        if units > 0 and (r["warehouse_id"], r["sku"]) not in need:
            spare[(r["warehouse_id"], r["sku"])] = math.floor(units)
    lanes = {(r["from_warehouse"], r["to_warehouse"]): r for r in d["lanes"]}
    air = {r["sku"]: r for r in d["air"]}
    info = {r["sku"]: r for r in d["skus"]}
    deadline = inp["deadline_days"]

    sources, destinations, routes, meta = [], [], [], {}
    need_by_sku = defaultdict(float)
    for (dc, sku), units in need.items():
        destinations.append(Destination(id=f"{dc}:{sku}", demand=units))
        need_by_sku[sku] += units
    for (dc, sku), units in spare.items():
        if sku in need_by_sku:
            sources.append(Source(id=f"{dc}:{sku}", supply=units))
    for sku, total in need_by_sku.items():
        if sku in air:
            sources.append(Source(id=f"AIR:{sku}", supply=total))
        sources.append(Source(id=f"UNFILLED:{sku}", supply=total))

    def add(src: str, dst: str, cost: float, mode: str, days: int | None, via: str):
        routes.append(Route(source=src, destination=dst, cost=round(cost, 4)))
        meta[(src, dst)] = {"mode": mode, "days": days, "via": via}

    for (to_dc, sku) in need:
        dst = f"{to_dc}:{sku}"
        for (from_dc, s_sku) in spare:
            lane = lanes.get((from_dc, to_dc))
            if s_sku == sku and lane and int(lane["transit_days"]) <= deadline and (from_dc, to_dc) not in excluded:
                add(f"{from_dc}:{sku}", dst, float(lane["cost_per_truck_inr"]) / float(lane["units_per_truck"]),
                    "truck", int(lane["transit_days"]), f"{from_dc}→{to_dc}")
        if sku in air:
            a = air[sku]
            hub = a["arrive_warehouse"]
            onward = lanes.get((hub, to_dc))
            days = int(a["lead_days"]) + (0 if hub == to_dc else int(onward["transit_days"]) if onward else 99)
            if days <= deadline:
                cost = float(a["surcharge_inr_per_unit"]) + (
                    0 if hub == to_dc else float(onward["cost_per_truck_inr"]) / float(onward["units_per_truck"]))
                add(f"AIR:{sku}", dst, cost, "air", days, f"air→{hub}" + ("" if hub == to_dc else f"→{to_dc}"))
        add(f"UNFILLED:{sku}", dst, float(info.get(sku, {}).get("list_price") or 1000), "unfilled", None, "not covered in time")

    problem = TransportationProblem(name="Stock rebalancing", sources=sources, destinations=destinations,
                                    routes=routes, demand_constraint="equal")
    rep = _solve_with_analysis(problem, solve_problem)
    verified, solution, check_objs, cert_source = _certify(problem, rep)

    rows, lane_units, air_units, air_cost, unfilled = [], defaultdict(float), 0.0, 0.0, defaultdict(float)
    if solution is not None:
        for s in solution.shipments:
            m = meta[(s.source, s.destination)]
            to_dc, sku = s.destination.split(":", 1)
            from_dc = s.source.split(":", 1)[0]
            rows.append([sku, info.get(sku, {}).get("name"), m["mode"], m["via"], to_dc, round(s.quantity),
                         round(s.unit_cost, 2), round(s.cost), m["days"]])
            if m["mode"] == "truck":
                lane_units[(from_dc, to_dc)] += s.quantity
            elif m["mode"] == "air":
                air_units += s.quantity
                air_cost += s.cost
            else:
                unfilled[to_dc] += s.quantity
    rows.sort(key=lambda r: ({"truck": 0, "air": 1, "unfilled": 2}[r[2]], r[3], -r[5]))
    trucks = []
    for (a, b), units in lane_units.items():
        lane = lanes[(a, b)]
        n = math.ceil(units / float(lane["units_per_truck"]))
        trucks.append({"lane": f"{a}→{b}", "from": a, "to": b, "units": round(units), "trucks": n,
                       "capacity": float(lane["units_per_truck"]),
                       "cost_inr": n * float(lane["cost_per_truck_inr"]), "transit_days": int(lane["transit_days"])})
    total_need = sum(need.values())
    checks = [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in check_objs]
    if not verified:
        return {"outcome": "not_verified", "verified": False, "certificate": False, "checks": checks,
                "stats": rep.stats.model_dump(), "summary": {"need_units": round(total_need), "skus": len(need_by_sku),
                                                              "trucks": []},
                "columns": [], "rows": [], "insights": [], "queries": inp["queries"], "deadline_days": deadline,
                "cover_days": inp["cover_days"], "scope": scope, "to_warehouses": inp["to_warehouses"],
                "needs": [[dc, sku, round(u)] for (dc, sku), u in sorted(need.items())], "spares": [],
                "certified_with": None}
    summary = {
        "skus": len(need_by_sku), "need_units": round(total_need),
        "truck_units": round(sum(lane_units.values())), "trucks": trucks,
        "truck_cost_inr": sum(t["cost_inr"] for t in trucks),
        "air_units": round(air_units), "air_cost_inr": round(air_cost),
        "unfilled_units": round(sum(unfilled.values())), "unfilled_by_dc": {k: round(v) for k, v in unfilled.items()},
        "covered_share": round(1 - sum(unfilled.values()) / total_need, 3) if total_need else 1.0,
    }
    return {
        "outcome": "verified_optimal", "verified": True, "objective_inr": round(rep.result.objective or 0),
        "certificate": next((c["passed"] for c in checks if c["name"] == "plan_optimality_certificate"), False),
        "certified_with": cert_source,
        "checks": checks, "stats": rep.stats.model_dump(), "summary": summary,
        "columns": ["sku", "name", "mode", "route", "to_dc", "units", "unit_cost_inr", "cost_inr", "arrives_in_days"],
        "rows": rows, "insights": [i.text for i in rep.insights[:6]],
        "queries": inp["queries"], "deadline_days": deadline, "cover_days": inp["cover_days"],
        "scope": scope, "to_warehouses": inp["to_warehouses"],
        "needs": [[dc, sku, round(u)] for (dc, sku), u in sorted(need.items())],
        "spares": [[dc, sku, u] for (dc, sku), u in sorted(spare.items())],
    }


def describe(res: dict) -> str:
    """Short text for an agent's transcript."""
    if res.get("outcome") == "nothing_to_do":
        return res["message"]
    if not res.get("verified"):
        failed = [c["name"] for c in res.get("checks", []) if not c["passed"]]
        return (f"Outcome: NOT VERIFIED (failed checks: {failed}). No plan is shown, because an unverified plan could be "
                f"wrong. Narrow the scope (fewer SKUs or DCs) or change the deadline and try again.")
    s = res["summary"]
    lines = [f"Outcome: {res['outcome']} (verified={res['verified']}, optimality proven with {res['certified_with']}). "
             f"Model: {res['stats']['variables']} variables, {res['stats']['constraints']} constraints.",
             f"Need: {s['need_units']:,} backordered units across {s['skus']} SKUs due within {res['deadline_days']} days.",
             f"Plan: {s['truck_units']:,} units by truck "
             + ", ".join(f"{t['lane']} {t['units']:,} units = {t['trucks']} truck(s) INR {t['cost_inr']:,.0f}" for t in s["trucks"])
             + f"; {s['air_units']:,} units by air (INR {s['air_cost_inr']:,}); {s['unfilled_units']:,} units can't be covered in time "
             f"{s['unfilled_by_dc'] or ''}. Covers {s['covered_share']:.0%} of the need."]
    if res["rows"]:
        lines.append("sku | mode | route | to_dc | units | cost_inr")
        lines += [f"{r[0]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[7]}" for r in res["rows"][:15]]
    for n in res.get("notes", []):
        lines.append(n)
    if res["insights"]:
        lines.append("Sensitivity: " + " ".join(res["insights"][:3]))
    return "\n".join(lines)
