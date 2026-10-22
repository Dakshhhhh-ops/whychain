"""The analysis engine on synthetic orders with planted effects: it must find exactly what was planted."""
import random
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np

from whychain.analysis.engine import Engine
from whychain.analysis.model import FactModel
from whychain.analysis.stats import shapley, shift_share

MODEL = FactModel(dataset="synthetic", sql="FACTS", delivered_status=("delivered",),
                  dims={"area": "area", "carrier": "carrier"}, map_within="area")
EVENTS_SQL_PREFIX = "SELECT event_id"


class FakeHarness:
    """Serves a synthetic fact table and an events table through the harness interface."""
    name = "fake"

    def __init__(self, orders: list[dict], events: list[dict] | None = None):
        self.orders, self.events = orders, events or []
        self.dataset = SimpleNamespace(key="synthetic")
        self.as_of = "2018-09-01"

    def query(self, sql: str, limit: int = 200):
        if sql.strip().startswith(EVENTS_SQL_PREFIX):
            cols = ["event_id", "name", "type", "start_date", "end_date", "region"]
            rows = [[e[c] for c in cols] for e in self.events]
        else:
            cols = ["order_id", "status", "purchase_ts", "handover_ts", "delivered_ts", "promised_ts", "area", "carrier"]
            rows = [[o[c] for c in cols] for o in self.orders]
        return SimpleNamespace(ok=True, rows=rows, columns=cols, error=None)


def make_orders(start: date, end: date, rnd: random.Random, per_day=40, h=(1, 3), t=(5, 9), p=(12, 14),
                area_weights=(("north", 0.5), ("south", 0.5)), shift=None, prefix="o"):
    """Orders placed every day in [start, end). `shift(day, area)` returns extra (h, t, p) days for planted effects."""
    out = []
    d = start
    i = 0
    while d < end:
        for _ in range(per_day):
            area = rnd.choices([a for a, _ in area_weights], [w for _, w in area_weights])[0]
            dh, dt, dp = shift(d, area) if shift else (0, 0, 0)
            hh = rnd.randint(*h) + dh
            tt = rnd.randint(*t) + dt
            pp = rnd.randint(*p) + dp
            out.append({"order_id": f"{prefix}{i}", "status": "delivered", "purchase_ts": d.isoformat(),
                        "handover_ts": (d + timedelta(days=hh)).isoformat(),
                        "delivered_ts": (d + timedelta(days=hh + tt)).isoformat(),
                        "promised_ts": (d + timedelta(days=pp)).isoformat(), "area": area,
                        "carrier": "fast" if area == "north" else "slow"})
            i += 1
        d += timedelta(days=1)
    return out


def engine(orders, events=None) -> Engine:
    return Engine(FakeHarness(orders, events), MODEL, "2018-09-01")


def test_shapley_adds_up_and_shift_share_adds_up():
    phi = shapley(["a", "b", "c"], lambda s: (2 if "a" in s else 0) + (3 if "b" in s else 0) + (1 if {"a", "c"} <= s else 0))
    assert abs(sum(phi.values()) - 6) < 1e-9 and phi["b"] == 3
    rnd = random.Random(1)
    e = engine(make_orders(date(2018, 1, 1), date(2018, 3, 1), rnd))
    s = e.time_scope(date(2018, 2, 1), date(2018, 3, 1))
    ss = shift_share(s.focus, s.ref, "area")
    assert abs(ss["total"].sum() - s.rise) < 1e-9


def test_slower_transit_is_attributed_to_transit():
    rnd = random.Random(2)
    shift = lambda d, a: (0, 4, 0) if d >= date(2018, 3, 1) else (0, 0, 0)   # transit +4 days from March
    e = engine(make_orders(date(2017, 9, 1), date(2018, 4, 1), rnd, shift=shift))
    s = e.time_scope(date(2018, 3, 1), date(2018, 4, 1))
    m = e.measure(s).summary
    assert m["rise_pp"] > 20
    st = e.stages(s).summary["shares"]
    assert st["transit"] > 85 and abs(st["handover"]) < 5 and abs(st["promise"]) < 5


def test_shorter_promises_are_attributed_to_the_promise():
    rnd = random.Random(3)
    shift = lambda d, a: (0, 0, -4) if d >= date(2018, 3, 1) else (0, 0, 0)
    e = engine(make_orders(date(2017, 9, 1), date(2018, 4, 1), rnd, shift=shift))
    st = e.stages(e.time_scope(date(2018, 3, 1), date(2018, 4, 1))).summary["shares"]
    assert st["promise"] > 85 and abs(st["transit"]) < 5


def test_abnormal_months_are_left_out_of_the_baseline():
    rnd = random.Random(4)
    shift = lambda d, a: (0, 5, 0) if date(2017, 11, 1) <= d < date(2017, 12, 1) or d >= date(2018, 3, 1) else (0, 0, 0)
    e = engine(make_orders(date(2017, 9, 1), date(2018, 4, 1), rnd, shift=shift))
    s = e.time_scope(date(2018, 3, 1), date(2018, 4, 1))
    assert "2017-11" in s.excluded_units and "2017-11" not in s.baseline_units


def test_concentration_by_area():
    rnd = random.Random(5)
    shift = lambda d, a: (0, 5, 0) if d >= date(2018, 3, 1) and a == "south" else (0, 0, 0)
    e = engine(make_orders(date(2017, 9, 1), date(2018, 4, 1), rnd, shift=shift))
    seg = e.segments(e.time_scope(date(2018, 3, 1), date(2018, 4, 1)), "area")
    assert seg.summary["concentrated_in"] == ["south"]


def test_event_onset_supports_a_real_trigger_and_rejects_an_earlier_one():
    rnd = random.Random(6)
    ev = [{"event_id": "strike", "name": "Strike", "type": "strike", "start_date": "2018-05-21",
           "end_date": "2018-06-01", "region": "Brazil"},
          {"event_id": "late_event", "name": "Late event", "type": "strike", "start_date": "2018-03-19",
           "end_date": "2018-03-21", "region": "Brazil"}]

    def shift(d, a):
        if date(2018, 5, 18) <= d < date(2018, 6, 1):   # transit jumps for parcels handed over with the strike
            return (0, 5, 0)
        if date(2018, 2, 15) <= d < date(2018, 4, 1):   # a slowdown that began weeks before 'late_event'
            return (0, 4, 0)
        return (0, 0, 0)

    e = engine(make_orders(date(2017, 9, 1), date(2018, 8, 1), rnd, per_day=30, shift=shift), ev)
    assert e.event("strike").verdict == "supports"
    late = e.event("late_event")
    assert late.verdict == "refutes" and late.summary["elevated_before"]


def test_a_volume_surge_is_measured_against_the_weeks_before():
    rnd = random.Random(7)
    before = make_orders(date(2017, 10, 1), date(2017, 11, 20), rnd, per_day=30)
    peak = make_orders(date(2017, 11, 20), date(2017, 11, 27), rnd, per_day=100, prefix="p",
                       shift=lambda d, a: (1, 3, 0))
    e = engine(before + peak)
    v = e.volume(e.time_scope(date(2017, 11, 20), date(2017, 11, 27)))
    assert v.verdict == "supports" and v.summary["orders_per_day_ratio"] > 3


def test_results_are_identical_on_every_run():
    rnd_a, rnd_b = random.Random(8), random.Random(8)
    shift = lambda d, a: (0, 3, -2) if d >= date(2018, 3, 1) else (0, 0, 0)
    a = engine(make_orders(date(2017, 9, 1), date(2018, 4, 1), rnd_a, shift=shift))
    b = engine(make_orders(date(2017, 9, 1), date(2018, 4, 1), rnd_b, shift=shift))
    sa = a.stages(a.time_scope(date(2018, 3, 1), date(2018, 4, 1)))
    sb = b.stages(b.time_scope(date(2018, 3, 1), date(2018, 4, 1)))
    assert sa.rows == sb.rows
    ci = sa.columns.index("contribution_pp")
    assert np.isclose(sum(r[ci] for r in sa.rows if r[ci] is not None), round(100 * sa.summary["rise_pp"] / 100, 2),
                      atol=0.05)


def test_stock_waits_explain_slower_dispatch():
    """Kaveri: dispatch slowed in Sep-Oct because orders waited for stock (the planted stock-outs: SoundWave earbuds
    on the DIWALI26 promotion, Voltix behind the port strike); Nashik's lateness is transit, not stock."""
    from datetime import date

    from whychain.analysis import get_engine
    from whychain.analysis.battery import plan_battery, run_task
    from whychain.harness.local import LocalHarness

    eng = get_engine(LocalHarness("kaveri"))
    q = "Why did our overall on-time delivery rate drop in September and October compared with August?"
    task = next(t for t in plan_battery(q, eng, date(2026, 10, 21))["tasks"] if t["tool"] == "stock_wait")
    f, ev = run_task(eng, task, "T3")
    assert f["fixed"]["status"] == "confirmed" and not f["fixed"]["is_cause"]
    assert "S-07" in f["claim"] and "S-14" in f["claim"] and "DIWALI26" in f["claim"]
    nashik = next(t for t in plan_battery("Why have deliveries to Nashik been late since September?", eng,
                                          date(2026, 10, 21))["tasks"] if t["tool"] == "stock_wait")
    f2, _ = run_task(eng, nashik, "T3")
    assert f2["fixed"]["status"] != "confirmed"
    # Olist has no stock data: the test isn't planned at all
    olist = get_engine(LocalHarness("olist"))
    assert not any(t["tool"] == "stock_wait" for t in plan_battery(
        "Why did late deliveries jump to 19% for orders placed in March 2018?", olist, date(2018, 9, 1))["tasks"])


def test_a_stage_that_did_not_move_is_not_a_cause():
    """Held-out case KH2 (Hyderabad, Sep 2026): transit was flat (typically 2 vs 2 days), yet the counterfactual on
    98 orders gave it 14.6% of the rise. A stage must itself have moved to count as a cause."""
    from datetime import date

    from whychain.analysis import get_engine
    from whychain.analysis.battery import plan_battery, run_task
    from whychain.harness.local import LocalHarness

    eng = get_engine(LocalHarness("kaveri"))
    tasks = plan_battery("Why did deliveries to Hyderabad get later in September?", eng, date(2026, 10, 21))["tasks"]
    verdict = {t["params"].get("stage"): run_task(eng, t, "T")[0]["fixed"] for t in tasks if t["tool"] == "stage"}
    assert not verdict["transit"]["is_cause"] and "barely changed" in verdict["transit"]["reason"]
    assert verdict["handover"]["is_cause"]


def test_a_stage_whose_distribution_did_not_change_is_ruled_out():
    """Small-sample noise: in March only hand-over is shifted (+2 days); transit keeps the same distribution. On 93
    focus orders the counterfactual still gives transit 11.1% of the rise. Transit must be ruled out, hand-over kept."""
    from whychain.analysis.battery import build_scope, run_task

    rnd = random.Random(33)
    base = make_orders(date(2017, 9, 1), date(2018, 3, 1), rnd, per_day=40, t=(2, 6), p=(7, 9))
    focus = make_orders(date(2018, 3, 1), date(2018, 4, 1), rnd, per_day=3, t=(2, 6), p=(7, 9),
                        shift=lambda d, a: (2, 0, 0), prefix="f")
    e = engine(base + focus)
    spec = {"mode": "time", "label": "orders placed March 2018", "start": "2018-03-01", "end": "2018-04-01"}
    res = e.stages(build_scope(e, spec))
    share, moved = res.columns.index("share_of_rise_pct"), res.columns.index("stage_moved")
    rows = {r[0]: r for r in res.rows}
    assert rows["transit"][share] >= 10 and not rows["transit"][moved]   # a noise share on an unchanged stage
    verdict = {st: run_task(e, {"tool": "stage", "statement": st, "scope": spec, "params": {"stage": st}}, "T")[0]["fixed"]
               for st in ("handover", "transit")}
    assert not verdict["transit"]["is_cause"] and verdict["handover"]["is_cause"]


def test_an_events_normal_level_leaves_out_abnormal_weeks():
    """Truckers' strike (O3): the 'normal' transit before it (11.8 days) included the Nov 2017 to Mar 2018 spike.
    Abnormal weeks (late rate well above the typical week's) are now left out of the normal level."""
    rnd = random.Random(41)
    ev = [{"event_id": "strike", "name": "Strike", "type": "strike", "start_date": "2018-05-21",
           "end_date": "2018-06-01", "region": "Brazil"}]

    def shift(d, a):
        if date(2017, 11, 10) <= d < date(2018, 4, 5):   # a long earlier spike: most of the 26 weeks before
            return (0, 7, 0)
        if date(2018, 5, 18) <= d < date(2018, 6, 1):    # the strike itself
            return (0, 5, 0)
        return (0, 0, 0)

    e = engine(make_orders(date(2016, 12, 1), date(2018, 8, 1), rnd, per_day=25, p=(10, 14), shift=shift), ev)
    r = e.event("strike")
    assert r.summary["normal_weeks_left_out"] >= 15
    assert r.summary["normal_transit_days"] < 8.5          # the base transit is 5 to 9 days (7 on average)
    assert r.verdict == "supports" and not r.summary["elevated_before"]


def test_an_event_that_ended_before_the_period_is_not_a_cause_of_it():
    """HO2 (December 2017): Black Friday (23 to 27 Nov) was listed among December's causes. An event that ended
    before the period asked about is not counted as a cause of the period (carry-over isn't tested)."""
    from whychain.analysis.battery import run_task

    rnd = random.Random(42)
    ev = [{"event_id": "bf", "name": "Black Friday", "type": "peak", "start_date": "2017-11-23",
           "end_date": "2017-11-27", "region": "Brazil"}]

    def shift(d, a):
        if date(2017, 11, 20) <= d < date(2017, 12, 31):   # parcels slowed from Black Friday into December
            return (0, 5, 0)
        return (0, 0, 0)

    e = engine(make_orders(date(2017, 3, 1), date(2018, 3, 1), rnd, per_day=25, p=(10, 14), shift=shift), ev)
    task = {"tool": "event", "statement": "Black Friday delayed deliveries", "params": {"event_id": "bf"}}
    dec = {"mode": "time", "label": "orders placed December 2017", "start": "2017-12-01", "end": "2018-01-01"}
    nov = {"mode": "time", "label": "orders placed November 2017", "start": "2017-11-01", "end": "2017-12-01"}
    assert e.event("bf").verdict == "supports"                         # it did delay parcels handed over during it
    in_dec = run_task(e, {**task, "scope": dec}, "T")[0]["fixed"]
    assert not in_dec["is_cause"] and in_dec["status"] == "rejected" and "ended before" in in_dec["reason"]
    in_nov = run_task(e, {**task, "scope": nov}, "T")[0]["fixed"]   # inside its own period it still counts
    assert in_nov["is_cause"]


def test_a_comparison_between_groups_explains_a_gap_not_a_rise():
    """H2 (Northeast vs the other orders, same period): shares are of the gap between the groups."""
    from whychain.analysis.battery import run_task

    rnd = random.Random(43)
    orders = make_orders(date(2017, 6, 1), date(2018, 1, 1), rnd, per_day=40, p=(10, 14),
                         shift=lambda d, a: (0, 4, 0) if a == "north" else (0, 0, 0))
    e = engine(orders)
    spec = {"mode": "segment", "label": "orders to north", "filters": {"area": ["north"]}}
    f = run_task(e, {"tool": "stage", "statement": "Carrier transit takes longer", "scope": spec,
                     "params": {"stage": "transit"}}, "T")[0]
    assert f["fixed"]["is_cause"]
    assert "gap" in f["claim"] and " rise" not in f["claim"] and "of the gap" in f["fixed"]["reason"]


def test_where_it_happened_is_left_out_when_nothing_rose():
    """O3: when the late rate did not rise, 'where the rise sits' listed segments with zero or negative shares."""
    from whychain.analysis.battery import run_task

    rnd = random.Random(44)
    e = engine(make_orders(date(2017, 6, 1), date(2018, 3, 1), rnd, per_day=40, p=(10, 14)))
    spec = {"mode": "time", "label": "orders placed February 2018", "start": "2018-02-01", "end": "2018-03-01"}
    f = run_task(e, {"tool": "segments", "statement": "The rise is concentrated in some places", "scope": spec,
                     "params": {"dim": "area"}}, "T")[0]
    assert f["fixed"]["status"] != "confirmed" and f["fixed"]["reason"].startswith("Not applicable")
    assert "of the rise from" not in f["claim"]


def test_the_value_at_stake_is_the_late_orders_own_value_and_reviews():
    """Olist, March 2018: the late orders' value and freight, and their review stars against on-time orders; each
    sized action carries the value and the low reviews of exactly the orders it would have turned on time."""
    from whychain.analysis import get_engine
    from whychain.analysis.battery import build_scope
    from whychain.harness.local import LocalHarness

    e = get_engine(LocalHarness("olist"))
    sc = build_scope(e, {"mode": "time", "label": "orders placed March 2018", "start": "2018-03-01", "end": "2018-04-01"})
    r = {row[0]: row for row in e.value_at_stake(sc).rows}
    assert r["orders"][1] == int(sc.focus["late"].sum()) == 1328
    assert r["order value (BRL)"][1] == round(float(sc.focus.loc[sc.focus["late"] == 1, "price"].sum()))
    assert r["mean review stars"][1] < r["mean review stars"][2]          # late orders are rated lower
    pb = e.promise_buffer(sc)
    vi, li = pb.columns.index("value_of_orders_avoided"), pb.columns.index("those_with_1_2_star_review")
    for row in pb.rows:
        avoided = row[5] - row[6]
        assert (row[vi] > 0) == (avoided > 0) and 0 <= row[li] <= avoided


def test_runs_share_the_tables_pulled_through_the_harness():
    """A second run on the same dataset reuses the fact table the first pulled (Olist's takes about a minute through
    Zetaris), as its own copy. Harnesses that don't opt in (the synthetic one above) are always queried."""
    from whychain.analysis import get_engine
    from whychain.analysis.engine import _TABLES
    from whychain.harness.local import LocalHarness

    _TABLES.clear()
    calls = []
    h1, h2 = LocalHarness("kaveri"), LocalHarness("kaveri")
    for h in (h1, h2):
        h.query = (lambda orig: lambda sql, limit=200: (calls.append(sql), orig(sql, limit))[1])(h.query)
    f1 = get_engine(h1).facts()
    f2 = get_engine(h2).facts()
    assert len(calls) == 1 and f1.equals(f2) and f1 is not f2
    assert not getattr(FakeHarness([]), "shared_tables", False)
