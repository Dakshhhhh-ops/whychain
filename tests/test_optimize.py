"""The stock-transfer optimizer on the generated data (no LLM involved)."""
from __future__ import annotations

import pytest

from whychain.harness.local import LocalHarness
from whychain.optimize import OptimizeError, optimize_rebalance


@pytest.fixture(scope="module")
def harness():
    return LocalHarness("kaveri")


def test_pune_voltix_plan_is_proven_optimal(harness):
    res = optimize_rebalance(harness, {"supplier_id": "S-14"}, ["PUN"], deadline_days=7)
    assert res["verified"] and res["certificate"]
    s = res["summary"]
    # Mumbai's spare Voltix stock goes to Pune by truck; whatever Mumbai can't spare is flown in
    assert any(t["lane"] == "MUM→PUN" and t["trucks"] >= 1 for t in s["trucks"])
    assert s["unfilled_units"] == 0 and s["covered_share"] == 1.0
    assert s["truck_units"] + s["air_units"] == s["need_units"]
    assert all(r[4] == "PUN" for r in res["rows"])


def test_tight_deadline_reports_what_cannot_arrive(harness):
    res = optimize_rebalance(harness, {"supplier_id": "S-14"}, None, deadline_days=7)
    assert res["verified"]  # proven optimal, possibly with the second solver's prices
    # air freight to Chennai takes 9 days, so part of Chennai's need can't be met within 7
    assert res["summary"]["unfilled_by_dc"].get("CHN", 0) > 0


def test_near_empty_trucks_are_dropped(harness):
    res = optimize_rebalance(harness, {"category": "Earbuds"}, ["MUM"], deadline_days=7)
    assert res["verified"]
    assert all(t["units"] >= 0.15 * t["capacity"] for t in res["summary"]["trucks"])


def test_scope_is_validated(harness):
    with pytest.raises(OptimizeError):
        optimize_rebalance(harness, {"supplier_id": "S-14'; DROP TABLE x; --"}, None)
    with pytest.raises(OptimizeError):
        optimize_rebalance(harness, {}, None)


def test_both_solvers_prove_the_same_plan_optimal():
    """The certificate is checked from the data and the dual prices alone, with either solver's prices."""
    from whychain.lp import Transport

    # a degenerate little problem: two warehouses can each cover one store exactly, at equal cost
    t = Transport({"A": 5, "B": 5, "UNFILLED": 10}, {"X": 5, "Y": 5},
                  [("A", "X", 1.0), ("A", "Y", 1.0), ("B", "X", 1.0), ("B", "Y", 1.0),
                   ("UNFILLED", "X", 100.0), ("UNFILLED", "Y", 100.0)])
    ok, x, obj, y, u = t.solve_glop()
    assert ok and obj == 10.0 and t.certificate(x, y, u).passed
    ok2, x2, obj2, y2, u2 = t.solve_clp()
    assert ok2 and abs(obj2 - 10.0) < 1e-9
    assert t.certificate(x, y2, u2).passed          # GLOP's plan, proven with CLP's prices
    res = t.solve()
    assert res.verified and sum(s.quantity for s in res.shipments) == 10


def test_a_wrong_plan_fails_the_certificate():
    from whychain.lp import Transport

    t = Transport({"A": 5, "UNFILLED": 5}, {"X": 5}, [("A", "X", 1.0), ("UNFILLED", "X", 100.0)])
    ok, x, obj, y, u = t.solve_glop()
    assert t.certificate(x, y, u).passed
    assert not t.certificate([0.0, 5.0], y, u).passed  # sending everything to "unfilled" is not optimal
