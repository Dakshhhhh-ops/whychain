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
    assert np.isclose(sum(r[-2] for r in sa.rows if r[-2] is not None), round(100 * sa.summary["rise_pp"] / 100, 2),
                      atol=0.05)
