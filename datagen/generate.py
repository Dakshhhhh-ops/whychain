"""
Generate one fictional company's data, split across separate systems the way
it would be in real life:

  ERP (Postgres)            warehouses, customers, skus, sales_orders, order_lines
  Warehouse system (CSV)    inventory_snapshots.csv, reorder_policy.csv
  Carrier feed (JSON)       shipments.json, lanes.json, carriers.json
  Procurement (CSV)         purchase_orders.csv, suppliers.csv
  External signals (JSON)   port_status.json, weather_alerts.json
  Documents (Markdown)      contracts/, emails/, policies/

Problems are planted by simulating their causes (a port strike, a carrier
service change, a promotion planning never heard about, an EDI bug) and then
running the business day by day. No answers are written into the data; the
agents have to find the causes.

Usage:  python -m datagen.generate [--seed 7] [--out data/generated]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from datagen import docs_content

AS_OF = date(2026, 10, 21)
START = AS_OF - timedelta(days=90)
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "generated"

# Planted events
STRIKE_START = date(2026, 10, 9)      # Chennai port strike begins
PORT_RELEASE = date(2026, 10, 30)     # forwarder's expected container release
NASHIK_CHANGE = date(2026, 9, 1)      # SwiftCargo moves Nashik lane to consolidated service
PROMO_START, PROMO_END = date(2026, 10, 8), date(2026, 10, 18)  # DIWALI26 earbuds promo
EDI_BUG_START = date(2026, 10, 16)    # EDI connector starts duplicating orders
FESTIVE_START = date(2026, 10, 1)     # general festive demand lift
SAFETY_DAYS = 9

WAREHOUSES = [
    {"warehouse_id": "MUM", "name": "Mumbai DC (Bhiwandi)", "city": "Bhiwandi", "state": "Maharashtra"},
    {"warehouse_id": "PUN", "name": "Pune DC (Chakan)", "city": "Chakan", "state": "Maharashtra"},
    {"warehouse_id": "CHN", "name": "Chennai DC (Sriperumbudur)", "city": "Sriperumbudur", "state": "Tamil Nadu"},
]
INLAND_DAYS_FROM_CHENNAI_PORT = {"CHN": 1, "PUN": 4, "MUM": 4}
INLAND_DAYS_FROM_NHAVA_SHEVA = {"CHN": 4, "PUN": 2, "MUM": 1}

# dest city -> (warehouse, carrier, standard transit days)
LANES = {
    "Mumbai": ("MUM", "C01", 1), "Thane": ("MUM", "C03", 1), "Nashik": ("MUM", "C02", 1), "Surat": ("MUM", "C03", 2),
    "Pune": ("PUN", "C01", 1), "Kolhapur": ("PUN", "C04", 2), "Aurangabad": ("PUN", "C04", 2),
    "Chennai": ("CHN", "C01", 1), "Bengaluru": ("CHN", "C04", 2), "Coimbatore": ("CHN", "C03", 2),
    "Hyderabad": ("CHN", "C02", 2),
}
CITY_CODE = {"Mumbai": "BOM", "Thane": "THN", "Nashik": "NSK", "Surat": "STV", "Pune": "PNQ", "Kolhapur": "KLH",
             "Aurangabad": "IXU", "Chennai": "MAA", "Bengaluru": "BLR", "Coimbatore": "CJB", "Hyderabad": "HYD"}
TRANSFER_LANES = [  # from, to, transit days, INR per truck, units per truck
    ("MUM", "PUN", 1, 14000, 1800), ("PUN", "MUM", 1, 14000, 1800),
    ("MUM", "CHN", 3, 52000, 1800), ("CHN", "PUN", 3, 49000, 1800),
]
EXPEDITE_OPTIONS = [  # supplier, mode, arrives at, lead days, category, INR surcharge per unit, minimum units
    ("S-14", "AIR", "MUM", 6, "Chargers", 95, 500), ("S-14", "AIR", "MUM", 6, "Power Banks", 140, 500),
    ("S-07", "AIR", "MUM", 5, "Earbuds", 85, 100),
]
CARRIERS = [
    {"carrier_id": "C01", "name": "BlueLine Express", "agreement_version": "v4", "agreement_effective": "2026-04-01"},
    {"carrier_id": "C02", "name": "SwiftCargo Logistics", "agreement_version": "v2", "agreement_effective": "2026-09-01"},
    {"carrier_id": "C03", "name": "Deccan Freight", "agreement_version": "v1", "agreement_effective": "2025-11-15"},
    {"carrier_id": "C04", "name": "RapidRoute", "agreement_version": "v3", "agreement_effective": "2026-02-01"},
]


@dataclass
class Supplier:
    supplier_id: str
    name: str
    brand: str
    country: str
    port_of_entry: str
    lead_time_days: int
    eta_sd_days: float


SUPPLIERS = [
    Supplier("S-02", "Kiran Peripherals Pvt Ltd", "Kiran", "India", "Domestic (road)", 7, 1.0),
    Supplier("S-03", "Unicable Industries", "Unicable", "India", "Domestic (road)", 6, 1.0),
    Supplier("S-05", "Smartnest Devices Ltd", "Smartnest", "Taiwan", "Nhava Sheva", 20, 1.5),
    Supplier("S-07", "SoundWave Acoustics Co.", "SoundWave", "China", "Nhava Sheva", 18, 1.5),
    Supplier("S-09", "NetLink Systems", "NetLink", "China", "Nhava Sheva", 24, 2.0),
    Supplier("S-11", "Bharat Wire & Cable", "BharatWire", "India", "Domestic (road)", 5, 1.0),
    Supplier("S-12", "Auralis Audio", "Auralis", "Vietnam", "Nhava Sheva", 22, 2.0),
    Supplier("S-14", "Voltix Power Components", "Voltix", "China", "Chennai", 21, 1.2),
    Supplier("S-16", "Keystone Input Devices", "Keystone", "India", "Domestic (road)", 8, 1.0),
    Supplier("S-19", "Pulse Earwear Ltd", "Pulse", "China", "Nhava Sheva", 19, 1.5),
    Supplier("S-21", "Chronofit Wearables", "Chronofit", "China", "Chennai", 23, 2.0),
]
SUP = {s.supplier_id: s for s in SUPPLIERS}

# (category, prefix, supplier, variants, price range, popularity)
CATALOG = [
    ("Chargers", "CHG", "S-14", ["20W USB-C Charger", "33W Dual-Port Charger", "65W GaN Charger", "36W Car Charger",
                                 "15W Wireless Pad", "3-in-1 Wireless Stand", "45W PD Charger", "100W Desk Charger",
                                 "Universal Travel Adapter", "Magnetic Wireless Charger"], (450, 2400), 3.0),
    ("Power Banks", "PWB", "S-14", ["10000mAh Slim", "20000mAh PD", "10000mAh Magnetic", "30000mAh Laptop",
                                    "5000mAh Mini", "20000mAh Solar", "25000mAh 65W", "10000mAh Kids"], (900, 4200), 3.0),
    ("Cables", "CBL", "S-03", ["USB-C to C 1m", "USB-C to C 2m", "Lightning 1m", "Micro-USB 1m", "HDMI 2.1 2m",
                               "USB-C Braided 1.5m", "Ethernet Cat6 3m", "Aux 3.5mm 1m"], (150, 900), 2.0),
    ("Cables", "CBL", "S-11", ["USB-A to C 1m", "Lightning Braided 2m", "HDMI 1.4 1.5m", "Ethernet Cat6 10m",
                               "Power Cord 3-pin"], (120, 600), 1.4),
    ("Earbuds", "EBD", "S-07", ["Air Pro TWS", "Bass Boost TWS", "ANC Pro", "Sport Neckband", "Lite TWS",
                                "Gaming TWS", "Open-Ear Clip", "Kids Safe Buds"], (1200, 6500), 2.0),
    ("Earbuds", "EBD", "S-19", ["Beat TWS", "Neckband X", "ANC Lite", "Studio Buds", "Run Buds"], (900, 4200), 1.5),
    ("Smart Plugs", "SPL", "S-05", ["Wi-Fi Plug 10A", "Wi-Fi Plug 16A", "Smart Strip 4-way", "Energy Monitor Plug",
                                    "Matter Plug"], (700, 2200), 1.0),
    ("Routers", "RTR", "S-09", ["AX1800 Router", "AX3000 Mesh (2-pack)", "4G LTE Router", "AC1200 Router",
                                "Travel Router"], (1800, 9500), 1.0),
    ("Speakers", "SPK", "S-12", ["Mini BT Speaker", "Party Speaker 40W", "Soundbar 2.1", "Smart Speaker",
                                 "Waterproof Speaker", "Desktop Speakers"], (1500, 12000), 1.2),
    ("Keyboards & Mice", "KBM", "S-02", ["Wireless Combo", "Mechanical Keyboard", "Ergonomic Mouse",
                                         "Silent Mouse", "Gaming Mouse"], (400, 3800), 1.0),
    ("Keyboards & Mice", "KBM", "S-16", ["Wired Keyboard", "Bluetooth Keyboard", "Wireless Mouse", "Mouse Pad XL"],
     (250, 2600), 0.8),
    ("Smartwatches", "SWT", "S-21", ["Fit Band 5", "Active Watch", "AMOLED Watch Pro", "Kids GPS Watch",
                                     "Calling Watch"], (2500, 15000), 1.0),
]

FIXED_CUSTOMERS = [
    # name, city, tier, channel
    ("Shree Ganesh Electronics Retail", "Pune", "A", "PORTAL"),
    ("Metro Mobile World", "Mumbai", "A", "PORTAL"),
    ("Coastal Digital Mart", "Chennai", "A", "EDI"),
    ("TechBazaar Retail", "Bengaluru", "A", "EDI"),
    ("Hyderabad Gadget Hub", "Hyderabad", "A", "EDI"),
    ("Godavari Mobiles", "Nashik", "B", "SALES_REP"),
    ("Nashik Digital World", "Nashik", "B", "PORTAL"),
]
CUSTOMERS_PER_CITY = {"Mumbai": 7, "Thane": 4, "Nashik": 4, "Surat": 4, "Pune": 8, "Kolhapur": 3, "Aurangabad": 3,
                      "Chennai": 6, "Bengaluru": 6, "Coimbatore": 3, "Hyderabad": 4}
NAME_A = ["Shree", "Sai", "New", "Royal", "Metro", "Star", "Sunrise", "Galaxy", "Om", "Lakshmi", "Prime", "City",
          "Smart", "Vision", "Krishna", "Balaji", "Modern", "Nova", "Urban", "Classic"]
NAME_B = ["Electronics", "Mobile World", "Gadgets", "Digital Mart", "Traders", "Tech Store", "Communications",
          "Retail", "Mobiles", "Enterprises", "Digital Hub", "Tele Care"]
ORDER_RATE = {"A": 1.3, "B": 0.55, "C": 0.25}
QTY_RANGE = {"A": (20, 120), "B": (10, 60), "C": (5, 30)}


def d2s(d: date | None) -> str | None:
    return d.isoformat() if d else None


def next_weekday(d: date, weekdays: set[int]) -> date:
    while d.weekday() not in weekdays:
        d += timedelta(days=1)
    return d


class World:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.skus: list[dict] = []
        self.customers: list[dict] = []
        self.orders: list[dict] = []
        self.pos: list[dict] = []
        self.snapshots: list[dict] = []
        self.shipments: list[dict] = []
        self.rop: dict[tuple[str, str], dict] = {}
        self._po_seq = 40000
        self._ord_seq = 100000
        self._shp_seq = 700000

    # ------------------------------------------------------------------ master data
    def build_master(self) -> None:
        rng = self.rng
        n = 1000
        for category, prefix, sup_id, variants, (lo, hi), pop in CATALOG:
            for v in variants:
                n += 1
                price = round(rng.uniform(lo, hi) / 10) * 10 - 1
                self.skus.append({
                    "sku": f"{prefix}-{n}",
                    "name": f"{SUP[sup_id].brand} {v}",
                    "category": category,
                    "supplier_id": sup_id,
                    "unit_cost": round(price * rng.uniform(0.55, 0.7), 2),
                    "list_price": float(price),
                    "case_pack": 10 if price > 3000 else 25,
                    "_pop": pop * rng.uniform(0.5, 1.5),
                })
        used = set()
        cid = 1000
        for name, city, tier, channel in FIXED_CUSTOMERS:
            cid += 1
            used.add(name)
            self.customers.append(self._customer(cid, name, city, tier, channel))
        for city, count in CUSTOMERS_PER_CITY.items():
            existing = sum(1 for c in self.customers if c["city"] == city)
            for _ in range(count - existing):
                while True:
                    name = f"{rng.choice(NAME_A)} {rng.choice(NAME_B)}"
                    if name not in used:
                        break
                used.add(name)
                cid += 1
                tier = rng.choices(["A", "B", "C"], [0.1, 0.4, 0.5])[0]
                channel = rng.choices(["PORTAL", "SALES_REP", "EDI"], [0.55, 0.4, 0.05])[0]
                self.customers.append(self._customer(cid, name, city, tier, channel))

    def _customer(self, cid, name, city, tier, channel):
        wh = LANES[city][0]
        return {"customer_id": f"CU-{cid}", "name": name, "city": city, "region": wh_region(wh), "tier": tier,
                "channel": channel, "default_warehouse": wh, "credit_limit_inr": {"A": 5_000_000, "B": 1_500_000,
                                                                                    "C": 500_000}[tier]}

    # ------------------------------------------------------------------ demand
    def sku_weight(self, sku: dict, wh: str) -> float:
        w = sku["_pop"]
        if wh == "PUN" and sku["supplier_id"] == "S-14":
            w *= 1.8  # power accessories are Pune's biggest category
        return w

    def generate_orders(self) -> None:
        rng = self.rng
        weights = {wh["warehouse_id"]: [self.sku_weight(s, wh["warehouse_id"]) for s in self.skus] for wh in WAREHOUSES}
        s07_earbuds = [s for s in self.skus if s["supplier_id"] == "S-07"]
        d = START
        while d <= AS_OF:
            dow_factor = 0.35 if d.weekday() == 6 else (0.8 if d.weekday() == 5 else 1.0)
            festive = 1.15 if d >= FESTIVE_START else 1.0
            for c in self.customers:
                lam = ORDER_RATE[c["tier"]] * dow_factor * festive
                for _ in range(poisson(rng, lam)):
                    self._make_order(c, d, weights, s07_earbuds)
            d += timedelta(days=1)

    def _make_order(self, c, d, weights, s07_earbuds):
        rng = self.rng
        wh = c["default_warehouse"]
        self._ord_seq += 1
        n_lines = rng.choices([1, 2, 3, 4], [0.35, 0.35, 0.2, 0.1])[0]
        chosen = []
        while len(chosen) < n_lines:
            s = rng.choices(self.skus, weights[wh])[0]
            if s not in chosen:
                chosen.append(s)
        promo = None
        in_promo = wh == "MUM" and PROMO_START <= d <= PROMO_END
        if in_promo and rng.random() < 0.38:
            s = rng.choice(s07_earbuds)
            if s not in chosen:
                chosen.append(s)
        lo, hi = QTY_RANGE[c["tier"]]
        lines = []
        for i, s in enumerate(chosen, 1):
            qty = int(round(rng.randint(lo, hi) / 5) * 5) or 5
            disc = 0.0
            if in_promo and s["supplier_id"] == "S-07":
                promo = "DIWALI26"
                disc = 25.0
                qty = int(qty * 1.3)
            price = s["list_price"]
            lines.append({"line_no": i, "sku": s["sku"], "qty": qty, "unit_price": price, "discount_pct": disc,
                          "line_value": round(qty * price * (1 - disc / 100), 2)})
        city = c["city"]
        transit = LANES[city][2]
        lead = rng.choices([2, 3, 4, 5, 7, 10, 14], [0.15, 0.25, 0.2, 0.15, 0.12, 0.08, 0.05])[0]
        lead = max(lead, transit + 1)
        promised = d + timedelta(days=lead)
        created = datetime.combine(d, time(rng.randint(8, 19), rng.randint(0, 59), rng.randint(0, 59)))
        ext_ref = None
        if c["channel"] == "EDI":
            ext_ref = f"{''.join(w[0] for w in c['name'].split())[:3].upper()}-PO-{rng.randint(50000, 99999)}"
        elif c["channel"] == "PORTAL":
            ext_ref = f"WEB-{rng.randint(100000, 999999)}"
        self.orders.append({
            "order_id": f"SO-{self._ord_seq}", "external_ref": ext_ref, "customer_id": c["customer_id"],
            "warehouse_id": wh, "dest_city": city, "channel": c["channel"], "order_date": d, "created_at": created,
            "promised_date": promised, "requested_ship_date": promised - timedelta(days=transit),
            # the DC ships up to 2 days ahead of the requested date when stock is ready
            "_earliest_ship": max(d, promised - timedelta(days=transit + rng.choices([0, 1, 2], [0.45, 0.35, 0.2])[0])),
            "promo_code": promo, "lines": lines, "shipped": None, "cancelled": rng.random() < 0.01,
        })

    # ------------------------------------------------------------------ policy
    def build_reorder_policy(self) -> None:
        """Reorder points were set in late August from the July-August baseline and never revisited."""
        baseline_end = date(2026, 8, 31)
        days = (baseline_end - START).days + 1
        demand = defaultdict(float)
        for o in self.orders:
            if o["order_date"] <= baseline_end and not o["cancelled"]:
                for ln in o["lines"]:
                    demand[(o["warehouse_id"], ln["sku"])] += ln["qty"]
        for wh in WAREHOUSES:
            for s in self.skus:
                key = (wh["warehouse_id"], s["sku"])
                avg = max(demand[key] / days, 0.5)
                lead = SUP[s["supplier_id"]].lead_time_days + 2
                safety = avg * SAFETY_DAYS
                rop = math.ceil(avg * lead + safety)
                self.rop[key] = {"warehouse_id": key[0], "sku": key[1], "avg_daily_demand_baseline": round(avg, 2),
                                 "safety_stock": math.ceil(safety), "reorder_point": rop,
                                 "order_up_to": math.ceil(rop + avg * 21), "last_reviewed": "2026-08-31",
                                 "next_review": "2026-11-30"}

    # ------------------------------------------------------------------ procurement
    def make_po(self, d: date, wh: str, sku: dict, qty: int, kind="REPLENISHMENT", expected: date | None = None,
                noise: bool = True) -> dict:
        s = SUP[sku["supplier_id"]]
        self._po_seq += 1
        expected = expected or d + timedelta(days=s.lead_time_days)
        actual = expected + timedelta(days=max(-2, round(self.rng.gauss(0.3, s.eta_sd_days)))) if noise else expected
        eta = expected
        strike_hit = False
        if s.port_of_entry == "Chennai":
            inland = INLAND_DAYS_FROM_CHENNAI_PORT[wh]
            port_arrival = actual - timedelta(days=inland)
            if STRIKE_START <= port_arrival < PORT_RELEASE:
                actual = PORT_RELEASE + timedelta(days=inland + self.rng.randint(0, 1))
                eta = PORT_RELEASE + timedelta(days=inland)
                strike_hit = True
        po = {"po_id": f"PO-{self._po_seq}", "supplier_id": s.supplier_id, "sku": sku["sku"], "dest_warehouse": wh,
              "qty": qty, "unit_cost": sku["unit_cost"], "order_date": d, "expected_date": expected,
              "_actual": actual, "_eta": eta, "_strike": strike_hit, "port_of_entry": s.port_of_entry,
              "incoterm": "CIF" if s.country != "India" else "DAP", "po_type": kind}
        self.pos.append(po)
        return po

    # ------------------------------------------------------------------ simulation
    def simulate(self) -> None:
        rng = self.rng
        on_hand: dict[tuple[str, str], int] = {}
        for key, pol in self.rop.items():
            on_hand[key] = int(pol["avg_daily_demand_baseline"] * rng.uniform(22, 30))
        sku_by_id = {s["sku"]: s for s in self.skus}
        on_order = defaultdict(int)
        # Seasonal pre-build for Voltix (S-14) power accessories ahead of Diwali, ordered 10 Sep.
        prebuild_day = date(2026, 9, 10)
        prebuild_plan = {"MUM": (date(2026, 10, 5), 50), "PUN": (date(2026, 10, 13), 30), "CHN": (date(2026, 10, 12), 30)}
        orders_by_day = defaultdict(list)
        for o in self.orders:
            if not o["cancelled"]:
                orders_by_day[o["order_date"]].append(o)
        pending: dict[str, list[dict]] = defaultdict(list)

        d = START
        while d <= AS_OF:
            # 1. receipts
            for po in self.pos:
                if po["_actual"] == d:
                    key = (po["dest_warehouse"], po["sku"])
                    on_hand[key] += po["qty"]
                    on_order[key] -= po["qty"]
                    po["received_date"] = d
            # 2. new orders
            for o in orders_by_day.get(d, []):
                pending[o["warehouse_id"]].append(o)
            # 3. ship complete orders whose ship day has come
            for wh, plist in pending.items():
                due = sorted((o for o in plist if o["_earliest_ship"] <= d),
                             key=lambda o: (o["promised_date"], o["created_at"]))
                for o in due:
                    if all(on_hand[(wh, ln["sku"])] >= ln["qty"] for ln in o["lines"]):
                        for ln in o["lines"]:
                            on_hand[(wh, ln["sku"])] -= ln["qty"]
                        self._ship(o, d)
                pending[wh] = [o for o in plist if not o["shipped"]]
            # 4. replenish
            if d == prebuild_day:
                for wh, (exp, days_cover) in prebuild_plan.items():
                    for s in self.skus:
                        if s["supplier_id"] == "S-14":
                            avg = self.rop[(wh, s["sku"])]["avg_daily_demand_baseline"]
                            qty = case_round(avg * days_cover, s["case_pack"])
                            po = self.make_po(d, wh, s, qty, "SEASONAL_PREBUILD", expected=exp, noise=False)
                            on_order[(wh, s["sku"])] += po["qty"]
            for key, pol in self.rop.items():
                if on_hand[key] + on_order[key] <= pol["reorder_point"]:
                    s = sku_by_id[key[1]]
                    qty = case_round(pol["order_up_to"] - on_hand[key] - on_order[key], s["case_pack"])
                    po = self.make_po(d, key[0], s, qty)
                    on_order[key] += po["qty"]
            # 5. end-of-day snapshot
            waiting = defaultdict(int)
            for wh, plist in pending.items():
                for o in plist:
                    if o["requested_ship_date"] <= d:
                        for ln in o["lines"]:
                            waiting[(wh, ln["sku"])] += ln["qty"]
            for key, pol in self.rop.items():
                self.snapshots.append({"snapshot_date": d, "warehouse_id": key[0], "sku": key[1],
                                       "on_hand": on_hand[key], "on_order": on_order[key],
                                       "qty_on_overdue_orders": waiting[key],
                                       "reorder_point": pol["reorder_point"]})
            d += timedelta(days=1)

        self._final_status(pending, on_hand)

    def _ship(self, o: dict, d: date) -> None:
        rng = self.rng
        wh, city = o["warehouse_id"], o["dest_city"]
        carrier, transit = LANES[city][1], LANES[city][2]
        service = "FTL"
        pickup = d
        depart = d
        if city == "Nashik" and d >= NASHIK_CHANGE:
            service = "CONSOLIDATED"
            depart = next_weekday(d, {1, 4})  # milk run leaves Tue and Fri
            transit = 2
        extra = rng.choices([0, 1, 2], [0.93, 0.06, 0.01])[0]
        delivered = depart + timedelta(days=transit + extra)
        self._shp_seq += 1
        o["shipped"] = d
        self.shipments.append({
            "shipment_id": f"SHP-{self._shp_seq}", "order_id": o["order_id"], "carrier_id": carrier,
            "lane_id": f"{wh}-{CITY_CODE[city]}", "origin_warehouse": wh, "dest_city": city,
            "service_type": service, "pickup_date": pickup, "departed_date": depart,
            "promised_delivery_date": o["promised_date"], "delivered_date": delivered,
        })

    def _final_status(self, pending, on_hand) -> None:
        available = dict(on_hand)
        unshipped = [o for plist in pending.values() for o in plist]
        unshipped.sort(key=lambda o: (o["requested_ship_date"], o["promised_date"], o["created_at"]))
        for o in unshipped:
            wh = o["warehouse_id"]
            short = False
            for ln in o["lines"]:
                key = (wh, ln["sku"])
                if available.get(key, 0) >= ln["qty"]:
                    available[key] -= ln["qty"]
                    ln["allocated_qty"], ln["backordered_qty"] = ln["qty"], 0
                else:
                    ln["allocated_qty"], ln["backordered_qty"] = 0, ln["qty"]
                    short = True
            o["status"] = "BACKORDERED" if short else "OPEN"
        ship_by_order = {s["order_id"]: s for s in self.shipments}
        for o in self.orders:
            if o["cancelled"]:
                o["status"] = "CANCELLED"
                for ln in o["lines"]:
                    ln["allocated_qty"], ln["backordered_qty"] = 0, 0
            elif o["shipped"]:
                s = ship_by_order[o["order_id"]]
                o["status"] = "DELIVERED" if s["delivered_date"] <= AS_OF else "SHIPPED"
                for ln in o["lines"]:
                    ln["allocated_qty"], ln["backordered_qty"] = ln["qty"], 0
        for s in self.shipments:
            if s["delivered_date"] > AS_OF:
                s["status"] = "IN_TRANSIT"
                s["carrier_eta"] = s["delivered_date"]
                s["delivered_date"] = None
            else:
                s["status"] = "DELIVERED"
                s["carrier_eta"] = None

    def add_edi_duplicates(self) -> int:
        """EDI connector v3.2 retries on timeout without checking for an existing order -> duplicates."""
        rng = self.rng
        dupes = []
        edi_customers = {c["customer_id"] for c in self.customers if c["channel"] == "EDI"}
        for o in self.orders:
            if o["customer_id"] in edi_customers and o["order_date"] >= EDI_BUG_START and not o["cancelled"]:
                for k in range(rng.choices([0, 1, 2], [0.1, 0.55, 0.35])[0]):
                    self._ord_seq += 1
                    dup = dict(o)
                    dup["order_id"] = f"SO-{self._ord_seq}"
                    dup["created_at"] = o["created_at"] + timedelta(seconds=rng.randint(35, 140) * (k + 1))
                    dup["status"] = "OPEN"
                    dup["lines"] = [dict(ln, allocated_qty=0, backordered_qty=0) for ln in o["lines"]]
                    dupes.append(dup)
        self.orders.extend(dupes)
        self.orders.sort(key=lambda o: o["created_at"])
        return len(dupes)


def wh_region(wh: str) -> str:
    return {"MUM": "West", "PUN": "West", "CHN": "South"}[wh]


def poisson(rng: random.Random, lam: float) -> int:
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def case_round(qty: float, pack: int) -> int:
    return max(pack, int(math.ceil(qty / pack) * pack))


# ---------------------------------------------------------------------- writers
def write_all(w: World, out: Path, n_dupes: int) -> dict:
    if out.exists():
        shutil.rmtree(out)
    for sub in ["erp", "wms", "carrier", "procurement", "external", "docs/contracts", "docs/emails", "docs/policies"]:
        (out / sub).mkdir(parents=True, exist_ok=True)

    # ERP
    pd.DataFrame(WAREHOUSES).to_csv(out / "erp/warehouses.csv", index=False)
    pd.DataFrame(w.customers).to_csv(out / "erp/customers.csv", index=False)
    pd.DataFrame([{k: v for k, v in s.items() if not k.startswith("_")} for s in w.skus]).to_csv(
        out / "erp/skus.csv", index=False)
    orders, lines = [], []
    for o in w.orders:
        value = round(sum(ln["line_value"] for ln in o["lines"]), 2)
        orders.append({"order_id": o["order_id"], "external_ref": o["external_ref"], "customer_id": o["customer_id"],
                       "warehouse_id": o["warehouse_id"], "channel": o["channel"], "order_date": d2s(o["order_date"]),
                       "created_at": o["created_at"].isoformat(sep=" "), "promised_date": d2s(o["promised_date"]),
                       "requested_ship_date": d2s(o["requested_ship_date"]), "status": o["status"],
                       "promo_code": o["promo_code"], "order_value_inr": value})
        for ln in o["lines"]:
            lines.append({"order_id": o["order_id"], **{k: ln[k] for k in
                          ["line_no", "sku", "qty", "unit_price", "discount_pct", "line_value", "allocated_qty",
                           "backordered_qty"]}})
    pd.DataFrame(orders).to_csv(out / "erp/sales_orders.csv", index=False)
    pd.DataFrame(lines).to_csv(out / "erp/order_lines.csv", index=False)

    # Warehouse system
    snaps = pd.DataFrame(w.snapshots)
    snaps["snapshot_date"] = snaps["snapshot_date"].map(d2s)
    snaps.to_csv(out / "wms/inventory_snapshots.csv", index=False)
    pd.DataFrame(list(w.rop.values())).to_csv(out / "wms/reorder_policy.csv", index=False)
    # Transfer lane rates (the same figures as policy OPS-POL-007)
    pd.DataFrame(TRANSFER_LANES, columns=["from_warehouse", "to_warehouse", "transit_days", "cost_per_truck_inr",
                                          "units_per_truck"]).to_csv(out / "wms/transfer_lanes.csv", index=False)

    # Carrier feed (newline-delimited JSON, the format most feeds and Spark use)
    write_jsonl(out / "carrier/shipments.json", [{k: (d2s(v) if isinstance(v, date) else v) for k, v in s.items()}
                                                 for s in w.shipments])
    write_jsonl(out / "carrier/carriers.json", CARRIERS)
    write_jsonl(out / "carrier/lanes.json", [
        {"lane_id": f"{wh}-{CITY_CODE[c]}", "origin_warehouse": wh, "dest_city": c, "carrier_id": car,
         "standard_transit_days": t} for c, (wh, car, t) in LANES.items()])

    # Procurement
    pos = []
    for p in w.pos:
        received = p.get("received_date")
        if received:
            status, eta = "RECEIVED", received
        else:
            status = "IN_TRANSIT" if (AS_OF - p["order_date"]).days >= 5 else "CONFIRMED"
            eta = p["_eta"]
        pos.append({"po_id": p["po_id"], "supplier_id": p["supplier_id"], "sku": p["sku"],
                    "dest_warehouse": p["dest_warehouse"], "qty": p["qty"], "unit_cost": p["unit_cost"],
                    "po_type": p["po_type"], "order_date": d2s(p["order_date"]),
                    "expected_date": d2s(p["expected_date"]), "supplier_eta": d2s(eta),
                    "received_date": d2s(received), "status": status, "port_of_entry": p["port_of_entry"],
                    "incoterm": p["incoterm"]})
    pd.DataFrame(pos).to_csv(out / "procurement/purchase_orders.csv", index=False)
    # Expedite options agreed in supplier contracts (air freight to Mumbai)
    pd.DataFrame(EXPEDITE_OPTIONS, columns=["supplier_id", "mode", "arrive_warehouse", "lead_days", "category",
                                            "surcharge_inr_per_unit", "min_units"]).to_csv(
        out / "procurement/expedite_options.csv", index=False)
    pd.DataFrame([{"supplier_id": s.supplier_id, "name": s.name, "brand": s.brand, "country": s.country,
                   "port_of_entry": s.port_of_entry, "contract_lead_time_days": s.lead_time_days}
                  for s in SUPPLIERS]).to_csv(out / "procurement/suppliers.csv", index=False)

    # External signals
    write_jsonl(out / "external/port_status.json", [
        {"port": "Chennai", "status": "DISRUPTED", "reason": "Dock workers' strike", "since": "2026-10-09",
         "updated_at": "2026-10-20T18:00:00", "vessels_waiting": 23, "expected_normalisation": "2026-10-28"},
        {"port": "Nhava Sheva", "status": "NORMAL", "reason": None, "since": None,
         "updated_at": "2026-10-20T18:00:00", "vessels_waiting": 4, "expected_normalisation": None},
        {"port": "Mundra", "status": "NORMAL", "reason": None, "since": None,
         "updated_at": "2026-10-20T18:00:00", "vessels_waiting": 6, "expected_normalisation": None},
    ])
    write_jsonl(out / "external/weather_alerts.json", [
        {"alert_id": "IMD-2026-1019-PNQ", "region": "Pune", "level": "ORANGE", "type": "Heavy rainfall",
         "valid_from": "2026-10-19", "valid_to": "2026-10-22", "issued_by": "IMD"},
        {"alert_id": "IMD-2026-1015-MAA", "region": "Chennai", "level": "YELLOW", "type": "Thunderstorms",
         "valid_from": "2026-10-15", "valid_to": "2026-10-16", "issued_by": "IMD"},
    ])

    # Documents
    ctx = docs_context(w)
    rendered = docs_content.render_all(ctx)
    for rel, text in rendered.items():
        (out / "docs" / rel).write_text(text, encoding="utf-8")
    # One queryable table of all documents, so contracts and email go through the same data harness.
    write_jsonl(out / "docs/documents.json", build_doc_index(rendered))

    manifest = {
        "company": "Kaveri Consumer Electronics Distribution Pvt Ltd (fictional)",
        "as_of": AS_OF.isoformat(),
        "sources": {
            "erp": {"system": "ERP (Postgres)", "tables": ["warehouses", "customers", "skus", "sales_orders",
                                                           "order_lines"]},
            "wms": {"system": "Warehouse management export (CSV)", "files": ["inventory_snapshots.csv",
                                                                            "reorder_policy.csv", "transfer_lanes.csv"]},
            "carrier": {"system": "3PL carrier feed (JSON)", "files": ["shipments.json", "carriers.json",
                                                                             "lanes.json"]},
            "procurement": {"system": "Procurement export (CSV)", "files": ["purchase_orders.csv", "suppliers.csv",
                                                                            "expedite_options.csv"]},
            "external": {"system": "External signals (JSON)", "files": ["port_status.json",
                                                                              "weather_alerts.json"]},
            "docs": {"system": "Contracts, policies and email (Markdown + documents.json index)",
                     "files": ["documents.json"]},
        },
        "row_counts": {"sales_orders": len(orders), "order_lines": len(lines), "shipments": len(w.shipments),
                       "purchase_orders": len(pos), "inventory_snapshots": len(snaps)},
        "edi_duplicates_planted": n_dupes,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def docs_context(w: World) -> dict:
    cust = {c["name"]: c for c in w.customers}
    stuck = [p for p in w.pos if p["_strike"] and p["supplier_id"] == "S-14"]
    return {
        "as_of": AS_OF,
        "shree_ganesh": cust["Shree Ganesh Electronics Retail"],
        "edi_customers": [c for c in w.customers if c["channel"] == "EDI"],
        "voltix_stuck": sorted({p["po_id"] for p in stuck if p["po_type"] == "SEASONAL_PREBUILD"}),
        "voltix_stuck_by_wh": {wh: sorted({p["po_id"] for p in stuck if p["dest_warehouse"] == wh})
                               for wh in ("PUN", "CHN", "MUM")},
        "voltix_units_stuck": sum(p["qty"] for p in stuck),
    }


def build_doc_index(rendered: dict[str, str]) -> list[dict]:
    kinds = {"contracts": "contract", "emails": "email", "policies": "policy"}
    rows = []
    for rel, text in sorted(rendered.items()):
        folder, fname = rel.split("/", 1)
        row = {"doc_id": Path(fname).stem, "doc_type": kinds[folder], "path": f"docs/{rel}", "title": None,
               "sent_at": None, "sender": None, "recipients": None, "body": text}
        if folder == "emails":
            head, _, body = text.partition("\n\n")
            fields = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
            row.update(title=fields.get("Subject"), sent_at=fields.get("Date"), sender=fields.get("From"),
                       recipients=", ".join(x for x in [fields.get("To"), fields.get("Cc")] if x), body=body)
        else:
            row["title"] = text.splitlines()[0].lstrip("# ").strip()
        rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    """A JSON array with one record per line: Zetaris reads a JSON file as one document, DuckDB reads either."""
    path.write_text("[\n" + ",\n".join(json.dumps(r, default=str) for r in rows) + "\n]\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    w = World(args.seed)
    w.build_master()
    w.generate_orders()
    w.build_reorder_policy()
    w.simulate()
    n_dupes = w.add_edi_duplicates()
    m = write_all(w, args.out, n_dupes)
    print(json.dumps(m["row_counts"], indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
