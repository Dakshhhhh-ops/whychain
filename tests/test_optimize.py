"""The Optiagent-backed optimizer on the generated data (no LLM involved)."""
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
