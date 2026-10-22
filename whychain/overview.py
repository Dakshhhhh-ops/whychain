"""Control tower: live KPIs, detected problems and the supply network, computed through the data harness.

Every number here is a plain SQL query over the same federated sources the agents use. Problems are
found by generic rules (a lane whose on-time rate fell, a DC with many backordered orders, a category
running short, suppliers running late, a disrupted port), and each comes with a suggested "why"
question that starts an investigation.
"""
from __future__ import annotations

from whychain.config import AS_OF
from whychain.harness.base import DataHarness

A = AS_OF
DC_NAMES = {"MUM": "Mumbai", "PUN": "Pune", "CHN": "Chennai"}


def _rows(h: DataHarness, sql: str, limit: int = 100) -> list[dict]:
    r = h.query(sql, limit=limit)
    if not r.ok:
        return []
    return [dict(zip(r.columns, row)) for row in r.rows]


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def kpis_and_problems(h: DataHarness) -> tuple[dict, list[dict]]:
    problems: list[dict] = []
    ontime_case = "CASE WHEN CAST(delivered_date AS DATE) <= CAST(promised_delivery_date AS DATE) THEN 1 ELSE 0 END"
    recent = f"CAST(delivered_date AS DATE) > date_sub(DATE'{A}', 30)"
    base = f"CAST(delivered_date AS DATE) BETWEEN date_sub(DATE'{A}', 90) AND date_sub(DATE'{A}', 60)"

    # ---- on-time delivery, overall and per lane
    lanes = _rows(h, f"""
        SELECT lane_id, dest_city,
          SUM(CASE WHEN {recent} THEN 1 ELSE 0 END) AS n_recent,
          SUM(CASE WHEN {recent} THEN {ontime_case} ELSE 0 END) AS ok_recent,
          SUM(CASE WHEN {base} THEN 1 ELSE 0 END) AS n_base,
          SUM(CASE WHEN {base} THEN {ontime_case} ELSE 0 END) AS ok_base
        FROM carrier.shipments WHERE delivered_date IS NOT NULL GROUP BY lane_id, dest_city""")
    n_r = sum(_f(x["n_recent"]) for x in lanes)
    n_b = sum(_f(x["n_base"]) for x in lanes)
    ontime_recent = sum(_f(x["ok_recent"]) for x in lanes) / n_r if n_r else None
    ontime_base = sum(_f(x["ok_base"]) for x in lanes) / n_b if n_b else None
    lane_status = {}
    for x in lanes:
        if _f(x["n_recent"]) >= 8 and _f(x["n_base"]) >= 8:
            r_, b_ = _f(x["ok_recent"]) / _f(x["n_recent"]), _f(x["ok_base"]) / _f(x["n_base"])
            lane_status[x["lane_id"]] = {"recent": r_, "baseline": b_}
            if b_ - r_ >= 0.2:
                lane_status[x["lane_id"]]["status"] = "critical" if r_ < 0.6 else "warning"
                problems.append({
                    "id": f"lane:{x['lane_id']}", "kind": "transport",
                    "severity": "critical" if r_ < 0.6 else "high",
                    "title": f"Deliveries to {x['dest_city']} are late",
                    "metric": f"{r_:.0%} on time", "baseline": f"was {b_:.0%}",
                    "detail": f"Lane {x['lane_id']}: on-time rate over the last 30 days vs. the same lane 60-90 days ago.",
                    "question": f"Why have deliveries to {x['dest_city']} become late recently?",
                    "entities": [x["lane_id"]],
                })

    # ---- next-week orders at risk, per DC
    dcs = _rows(h, f"""
        SELECT warehouse_id, COUNT(*) AS open_due,
          SUM(CASE WHEN status = 'BACKORDERED' THEN 1 ELSE 0 END) AS backordered,
          SUM(CASE WHEN status = 'BACKORDERED' THEN order_value_inr ELSE 0 END) AS value_at_risk
        FROM erp.sales_orders
        WHERE status IN ('OPEN', 'BACKORDERED') AND CAST(promised_date AS DATE) BETWEEN DATE'{A}' AND date_add(DATE'{A}', 7)
        GROUP BY warehouse_id""")
    dc_status = {}
    for x in dcs:
        share = _f(x["backordered"]) / _f(x["open_due"], 1) if _f(x["open_due"]) else 0
        dc_status[x["warehouse_id"]] = {"at_risk_share": share, "backordered": int(_f(x["backordered"])),
                                        "open_due": int(_f(x["open_due"])), "value_at_risk": _f(x["value_at_risk"])}
        if share >= 0.35 and _f(x["backordered"]) >= 8:
            name = DC_NAMES.get(x["warehouse_id"], x["warehouse_id"])
            dc_status[x["warehouse_id"]]["status"] = "critical" if share >= 0.6 else "warning"
            problems.append({
                "id": f"dc:{x['warehouse_id']}", "kind": "fulfilment",
                "severity": "critical" if share >= 0.6 else "high",
                "title": f"{name} orders due next week are backordered",
                "metric": f"{int(_f(x['backordered']))} of {int(_f(x['open_due']))} orders",
                "baseline": f"₹{_f(x['value_at_risk']) / 1e5:,.1f} lakh at risk",
                "detail": f"Open orders at the {name} DC promised in the next 7 days that can't be filled from stock.",
                "question": f"Why are so many {name} orders at risk of missing their promised date next week?",
                "entities": [x["warehouse_id"]],
            })

    # ---- order intake jump, per DC
    intake = _rows(h, f"""
        SELECT warehouse_id,
          SUM(CASE WHEN CAST(order_date AS DATE) > date_sub(DATE'{A}', 7) THEN 1 ELSE 0 END) AS this_week,
          SUM(CASE WHEN CAST(order_date AS DATE) <= date_sub(DATE'{A}', 7)
                    AND CAST(order_date AS DATE) > date_sub(DATE'{A}', 35) THEN 1 ELSE 0 END) / 4.0 AS weekly_avg
        FROM erp.sales_orders GROUP BY warehouse_id""")
    for x in intake:
        tw, avg = _f(x["this_week"]), _f(x["weekly_avg"])
        if avg and tw / avg >= 1.3:
            name = DC_NAMES.get(x["warehouse_id"], x["warehouse_id"])
            problems.append({
                "id": f"intake:{x['warehouse_id']}", "kind": "demand", "severity": "medium",
                "title": f"{name} order intake jumped this week",
                "metric": f"{int(tw)} orders", "baseline": f"vs {avg:.0f}/week average",
                "detail": "Sales orders created in the last 7 days vs. the previous 4-week average.",
                "question": f"Why has the {name} DC's open-order backlog jumped this week?",
                "entities": [x["warehouse_id"]],
            })

    # ---- categories running short today
    short = _rows(h, f"""
        SELECT i.warehouse_id, s.category, COUNT(DISTINCT i.sku) AS skus_short, SUM(i.qty_on_overdue_orders) AS overdue_qty
        FROM wms.inventory_snapshots i JOIN erp.skus s ON s.sku = i.sku
        WHERE CAST(i.snapshot_date AS DATE) = DATE'{A}' AND i.qty_on_overdue_orders > i.on_hand
        GROUP BY i.warehouse_id, s.category ORDER BY overdue_qty DESC LIMIT 3""")
    for x in short:
        if _f(x["skus_short"]) >= 3:
            name = DC_NAMES.get(x["warehouse_id"], x["warehouse_id"])
            problems.append({
                "id": f"short:{x['warehouse_id']}:{x['category']}", "kind": "inventory", "severity": "high",
                "title": f"{name} is short of {x['category'].lower()}",
                "metric": f"{int(_f(x['skus_short']))} SKUs short", "baseline": f"{int(_f(x['overdue_qty'])):,} units on overdue orders",
                "detail": "SKUs where overdue order quantity exceeds stock on hand today.",
                "question": f"Why did {name} run out of {x['category'].lower()}?",
                "entities": [x["warehouse_id"]],
            })

    # ---- suppliers running late
    late = _rows(h, """
        SELECT p.supplier_id, s.name, s.port_of_entry, COUNT(*) AS pos, SUM(p.qty) AS units,
          MAX(datediff(CAST(p.supplier_eta AS DATE), CAST(p.expected_date AS DATE))) AS max_delay
        FROM procurement.purchase_orders p JOIN procurement.suppliers s ON s.supplier_id = p.supplier_id
        WHERE p.status <> 'RECEIVED' AND CAST(p.supplier_eta AS DATE) > CAST(p.expected_date AS DATE)
        GROUP BY p.supplier_id, s.name, s.port_of_entry ORDER BY units DESC LIMIT 6""")
    supplier_status = {}
    for x in late:
        if _f(x["max_delay"]) >= 7:
            supplier_status[x["supplier_id"]] = {"status": "warning", "delay": int(_f(x["max_delay"])), "units": int(_f(x["units"]))}
            problems.append({
                "id": f"supplier:{x['supplier_id']}", "kind": "supply", "severity": "high",
                "title": f"{x['name']} shipments are delayed",
                "metric": f"{int(_f(x['units'])):,} units late", "baseline": f"up to {int(_f(x['max_delay']))} days",
                "detail": f"{int(_f(x['pos']))} open POs via {x['port_of_entry']} with a revised ETA after the expected date.",
                "question": f"Why are {x['name']} ({x['supplier_id']}) shipments late, and what does it affect?",
                "entities": [x["supplier_id"]],
            })

    # ---- external signals
    ports = _rows(h, "SELECT port, status, reason, since, expected_normalisation FROM signals.port_status")
    port_status = {}
    for x in ports:
        if str(x.get("status", "")).upper() != "NORMAL":
            port_status[str(x["port"])] = {"status": "critical", "reason": x.get("reason")}
            problems.append({
                "id": f"port:{x['port']}", "kind": "external", "severity": "critical",
                "title": f"{x['port']} port: {str(x.get('reason') or x['status']).lower()}",
                "metric": str(x["status"]).title(), "baseline": f"since {x.get('since')}",
                "detail": f"Expected to normalise {x.get('expected_normalisation') or 'unknown'}.",
                "question": f"Which orders and suppliers are affected by the disruption at {x['port']} port?",
                "entities": [f"port:{x['port']}"],
            })

    sev = {"critical": 0, "high": 1, "medium": 2}
    problems.sort(key=lambda p: sev.get(p["severity"], 3))
    total_due = sum(v["open_due"] for v in dc_status.values())
    total_bo = sum(v["backordered"] for v in dc_status.values())
    kpis = {
        "ontime_recent": ontime_recent, "ontime_baseline": ontime_base,
        "orders_due_next_week": total_due, "backordered_next_week": total_bo,
        "value_at_risk_inr": sum(v["value_at_risk"] for v in dc_status.values()),
        "late_supplier_units": sum(v["units"] for v in supplier_status.values()),
        "problems": len(problems),
    }
    status = {"lanes": lane_status, "dcs": dc_status, "suppliers": supplier_status, "ports": port_status}
    return {**kpis, "_status": status}, problems


def network(h: DataHarness, status: dict) -> dict:
    """Suppliers -> ports -> DCs -> customer cities, with each node's current status."""
    sup = _rows(h, "SELECT supplier_id, name, brand, port_of_entry FROM procurement.suppliers ORDER BY port_of_entry, supplier_id")
    flows = _rows(h, f"""
        SELECT s.port_of_entry AS port, p.dest_warehouse AS dc, SUM(p.qty) AS units
        FROM procurement.purchase_orders p JOIN procurement.suppliers s ON s.supplier_id = p.supplier_id
        WHERE CAST(p.order_date AS DATE) > date_sub(DATE'{A}', 90) GROUP BY s.port_of_entry, p.dest_warehouse""")
    lanes = _rows(h, "SELECT l.lane_id, l.origin_warehouse, l.dest_city, l.carrier_id, c.name AS carrier "
                     "FROM carrier.lanes l JOIN carrier.carriers c ON c.carrier_id = l.carrier_id ORDER BY l.origin_warehouse, l.dest_city")
    wh = _rows(h, "SELECT warehouse_id, name FROM erp.warehouses ORDER BY warehouse_id")

    def port_state(port: str) -> str | None:
        for name, st in status["ports"].items():
            if name.lower().startswith(port.lower()) or port.lower().startswith(name.lower().split(" ")[0]):
                return st["status"]
        return None

    nodes, edges = [], []
    ports = sorted({x["port_of_entry"] for x in sup}, key=lambda p: (p.startswith("Domestic"), p))
    for x in sup:
        nodes.append({"id": x["supplier_id"], "type": "supplier", "label": x["brand"], "sub": x["supplier_id"],
                      "status": status["suppliers"].get(x["supplier_id"], {}).get("status")})
        edges.append({"from": x["supplier_id"], "to": f"port:{x['port_of_entry']}", "kind": "inbound"})
    for p in ports:
        nodes.append({"id": f"port:{p}", "type": "port", "label": p.replace(" (road)", ""),
                      "sub": "road" if p.startswith("Domestic") else "sea port", "status": port_state(p)})
    for w in wh:
        st = status["dcs"].get(w["warehouse_id"], {})
        nodes.append({"id": w["warehouse_id"], "type": "dc", "label": DC_NAMES.get(w["warehouse_id"], w["name"]),
                      "sub": f"{st.get('backordered', 0)} backordered next wk" if st else w["warehouse_id"],
                      "status": st.get("status")})
    for f in flows:
        edges.append({"from": f"port:{f['port']}", "to": f["dc"], "kind": "inbound", "units": _f(f["units"])})
    for ln in lanes:
        city = f"city:{ln['dest_city']}"
        if not any(n["id"] == city for n in nodes):
            nodes.append({"id": city, "type": "city", "label": ln["dest_city"], "sub": ln["carrier"], "status": None})
        lst = status["lanes"].get(ln["lane_id"], {})
        edges.append({"from": ln["origin_warehouse"], "to": city, "kind": "lane", "id": ln["lane_id"],
                      "carrier": ln["carrier_id"], "status": lst.get("status"),
                      "ontime": lst.get("recent")})
    return {"nodes": nodes, "edges": edges}


# ============================================================================ Olist (real marketplace data)
BR_STATES = {"AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará",
             "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso",
             "MS": "Mato Grosso do Sul", "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
             "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
             "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
             "SE": "Sergipe", "TO": "Tocantins"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def _month_name(m: str) -> str:
    y, mm = m.split("-")
    return f"{MONTHS[int(mm) - 1]} {y}"


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2) if n else 0.0


OLIST_MONTHLY_SQL = """
WITH o AS (SELECT order_id, date_format(order_purchase_timestamp, 'yyyy-MM') AS month,
             CASE WHEN CAST(order_delivered_customer_date AS DATE) > CAST(order_estimated_delivery_date AS DATE) THEN 1 ELSE 0 END AS late,
             datediff(order_delivered_customer_date, order_delivered_carrier_date) AS carrier_days
           FROM oms.orders WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL),
     s AS (SELECT i.order_id, MAX(CASE WHEN o.order_delivered_carrier_date > i.shipping_limit_date THEN 1 ELSE 0 END) AS seller_late
           FROM sellers.order_items i JOIN oms.orders o ON o.order_id = i.order_id GROUP BY i.order_id),
     r AS (SELECT order_id, AVG(review_score) AS score,
             MAX(CASE WHEN lower(review_comment_message) LIKE '%greve%' THEN 1 ELSE 0 END) AS strike
           FROM feedback.reviews GROUP BY order_id)
SELECT o.month, COUNT(*) AS orders, ROUND(100.0 * AVG(o.late), 1) AS late_pct,
       ROUND(100.0 * AVG(s.seller_late), 1) AS seller_late_pct, ROUND(AVG(o.carrier_days), 1) AS carrier_days,
       ROUND(AVG(r.score), 2) AS review_score, SUM(COALESCE(r.strike, 0)) AS strike_mentions
FROM o LEFT JOIN s ON s.order_id = o.order_id LEFT JOIN r ON r.order_id = o.order_id
GROUP BY o.month HAVING COUNT(*) >= 200 ORDER BY o.month"""

OLIST_STATES_SQL = """
SELECT c.customer_state AS state, COUNT(*) AS orders,
       ROUND(100.0 * AVG(CASE WHEN CAST(o.order_delivered_customer_date AS DATE) > CAST(o.order_estimated_delivery_date AS DATE) THEN 1 ELSE 0 END), 1) AS late_pct
FROM oms.orders o JOIN oms.customers c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
GROUP BY c.customer_state HAVING COUNT(*) >= 300 ORDER BY late_pct DESC"""


OLIST_EVENTS_SQL = """SELECT event_id, name, type, start_date, end_date, region, summary, source_urls
FROM signals.events ORDER BY start_date"""

OLIST_REGIONS_SQL = """
WITH o AS (SELECT c.customer_state AS state, date_format(o.order_purchase_timestamp, 'yyyy-MM') AS month,
             CASE WHEN CAST(o.order_delivered_customer_date AS DATE) > CAST(o.order_estimated_delivery_date AS DATE) THEN 1 ELSE 0 END AS late,
             datediff(o.order_delivered_customer_date, o.order_delivered_carrier_date) AS transit
           FROM oms.orders o JOIN oms.customers c ON c.customer_id = o.customer_id
           WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
             AND date_format(o.order_purchase_timestamp, 'yyyy-MM') BETWEEN '{base_start}' AND '{month}')
SELECT state,
       SUM(CASE WHEN month = '{month}' THEN 1 ELSE 0 END) AS orders,
       ROUND(100.0 * AVG(CASE WHEN month = '{month}' THEN late END), 1) AS late_pct,
       ROUND(100.0 * AVG(CASE WHEN month IN ({base_months}) THEN late END), 1) AS base_late_pct,
       ROUND(AVG(CASE WHEN month = '{month}' THEN transit END), 1) AS transit_days,
       ROUND(AVG(CASE WHEN month IN ({base_months}) THEN transit END), 1) AS base_transit_days
FROM o GROUP BY state ORDER BY state"""


def _shift_month(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    i = y * 12 + (m - 1) + n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def olist_regions(h: DataHarness, month: str | None = None) -> dict:
    """Late deliveries by customer state: for one purchase month against the six months before it, or all time."""
    if not month:
        rows = _rows(h, OLIST_STATES_SQL.replace("HAVING COUNT(*) >= 300", "HAVING COUNT(*) >= 50"), limit=40)
        return {"month": None, "baseline": None,
                "states": [{"state": r["state"], "name": BR_STATES.get(r["state"], r["state"]), "orders": int(_f(r["orders"])),
                            "late_pct": _f(r["late_pct"]), "base_late_pct": None, "transit_days": None,
                            "base_transit_days": None} for r in rows]}
    base_start = _shift_month(month, -6)
    # a normal baseline: the six months before, minus any month that was itself abnormal nationally (a peak season or
    # another incident), i.e. late rate above 1.5x the window's median
    window = [_shift_month(month, -k) for k in range(6, 0, -1)]
    national = {t["month"]: t["late_pct"] for t in (
        {"month": r["month"], "late_pct": _f(r["late_pct"])} for r in _rows(h, OLIST_MONTHLY_SQL, limit=100))}
    rates = [national[m] for m in window if m in national]
    med = _median(rates) if rates else 0.0
    base = [m for m in window if m in national and national[m] <= 1.5 * med] or [m for m in window if m in national]
    excluded = [m for m in window if m in national and m not in base]
    rows = _rows(h, OLIST_REGIONS_SQL.format(month=month, base_start=base_start,
                                             base_months=", ".join(f"'{m}'" for m in base) or "''"), limit=40)
    states = [{"state": r["state"], "name": BR_STATES.get(r["state"], r["state"]), "orders": int(_f(r["orders"])),
               "late_pct": _f(r["late_pct"], None), "base_late_pct": _f(r["base_late_pct"], None),
               "transit_days": _f(r["transit_days"], None), "base_transit_days": _f(r["base_transit_days"], None)}
              for r in rows if _f(r["orders"]) > 0]
    label = f"{_month_name(window[0])} to {_month_name(window[-1])}"
    if excluded:
        label += ", leaving out " + " and ".join(_month_name(m) for m in excluded) + " (abnormal nationally)"
    return {"month": month, "baseline": label, "baseline_months": base, "excluded_months": excluded, "states": states}


def olist_overview(h: DataHarness) -> dict:
    monthly = _rows(h, OLIST_MONTHLY_SQL, limit=100)
    timeline = [{"month": m["month"], "orders": int(_f(m["orders"])), "late_pct": _f(m["late_pct"]),
                 "seller_late_pct": _f(m["seller_late_pct"]), "carrier_days": _f(m["carrier_days"]),
                 "review_score": _f(m["review_score"]), "strike_mentions": int(_f(m["strike_mentions"]))}
                for m in monthly]
    problems: list[dict] = []
    total_orders = max(sum(t["orders"] for t in timeline), 1)
    if timeline:
        med_late = _median([t["late_pct"] for t in timeline])
        med_seller = _median([t["seller_late_pct"] for t in timeline])
        avg_score = sum(t["review_score"] * t["orders"] for t in timeline) / total_orders
        for t in timeline:
            if t["orders"] < 1000:  # the platform's first months: too few orders to call an incident
                continue
            name = _month_name(t["month"])
            if t["late_pct"] >= max(10.0, 2 * med_late):
                problems.append({
                    "id": f"late:{t['month']}", "kind": "transport", "when": t["month"],
                    "severity": "critical" if t["late_pct"] >= 15 else "high",
                    "title": f"Late deliveries spiked in {name}", "metric": f"{t['late_pct']:.1f}% late",
                    "baseline": f"usually {med_late:.1f}%",
                    "detail": f"{t['orders']:,} orders placed that month; carrier transit averaged {t['carrier_days']} days.",
                    "question": f"Why did late deliveries jump to {round(t['late_pct'])}% for orders placed in {name}?",
                    "entities": [t["month"]]})
            if t["seller_late_pct"] >= max(10.0, 1.5 * med_seller):
                problems.append({
                    "id": f"seller:{t['month']}", "kind": "supply", "when": t["month"], "severity": "high",
                    "title": f"Sellers missed hand-over deadlines in {name}",
                    "metric": f"{t['seller_late_pct']:.1f}% of orders", "baseline": f"usually {med_seller:.1f}%",
                    "detail": "Orders handed to the carrier after the seller's shipping deadline.",
                    "question": f"Why did sellers hand orders to carriers late in {name}?", "entities": [t["month"]]})
            if t["review_score"] <= avg_score - 0.25:
                problems.append({
                    "id": f"reviews:{t['month']}", "kind": "demand", "when": t["month"], "severity": "medium",
                    "title": f"Review scores fell in {name}", "metric": f"{t['review_score']:.2f} / 5",
                    "baseline": f"average {avg_score:.2f}",
                    "detail": "Average customer review score for orders placed that month.",
                    "question": f"Why did customer review scores fall in {name}?", "entities": [t["month"]]})
            if t["strike_mentions"] >= 10:
                problems.append({
                    "id": f"strike:{t['month']}", "kind": "external", "when": t["month"], "severity": "medium",
                    "title": f"Customers mention strikes in {name}", "metric": f"{t['strike_mentions']} reviews",
                    "baseline": "mention 'greve' (strike)",
                    "detail": "Reviews of orders placed that month that mention a strike.",
                    "question": f"Did strikes delay deliveries for orders placed in {name}, and by how much?",
                    "entities": [t["month"]]})
    states = _rows(h, OLIST_STATES_SQL, limit=30)
    overall = sum(t["late_pct"] * t["orders"] for t in timeline) / total_orders if timeline else 0.0
    for s in states[:4]:
        if _f(s["late_pct"]) >= 1.6 * overall:
            nm = BR_STATES.get(s["state"], s["state"])
            problems.append({
                "id": f"state:{s['state']}", "kind": "fulfilment", "when": None, "severity": "high",
                "title": f"Deliveries to {nm} are often late", "metric": f"{_f(s['late_pct']):.1f}% late",
                "baseline": f"national {overall:.1f}%", "detail": f"{int(_f(s['orders'])):,} delivered orders, all time.",
                "question": f"Why are deliveries to {nm} ({s['state']}) late so often?", "entities": [s["state"]]})
    sev = {"critical": 0, "high": 1, "medium": 2}
    problems.sort(key=lambda p: (sev.get(p["severity"], 3), -(int(p["when"].replace("-", "")) if p.get("when") else 0)))

    recent, prev = timeline[-3:], timeline[-6:-3]

    def wavg(rows, key):
        n = sum(r["orders"] for r in rows)
        return sum(r[key] * r["orders"] for r in rows) / n if n else None

    worst = max(timeline, key=lambda t: t["late_pct"]) if timeline else None
    r_late, p_late = wavg(recent, "late_pct"), wavg(prev, "late_pct")
    r_score, p_score = wavg(recent, "review_score"), wavg(prev, "review_score")
    cards = [
        {"label": "Late deliveries · last 3 months", "value": f"{r_late:.1f}%" if r_late is not None else "—",
         "sub": f"{p_late:.1f}% the 3 months before" if p_late is not None else "",
         "tone": "bad" if (r_late or 0) > (p_late or 0) else None},
        {"label": "Orders delivered · last 3 months", "value": f"{sum(r['orders'] for r in recent):,}",
         "sub": f"{sum(t['orders'] for t in timeline):,} since {_month_name(timeline[0]['month']) if timeline else ''}",
         "tone": None},
        {"label": "Avg review score · last 3 months", "value": f"{r_score:.2f}" if r_score is not None else "—",
         "sub": f"{p_score:.2f} the 3 months before" if p_score is not None else "", "tone": None},
        {"label": "Worst month for delays", "value": f"{worst['late_pct']:.1f}%" if worst else "—",
         "sub": _month_name(worst["month"]) if worst else "", "tone": "bad"},
    ]
    months = {t["month"] for t in timeline}
    events = [{"id": e["event_id"], "name": e["name"], "type": e["type"], "start": str(e["start_date"])[:10],
               "end": str(e["end_date"])[:10] if e.get("end_date") else None, "region": e["region"],
               "summary": e["summary"], "sources": [u.strip() for u in str(e.get("source_urls") or "").split(",") if u.strip()],
               "month": str(e["start_date"])[:7]}
              for e in _rows(h, OLIST_EVENTS_SQL, limit=200)]
    events = [e for e in events if e["month"] in months]
    return {"kpi_cards": cards, "problems": problems, "timeline": timeline, "network": None, "events": events,
            "states": [{"state": s["state"], "name": BR_STATES.get(s["state"], s["state"]),
                        "orders": int(_f(s["orders"])), "late_pct": _f(s["late_pct"])} for s in states]}


def kaveri_cards(k: dict) -> list[dict]:
    def pct(v):
        return f"{v:.0%}" if v is not None else "—"

    var = k["value_at_risk_inr"]
    return [
        {"label": "On-time delivery · 30 days", "value": pct(k["ontime_recent"]),
         "sub": f"{pct(k['ontime_baseline'])} two months ago",
         "tone": "bad" if (k["ontime_baseline"] or 0) - (k["ontime_recent"] or 0) > 0.05 else None},
        {"label": "Orders due next 7 days", "value": f"{k['orders_due_next_week']:,}",
         "sub": f"{k['backordered_next_week']:,} backordered", "tone": None},
        {"label": "Order value at risk", "value": f"₹{var / 1e7:.2f} Cr" if var >= 1e7 else f"₹{var / 1e5:.1f} L",
         "sub": "backordered, due next week", "tone": "bad" if var > 0 else None},
        {"label": "Inbound units running late", "value": f"{k['late_supplier_units']:,}",
         "sub": "supplier ETA past the agreed date", "tone": None},
    ]


def overview(h: DataHarness) -> dict:
    ds = getattr(h, "dataset", None)
    as_of = getattr(h, "as_of", AS_OF)
    if ds is not None and ds.key == "olist":
        return {"as_of": as_of, "harness": h.name, "dataset": ds.public(), **olist_overview(h)}
    kpis, problems = kpis_and_problems(h)
    status = kpis.pop("_status")
    return {"as_of": as_of, "harness": h.name, "dataset": ds.public() if ds else None, "kpis": kpis,
            "kpi_cards": kaveri_cards(kpis), "problems": problems, "network": network(h, status), "timeline": None}
