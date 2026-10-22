"""Supply analyses: orders at risk, stock-outs and backlogs, computed in code with coded verdicts.

The same idea as the late-delivery engine: one set of federated queries (order lines, stock, purchase orders, ports,
reorder policy), deterministic checks that each measure what share of the symptom they explain, and a fixed battery
per kind of question.

Symptom by kind of question:
  at risk   orders due in the window that can't be fully allocated (units on backorder)
  stock-out units on backorder for the product in the window
  backlog   open orders created in the window

Checks (each explains a share of the symptom; a cause needs >= 25%):
  inbound   the short SKUs have open purchase orders that are late (supplier ETA >= 3 days after the expected date,
            or past due and not received)
  port      ... and those late orders come through a port the signals feed reports as disrupted
  demand    recent demand for the short SKUs is >= 1.5x their planning baseline (and which promotions drove it)
  policy    the reorder point is below what recent demand needs over the supplier's lead time
  duplicates orders sharing a business key (external reference) inflate the count
  volume    new orders per day vs the four weeks before
"""
from __future__ import annotations

import math
import re
import threading
from datetime import date, timedelta

import numpy as np
import pandas as pd

from whychain.analysis.engine import Result, pct, r1

SUPPLY_SHARE = 0.25

LINES_SQL = """SELECT o.order_id, o.external_ref, o.customer_id, o.warehouse_id, o.channel, o.order_date, o.created_at,
       o.promised_date, o.status, o.promo_code, c.city, l.sku, l.qty, l.allocated_qty, l.backordered_qty,
       l.line_value, s.name AS sku_name, s.category, s.supplier_id
FROM erp.sales_orders o
JOIN erp.order_lines l ON l.order_id = o.order_id
JOIN erp.customers c ON c.customer_id = o.customer_id
JOIN erp.skus s ON s.sku = l.sku"""
POS_SQL = """SELECT po_id, supplier_id, sku, dest_warehouse, qty, po_type, order_date, expected_date, supplier_eta,
       received_date, status, port_of_entry FROM procurement.purchase_orders"""
SUPPLIERS_SQL = "SELECT supplier_id, name, brand, port_of_entry, contract_lead_time_days FROM procurement.suppliers"
PORTS_SQL = "SELECT port, status, reason, since, expected_normalisation FROM signals.port_status"
POLICY_SQL = """SELECT warehouse_id, sku, avg_daily_demand_baseline, safety_stock, reorder_point, last_reviewed
FROM wms.reorder_policy"""
WAREHOUSES_SQL = "SELECT warehouse_id, name, city FROM erp.warehouses"

OPEN_STATUSES = ("OPEN", "BACKORDERED")
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]


def supply_kind(question: str) -> str | None:
    q = question.lower()
    if re.search(r"\bbacklog|open[- ]orders?\b", q):
        return "backlog"
    if re.search(r"\b(run|ran|running) out\b|stock[- ]?outs?\b|out of stock|shortage", q):
        return "stockout"
    if re.search(r"\bat risk\b|miss(ing)? (their|the) promised|next week", q):
        return "at_risk"
    return None


class SupplyEngine:
    """Supply data for one dataset (needs order lines, stock and purchase orders: the simulated company)."""

    def __init__(self, harness):
        self.h = harness
        self.as_of = pd.Timestamp(getattr(harness, "as_of", None) or date.today())
        self._cache: dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()

    def _df(self, key: str, sql: str) -> pd.DataFrame:
        with self._lock:
            if key not in self._cache:
                r = self.h.query(sql, limit=500_000)
                if not r.ok:
                    raise RuntimeError(f"{key} query failed: {r.error}")
                self._cache[key] = pd.DataFrame(r.rows, columns=r.columns)
            return self._cache[key]

    def genuine_lines(self) -> pd.DataFrame:
        """Order lines with duplicate copies removed: for each external reference, only the first order counts."""
        d = self.lines()
        first = d.drop_duplicates("order_id").sort_values("created_at").drop_duplicates("external_ref")["order_id"]
        return d[d["order_id"].isin(set(first))]

    def lines(self) -> pd.DataFrame:
        d = self._df("lines", LINES_SQL).copy()
        for c in ("order_date", "promised_date"):
            d[c] = pd.to_datetime(d[c], errors="coerce")
        for c in ("qty", "allocated_qty", "backordered_qty", "line_value"):
            d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0)
        return d

    def pos(self) -> pd.DataFrame:
        d = self._df("pos", POS_SQL).copy()
        for c in ("order_date", "expected_date", "supplier_eta", "received_date"):
            d[c] = pd.to_datetime(d[c], errors="coerce")
        d["qty"] = pd.to_numeric(d["qty"], errors="coerce").fillna(0)
        return d

    def suppliers(self) -> pd.DataFrame:
        return self._df("suppliers", SUPPLIERS_SQL)

    def ports(self) -> pd.DataFrame:
        return self._df("ports", PORTS_SQL)

    def policy(self) -> pd.DataFrame:
        d = self._df("policy", POLICY_SQL).copy()
        for c in ("avg_daily_demand_baseline", "safety_stock", "reorder_point"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
        return d

    def warehouses(self) -> pd.DataFrame:
        return self._df("warehouses", WAREHOUSES_SQL)

    def provenance(self) -> str:
        return LINES_SQL

    # ------------------------------------------------------------------ reading the question
    def plan_scope(self, question: str) -> dict:
        q = question.lower()
        kind = supply_kind(question) or "at_risk"
        wh = self.warehouses()
        warehouses = [str(r.warehouse_id) for r in wh.itertuples()
                      if re.search(rf"\b{re.escape(str(r.city).lower())}\b", q) or str(r.warehouse_id).lower() in q.split()]
        if not warehouses:
            for r in wh.itertuples():
                for w in re.findall(r"[a-z]+", str(r.name).lower()):
                    if len(w) >= 4 and w not in ("dc", "warehouse", "distribution", "centre", "center") \
                            and re.search(rf"\b{w}\b", q):
                        warehouses.append(str(r.warehouse_id))
        lines = self.lines()
        cats = sorted({c for c in lines["category"].dropna().astype(str).unique()
                       if re.search(rf"\b{re.escape(c.lower().rstrip('s'))}", q)})
        a = self.as_of.normalize()
        if kind == "at_risk":
            start, end, label = a + timedelta(days=1), a + timedelta(days=8), "due in the coming week"
        else:
            m = re.search(r"\b(early|mid|late)[- ]?(" + "|".join(_MONTHS) + r")\b", q)
            if m:
                mo = _MONTHS.index(m.group(2)) + 1
                y = a.year if mo <= a.month else a.year - 1
                d0 = {"early": 1, "mid": 8, "late": 18}[m.group(1)]
                d1 = {"early": 11, "mid": 22, "late": 32}[m.group(1)]
                start = pd.Timestamp(date(y, mo, d0))
                end = min(pd.Timestamp(date(y, mo, 1)) + pd.offsets.MonthEnd(0) + timedelta(days=1),
                          pd.Timestamp(date(y, mo, 1)) + timedelta(days=d1 - 1), a + timedelta(days=1))
                label = f"{m.group(1)} {m.group(2).title()} {y}"
            elif re.search(r"\bthis week\b", q):
                start, end, label = a - timedelta(days=6), a + timedelta(days=1), "this week"
            else:
                start, end, label = a - timedelta(days=13), a + timedelta(days=1), "the last 14 days"
        return {"kind": kind, "warehouses": sorted(set(warehouses)), "categories": cats, "start": str(start.date()),
                "end": str(end.date()), "label": label}

    # ------------------------------------------------------------------ the symptom
    def focus(self, spec: dict) -> pd.DataFrame:
        d = self.lines()
        if spec["warehouses"]:
            d = d[d["warehouse_id"].isin(spec["warehouses"])]
        if spec["categories"]:
            d = d[d["category"].isin(spec["categories"])]
        a, b = pd.Timestamp(spec["start"]), pd.Timestamp(spec["end"])
        if spec["kind"] == "at_risk":
            d = d[d["status"].isin(OPEN_STATUSES) & (d["promised_date"] >= a) & (d["promised_date"] < b)]
        elif spec["kind"] == "backlog":
            d = d[d["status"].isin(OPEN_STATUSES) & (d["order_date"] >= a) & (d["order_date"] < b)]
        else:
            d = d[(d["order_date"] >= a) & (d["order_date"] < b)]
        return d

    def measure(self, spec: dict) -> Result:
        f = self.focus(spec)
        orders = f["order_id"].nunique()
        short = f[f["backordered_qty"] > 0]
        at_risk = short["order_id"].nunique()
        units = int(short["backordered_qty"].sum())
        value = float((short["backordered_qty"] / short["qty"].replace(0, np.nan) * short["line_value"]).sum())
        rows = [["orders in scope", orders], ["orders with a backordered line", at_risk],
                ["share of orders at risk (%)", pct(at_risk / orders) if orders else None],
                ["units on backorder", units], ["value on backorder", round(value, 2)],
                ["SKUs short", short["sku"].nunique()]]
        where = (f" from {', '.join(spec['warehouses'])}" if spec["warehouses"] else "") + \
                (f", category {', '.join(spec['categories'])}" if spec["categories"] else "")
        what = {"at_risk": "open orders", "stockout": "orders placed", "backlog": "open orders created"}[spec["kind"]]
        summary = {"orders": orders, "at_risk": at_risk, "units": units, "label": f"{what} {spec['label']}{where}"}
        method = (f"Order lines of {what} {spec['label']}{where}, as of {self.as_of.date()}. A line is short when part "
                  "of it is on backorder (not allocated from stock).")
        return Result("supply_measure", "The symptom: orders and units short", ["measure", "result"], rows, summary,
                      method, "measured", self.provenance())

    def shortage_by(self, spec: dict, dim: str) -> Result:
        f = self.focus(spec)
        short = f[f["backordered_qty"] > 0]
        total = short["backordered_qty"].sum()
        g = short.groupby(dim).agg(units=("backordered_qty", "sum"), orders=("order_id", "nunique")).sort_values(
            "units", ascending=False)
        names = {}
        if dim == "supplier_id":
            sup = self.suppliers().set_index("supplier_id")
            names = {k: f"{sup.loc[k, 'name']}" if k in sup.index else k for k in g.index}
        rows = [[str(k), names.get(k, str(k)), int(r.units), int(r.orders), pct(r.units / total) if total else None]
                for k, r in g.head(8).iterrows()]
        summary = {"dimension": dim, "top": rows[0][0] if rows else None, "top_share": rows[0][4] if rows else None}
        return Result("shortage", f"Where the shortage sits, by {dim}", [dim, "name", "units_short", "orders",
                      "share_of_short_units_pct"], rows, summary,
                      "Backordered units in scope, grouped; shares of all backordered units.", "measured",
                      self.provenance())

    # ------------------------------------------------------------------ checks
    def _short(self, spec):
        f = self.focus(spec)
        return f[f["backordered_qty"] > 0]

    def inbound(self, spec: dict) -> Result:
        short = self._short(spec)
        total = float(short["backordered_qty"].sum())
        po = self.pos()
        whs = spec["warehouses"] or sorted(short["warehouse_id"].unique())
        open_po = po[po["sku"].isin(short["sku"].unique()) & po["dest_warehouse"].isin(whs) & (po["status"] != "RECEIVED")]
        open_po = open_po.assign(slip_days=(open_po["supplier_eta"] - open_po["expected_date"]).dt.days)
        late = open_po[(open_po["slip_days"] >= 3) | ((open_po["expected_date"] < self.as_of) & open_po["received_date"].isna())]
        ports = self.ports().set_index("port")
        late_skus = set(late["sku"])
        units_late = float(short.loc[short["sku"].isin(late_skus), "backordered_qty"].sum())
        disrupted = {p for p in ports.index if str(ports.loc[p, "status"]).upper() in ("DISRUPTED", "CLOSED", "CONGESTED")}
        port_skus = set(late.loc[late["port_of_entry"].isin(disrupted), "sku"])
        units_port = float(short.loc[short["sku"].isin(port_skus), "backordered_qty"].sum())
        sup = self.suppliers().set_index("supplier_id")
        rows = []
        for _, r in late.sort_values("slip_days", ascending=False).head(12).iterrows():
            pstat = ports.loc[r.port_of_entry, "status"] if r.port_of_entry in ports.index else ""
            rows.append([r.po_id, r.supplier_id, str(sup.loc[r.supplier_id, "name"]) if r.supplier_id in sup.index else "",
                         r.sku, int(r.qty), str(r.expected_date.date()) if pd.notna(r.expected_date) else None,
                         str(r.supplier_eta.date()) if pd.notna(r.supplier_eta) else None,
                         int(r.slip_days) if pd.notna(r.slip_days) else None, r.port_of_entry, pstat])
        rows.append(["(share of short units with a late inbound order)", None, None, None, None, None, None, None,
                     None, pct(units_late / total) if total else None])
        rows.append(["(share of short units whose late order is held at a disrupted port)", None, None, None, None,
                     None, None, None, None, pct(units_port / total) if total else None])
        top_sup = late.groupby("supplier_id")["qty"].sum().sort_values(ascending=False)
        top_port = late.groupby("port_of_entry")["qty"].sum().sort_values(ascending=False)
        summary = {"late_share": units_late / total if total else 0.0, "port_share": units_port / total if total else 0.0,
                   "top_supplier": top_sup.index[0] if len(top_sup) else None,
                   "top_supplier_name": str(sup.loc[top_sup.index[0], "name"]) if len(top_sup) and top_sup.index[0] in sup.index else None,
                   "top_port": top_port.index[0] if len(top_port) else None,
                   "port_status": {p: f"{ports.loc[p, 'status']}: {ports.loc[p, 'reason']}" for p in disrupted},
                   "late_skus": sorted(late_skus)}
        method = ("Open purchase orders for the short SKUs, to the same warehouses. Late = supplier ETA at least 3 days "
                  "after the expected date, or past the expected date and not received. Ports are matched to the "
                  "signals feed.")
        return Result("inbound", "Inbound purchase orders for the short SKUs",
                      ["po_id", "supplier_id", "supplier", "sku", "qty", "expected_date", "supplier_eta", "slip_days",
                       "port_of_entry", "port_status"], rows, summary, method, "measured", POS_SQL)

    def demand(self, spec: dict) -> Result:
        short = self._short(spec)
        total = float(short["backordered_qty"].sum())
        lines = self.lines()
        a, b = pd.Timestamp(spec["start"]), pd.Timestamp(spec["end"])
        win_end = min(b, self.as_of + timedelta(days=1))
        win_start = win_end - timedelta(days=14)
        whs = spec["warehouses"] or sorted(short["warehouse_id"].unique())
        lines = self.genuine_lines()
        recent = lines[lines["sku"].isin(short["sku"].unique()) & lines["warehouse_id"].isin(whs)
                       & (lines["order_date"] >= win_start) & (lines["order_date"] < win_end)
                       & (lines["status"] != "CANCELLED")]
        pol = self.policy()
        rows, spike_skus = [], set()
        for (w, sku), g in recent.groupby(["warehouse_id", "sku"]):
            daily = g["qty"].sum() / 14
            base = pol[(pol["warehouse_id"] == w) & (pol["sku"] == sku)]["avg_daily_demand_baseline"]
            base = float(base.iloc[0]) if len(base) else float("nan")
            ratio = daily / base if base and base == base else float("nan")
            promo_units = g.loc[g["promo_code"].notna() & (g["promo_code"].astype(str) != "None"), "qty"].sum()
            promo = ", ".join(sorted(g["promo_code"].dropna().astype(str).unique()))
            if ratio == ratio and ratio >= 1.5:
                spike_skus.add(sku)
            rows.append([w, sku, r1(daily), r1(base), round(ratio, 2) if ratio == ratio else None,
                         pct(promo_units / g["qty"].sum()) if g["qty"].sum() else None, promo or None])
        rows.sort(key=lambda r: -(r[4] or 0))
        rows = rows[:12]
        units_spike = float(short.loc[short["sku"].isin(spike_skus), "backordered_qty"].sum())
        rows.append(["(share of short units in SKUs with demand at or above the threshold ratio)", None, None, None,
                     1.5, pct(units_spike / total) if total else None, None])
        promos = sorted({p for r in rows[:-1] if r[6] for p in str(r[6]).split(", ")})
        summary = {"spike_share": units_spike / total if total else 0.0, "spike_skus": sorted(spike_skus),
                   "promos": promos}
        method = ("Units ordered per day for the short SKUs over the 14 days to the end of the window, against the "
                  "planning baseline in the reorder policy, and the share of those units on a promotion code.")
        return Result("demand", "Recent demand vs the planning baseline",
                      ["warehouse_id", "sku", "recent_daily_units", "baseline_daily_units", "ratio",
                       "promo_share_pct", "promo_codes"], rows, summary, method, "measured", POLICY_SQL)

    def policy_check(self, spec: dict) -> Result:
        short = self._short(spec)
        total = float(short["backordered_qty"].sum())
        lines = self.genuine_lines()
        pol = self.policy()
        sup = self.suppliers().set_index("supplier_id")
        b = min(pd.Timestamp(spec["end"]), self.as_of + timedelta(days=1))
        rows, stale = [], set()
        for (w, sku), g in short.groupby(["warehouse_id", "sku"]):
            p = pol[(pol["warehouse_id"] == w) & (pol["sku"] == sku)]
            if p.empty:
                continue
            p = p.iloc[0]
            recent = lines[(lines["warehouse_id"] == w) & (lines["sku"] == sku) & (lines["order_date"] >= b - timedelta(days=14))
                           & (lines["order_date"] < b) & (lines["status"] != "CANCELLED")]["qty"].sum() / 14
            sid = g["supplier_id"].iloc[0]
            lead = float(sup.loc[sid, "contract_lead_time_days"]) if sid in sup.index else float("nan")
            needed = recent * lead + float(p["safety_stock"]) if lead == lead else float("nan")
            is_stale = needed == needed and float(p["reorder_point"]) < 0.8 * needed
            if is_stale:
                stale.add((w, sku))
            rows.append([w, sku, int(p["reorder_point"]), r1(recent), int(lead) if lead == lead else None,
                         int(round(needed)) if needed == needed else None, str(p["last_reviewed"]),
                         "stale" if is_stale else "ok"])
        rows = sorted(rows, key=lambda r: r[7] != "stale")[:12]
        units_stale = float(sum(short.loc[(short["warehouse_id"] == w) & (short["sku"] == s), "backordered_qty"].sum()
                                for w, s in stale))
        rows.append(["(share of short units where the reorder point is below what recent demand needs)", None, None,
                     None, None, None, None, pct(units_stale / total) if total else None])
        summary = {"stale_share": units_stale / total if total else 0.0, "stale": sorted(f"{w}/{s}" for w, s in stale)}
        method = ("Reorder point vs what recent demand needs over the supplier's contract lead time plus safety stock "
                  "(stale when below 80% of it).")
        return Result("policy", "Reorder points vs recent demand",
                      ["warehouse_id", "sku", "reorder_point", "recent_daily_units", "lead_time_days",
                       "reorder_point_needed", "last_reviewed", "assessment"], rows, summary, method,
                      "measured", POLICY_SQL)

    def duplicates(self, spec: dict) -> Result:
        f = self.focus(spec).drop_duplicates("order_id")
        all_orders = self.lines().drop_duplicates("order_id")
        refs = all_orders.groupby("external_ref")["order_id"].nunique()
        dup_refs = set(refs[refs > 1].index)
        dups = f[f["external_ref"].isin(dup_refs)]
        # the copies beyond the first order of each reference are the extra ones
        first = all_orders.sort_values("created_at").drop_duplicates("external_ref")
        extra = dups[~dups["order_id"].isin(first["order_id"])]
        n = len(f)
        by_channel = extra.groupby("channel")["order_id"].nunique().sort_values(ascending=False)
        rows = [["orders in scope", n], ["orders sharing an external reference with another order", len(dups)],
                ["extra copies (beyond the first order of each reference)", len(extra)],
                ["share of orders in scope that are extra copies (%)", pct(len(extra) / n) if n else None]]
        rows += [[f"extra copies via channel {c}", int(v)] for c, v in by_channel.items()]
        summary = {"extra": len(extra), "share": len(extra) / n if n else 0.0,
                   "channel": by_channel.index[0] if len(by_channel) else None,
                   "extra_order_ids": sorted(extra["order_id"].astype(str))[:200]}
        method = ("Orders that share an external reference (the customer's own order number) with another order; "
                  "every copy after the first is counted as an extra.")
        return Result("duplicates", "Duplicate orders (same external reference)", ["check", "result"], rows, summary,
                      method, "measured", self.provenance())

    def order_volume(self, spec: dict) -> Result:
        lines = self.lines().drop_duplicates("order_id")
        if spec["warehouses"]:
            lines = lines[lines["warehouse_id"].isin(spec["warehouses"])]
        a, b = pd.Timestamp(spec["start"]), pd.Timestamp(spec["end"])
        cur = lines[(lines["order_date"] >= a) & (lines["order_date"] < b)]
        prev = lines[(lines["order_date"] >= a - timedelta(days=28)) & (lines["order_date"] < a)]
        days_cur = max((min(b, self.as_of + timedelta(days=1)) - a).days, 1)
        f_day, r_day = len(cur) / days_cur, len(prev) / 28
        uniq_cur = cur.drop_duplicates("external_ref")
        rows = [["orders per day in the window", r1(f_day)], ["orders per day, 4 weeks before", r1(r_day)],
                ["ratio", round(f_day / r_day, 2) if r_day else None],
                ["orders per day in the window, counting each external reference once", r1(len(uniq_cur) / days_cur)],
                ["ratio, counting each external reference once", round(len(uniq_cur) / days_cur / r_day, 2) if r_day else None]]
        summary = {"ratio": f_day / r_day if r_day else None, "ratio_dedup": len(uniq_cur) / days_cur / r_day if r_day else None}
        method = "New orders per day in the window vs the four weeks before, with and without duplicate references."
        return Result("volume_orders", "New orders per day", ["measure", "result"], rows, summary, method,
                      "measured", self.provenance())


# ---------------------------------------------------------------------- battery
def plan_supply(question: str, eng: SupplyEngine) -> dict:
    spec = eng.plan_scope(question)
    tasks = []

    def add(tool, statement, **params):
        tasks.append({"tool": tool, "statement": statement, "scope": spec, "params": params})

    m = eng.measure(spec).summary
    add("supply_measure", f"Measure the symptom: {m['label']}")
    if spec["kind"] in ("at_risk", "stockout"):
        add("shortage", "The shortage is concentrated in particular suppliers", dim="supplier_id")
        add("shortage", "The shortage is concentrated in particular SKUs", dim="sku")
        add("inbound", "Inbound purchase orders for the short SKUs are late")
        add("port", "A disrupted port is holding the late inbound orders")
        add("demand", "Demand for the short SKUs spiked above the planning baseline")
        add("policy", "Reorder points are too low for recent demand")
    if spec["kind"] == "backlog":
        add("volume_orders", "More orders arrived than usual")
    add("duplicates", "Duplicate orders (the same external reference) inflate the numbers")
    return {"spec": spec, "tasks": tasks}


_MEMO: dict[tuple, Result] = {}


def _run(eng: SupplyEngine, tool: str, spec: dict, **params) -> Result:
    import json

    key = (id(eng), tool, json.dumps(spec, sort_keys=True), json.dumps(params, sort_keys=True))
    if key not in _MEMO:
        fn = {"supply_measure": eng.measure, "inbound": eng.inbound, "port": eng.inbound, "demand": eng.demand,
              "policy": eng.policy_check, "duplicates": eng.duplicates, "volume_orders": eng.order_volume}
        _MEMO[key] = eng.shortage_by(spec, params["dim"]) if tool == "shortage" else fn[tool](spec)
    return _MEMO[key]


def _ref(eid: str, res: Result, row: int, col: str) -> dict:
    return {"value": res.rows[row][res.columns.index(col)], "evidence": eid, "column": col, "row": row}


def _last(res: Result) -> int:
    return len(res.rows) - 1


def run_supply_task(eng: SupplyEngine, task: dict, eid_prefix: str) -> tuple[dict, dict]:
    tool, spec, params = task["tool"], task["scope"], task.get("params", {})
    res = _run(eng, tool, spec, **params)
    eid = f"{eid_prefix}.A1"
    evidence = {eid: res.evidence(eid, eid_prefix, "Analysis engine")}
    keys, status, is_cause, share, kind = [], "rejected", False, None, "hypothesis"
    s = res.summary
    if tool == "supply_measure":
        kind, status, verdict = "measure", "confirmed", "measured"
        keys = [_ref(eid, res, k, "result") for k in range(len(res.rows))]
        claim = (f"{res.rows[1][1]} of {res.rows[0][1]} {s['label']} have a line on backorder "
                 f"({res.rows[2][1]}%): {res.rows[3][1]:,} units across {res.rows[5][1]} SKUs, worth "
                 f"{res.rows[4][1]:,.0f} on backorder.")
        reason = "Measured directly by the analysis engine."
    elif tool == "shortage":
        kind, status, verdict = "measure", "confirmed", "measured"
        keys = [_ref(eid, res, k, c) for k in range(min(3, len(res.rows))) for c in ("units_short", "share_of_short_units_pct")]
        claim = "Short units by " + s["dimension"] + ": " + "; ".join(
            f"{r[0]} ({r[1]}): {r[2]:,} units, {r[4]}%" for r in res.rows[:3]) + "."
        reason = "Where the shortage sits. This says where, not why."
    elif tool in ("inbound", "port"):
        sh = s["late_share"] if tool == "inbound" else s["port_share"]
        row = _last(res) - (1 if tool == "inbound" else 0)
        keys = [_ref(eid, res, row, "port_status")]
        top = [r for r in res.rows if r[0] and not str(r[0]).startswith("(")][:3]
        keys += [_ref(eid, res, k, c) for k in range(len(top)) for c in ("qty", "slip_days")]
        if tool == "inbound":
            claim = (f"{res.rows[row][9]}% of the short units are SKUs with a late inbound purchase order"
                     + (f"; most late units are from {s['top_supplier']} ({s['top_supplier_name']}), coming through "
                        f"{s['top_port']}" if s.get("top_supplier") else "") + ". Latest: "
                     + "; ".join(f"{r[0]} {r[3]} {r[4]:,} units, ETA {r[6]} vs expected {r[5]} ({r[7]} days late)"
                                 for r in top) + ".")
        else:
            ports = "; ".join(f"{p} ({st})" for p, st in s["port_status"].items()) or "none"
            claim = (f"{res.rows[row][9]}% of the short units are SKUs whose late inbound orders come through a port "
                     f"the signals feed reports as disrupted. Disrupted ports: {ports}.")
        share = sh
        if sh >= SUPPLY_SHARE:
            status, is_cause, verdict = "confirmed", True, "supports"
            reason = f"Involved in {pct(sh)}% of the short units (cause threshold: {int(SUPPLY_SHARE * 100)}%)."
        else:
            verdict = "refutes"
            reason = f"Involved in only {pct(sh)}% of the short units (cause threshold: {int(SUPPLY_SHARE * 100)}%)."
    elif tool == "demand":
        sh = s["spike_share"]
        keys = [_ref(eid, res, _last(res), "promo_share_pct"), _ref(eid, res, _last(res), "ratio")]
        spikes = [r for r in res.rows[:-1] if r[4] is not None and r[4] >= 1.5][:3]
        keys += [_ref(eid, res, res.rows.index(r), c) for r in spikes
                 for c in ("recent_daily_units", "baseline_daily_units", "ratio")]
        claim = (f"{res.rows[_last(res)][5]}% of the short units are SKUs whose recent demand is at least "
                 f"{res.rows[_last(res)][4]}x the planning baseline" + (": " + "; ".join(f"{r[1]} at {r[0]}: {r[2]} a day vs {r[3]} ({r[4]}x)"
                                                          for r in spikes) if spikes else "")
                 + (f". Promotion codes on those orders: {', '.join(s['promos'])}" if s["promos"] else "") + ".")
        share = sh
        if sh >= SUPPLY_SHARE:
            status, is_cause, verdict = "confirmed", True, "supports"
            reason = f"Involved in {pct(sh)}% of the short units (cause threshold: {int(SUPPLY_SHARE * 100)}%)."
        else:
            verdict, reason = "refutes", f"Involved in only {pct(sh)}% of the short units."
    elif tool == "policy":
        sh = s["stale_share"]
        keys = [_ref(eid, res, _last(res), "assessment")]
        stale = [r for r in res.rows[:-1] if r[7] == "stale"][:3]
        keys += [_ref(eid, res, res.rows.index(r), c) for r in stale for c in ("reorder_point", "reorder_point_needed")]
        claim = (f"{res.rows[_last(res)][7]}% of the short units are SKUs whose reorder point is below what recent "
                 "demand needs" + (": " + "; ".join(f"{r[1]} at {r[0]} reorder point {r[2]:,} vs {r[5]:,} needed "
                                                    f"(last reviewed {r[6]})" for r in stale) if stale else "") + ".")
        share = sh
        if sh >= SUPPLY_SHARE:
            status, is_cause, verdict = "confirmed", True, "supports"
            reason = f"Involved in {pct(sh)}% of the short units (cause threshold: {int(SUPPLY_SHARE * 100)}%)."
        else:
            verdict, reason = "refutes", f"Involved in only {pct(sh)}% of the short units."
    elif tool == "duplicates":
        keys = [_ref(eid, res, k, "result") for k in range(len(res.rows))]
        claim = (f"{res.rows[2][1]} of {res.rows[0][1]} orders in scope are extra copies of an earlier order with the "
                 f"same external reference ({res.rows[3][1]}%)" + (f", mostly via channel {s['channel']}" if s["channel"] else "")
                 + ".")
        share = s["share"]
        if s["share"] >= 0.02 and s["extra"] >= 3:
            status, is_cause, verdict = "confirmed", True, "supports"
            reason = f"{res.rows[3][1]}% of the orders in scope are duplicates (threshold: 2%)."
        else:
            verdict, reason = "refutes", "Too few duplicates to matter."
    elif tool == "volume_orders":
        keys = [_ref(eid, res, k, "result") for k in range(len(res.rows))]
        claim = (f"{res.rows[0][1]} new orders a day in the window vs {res.rows[1][1]} in the four weeks before "
                 f"({res.rows[2][1]}x); counting each external reference once, {res.rows[3][1]} a day "
                 f"({res.rows[4][1]}x).")
        ratio = s.get("ratio_dedup") or 0
        if ratio >= 1.25:
            status, is_cause, verdict = "confirmed", True, "supports"
            reason = "Genuine new orders (duplicates removed) rose by 25% or more."
        else:
            verdict = "refutes"
            reason = ("Once duplicate references are removed, orders did not rise by 25% or more."
                      if (s.get("ratio") or 0) >= 1.25 else "Orders did not rise by 25% or more.")
    else:
        raise ValueError(tool)
    return ({"statement": task["statement"], "type": kind, "verdict": verdict, "claim": claim, "key_numbers": keys,
             "evidence_ids": [eid], "fixed": {"status": status, "is_cause": is_cause, "share_of_symptom": share,
                                              "reason": reason}}, evidence)


_ENGINES: dict[int, SupplyEngine] = {}


def get_supply_engine(harness) -> SupplyEngine | None:
    """A supply engine for datasets that have order lines, stock and purchase orders (the simulated company)."""
    if getattr(getattr(harness, "dataset", None), "key", "") != "kaveri":
        return None
    if id(harness) not in _ENGINES:
        _ENGINES[id(harness)] = SupplyEngine(harness)
    return _ENGINES[id(harness)]


SUPPLY_TOOLS = {"supply_measure", "shortage", "inbound", "port", "demand", "policy", "duplicates", "volume_orders"}
