"""Datasets (workspaces) WhyChain can investigate.

  olist   REAL: the Olist Brazilian e-commerce marketplace (about 100k orders, 2016-2018) with real customer reviews,
          plus verified external events. The main demo.
  kaveri  SIMULATED: a distributor with warehouses, stock, purchase orders, contracts and email. It exercises the full
          prescriptive loop (stock transfers proven optimal by the Optiagent optimizer), which real public data
          can't, because no public dataset includes a company's inventory, POs and contracts together.

Agents don't know which dataset they're in: they read the catalog and its notes and write their own SQL.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Dataset:
    key: str
    name: str
    real: bool
    description: str
    data_dir: Path
    pg_schema: str                              # Postgres schema holding the live "system of record" tables
    pg_tables: dict[str, list[str]]             # source -> tables served live from Postgres
    files: dict[str, dict[str, str]]            # source -> {table: path relative to data_dir}
    source_notes: dict[str, str]
    samples: list[dict] = field(default_factory=list)
    optimizer: bool = False                     # has warehouses/stock/lanes for the rebalancing optimizer
    default_as_of: str = ""
    currency: str = "INR"
    approval_threshold: float = 300_000         # actions costing more need a manager's approval

    @property
    def manifest(self) -> dict:
        try:
            return json.loads((self.data_dir / "manifest.json").read_text(encoding="utf-8"))
        except Exception:
            return {}

    @property
    def as_of(self) -> str:
        return self.manifest.get("as_of") or self.default_as_of

    @property
    def available(self) -> bool:
        return (self.data_dir / "manifest.json").exists()

    @property
    def sources(self) -> list[str]:
        return list(self.pg_tables) + [s for s in self.files if s not in self.pg_tables]

    def public(self) -> dict:
        m = self.manifest
        return {"key": self.key, "name": self.name, "real": self.real, "description": self.description,
                "available": self.available, "as_of": self.as_of, "company": m.get("company"),
                "row_counts": m.get("row_counts", {}), "optimizer": self.optimizer, "samples": self.samples,
                "currency": self.currency, "approval_threshold": self.approval_threshold,
                "source": m.get("source")}


OLIST = Dataset(
    key="olist",
    name="Olist marketplace (real data)",
    real=True,
    description="Real Brazilian e-commerce marketplace: ~100k orders 2016-2018, sellers, payments and real customer reviews.",
    data_dir=ROOT / "data" / "olist" / "generated",
    pg_schema="olist",
    pg_tables={"oms": ["orders", "customers"]},
    files={
        "sellers": {"order_items": "sellers/order_items.csv", "sellers": "sellers/sellers.csv",
                    "products": "sellers/products.csv"},
        "payments": {"order_payments": "payments/order_payments.json"},
        "feedback": {"reviews": "feedback/reviews.json"},
        "signals": {"events": "signals/events.json"},
        "docs": {"documents": "docs/documents.json"},
    },
    source_notes={
        "oms": ("Order management system (Postgres): orders (status, purchase/approval timestamps, handed to carrier "
                "= order_delivered_carrier_date, delivered = order_delivered_customer_date, promised = "
                "order_estimated_delivery_date) and customers (city, state). LATE (use this definition everywhere) = delivered on a later calendar date than promised: CAST(order_delivered_customer_date AS DATE) > CAST(order_estimated_delivery_date AS DATE). The promised date has no time of day, so comparing raw timestamps would wrongly count same-day deliveries as late."),
        "sellers": ("Seller platform (CSV): order_items (one row per item: seller, product, shipping_limit_date = deadline "
                    "for the seller to hand the item to the carrier, price, freight_value), sellers (city, state), "
                    "products (category in Portuguese and English, weight, dimensions)."),
        "payments": "Payment gateway (JSON): order_payments (type, installments, value).",
        "feedback": ("Review platform (JSON): reviews with review_score 1-5 and the customer's own words in Portuguese "
                     "(review_comment_title, review_comment_message); e.g. 'atraso' = delay, 'greve' = strike, "
                     "'não recebi' = didn't receive, 'correios' = the postal service."),
        "signals": "External events (JSON): verified strikes, peak seasons and other disruptions with dates, region and sources.",
        "docs": "Documents (JSON): news briefs on those external events in docs.documents (doc_type, title, sent_at, body with sources).",
    },
    samples=[
        {"title": "March 2018 delay spike", "tag": "Transport",
         "question": "Why did late deliveries jump to 19% for orders placed in March 2018?"},
        {"title": "Black Friday 2017", "tag": "Peak season",
         "question": "Why did deliveries run late around Black Friday 2017?"},
        {"title": "Late in the Northeast", "tag": "Regional",
         "question": "Why are deliveries to Northeast states like Maranhão and Ceará late so often?"},
        {"title": "Review scores fell", "tag": "Customer",
         "question": "Why did customer review scores fall in early 2018?"},
        {"title": "Truckers' strike", "tag": "External",
         "question": "Did the May 2018 truckers' strike delay deliveries, and by how much?"},
    ],
    optimizer=False,
    default_as_of="2018-09-01",
    currency="BRL",
    approval_threshold=50_000,
)

KAVERI = Dataset(
    key="kaveri",
    name="Kaveri distribution (simulated)",
    real=False,
    description="Simulated electronics distributor with warehouses, stock, purchase orders, contracts and email.",
    data_dir=ROOT / "data" / "generated",
    pg_schema="public",
    pg_tables={"erp": ["warehouses", "customers", "skus", "sales_orders", "order_lines"]},
    files={
        "wms": {"inventory_snapshots": "wms/inventory_snapshots.csv", "reorder_policy": "wms/reorder_policy.csv",
                "transfer_lanes": "wms/transfer_lanes.csv"},
        "carrier": {"shipments": "carrier/shipments.json", "lanes": "carrier/lanes.json",
                    "carriers": "carrier/carriers.json"},
        "procurement": {"purchase_orders": "procurement/purchase_orders.csv", "suppliers": "procurement/suppliers.csv",
                        "expedite_options": "procurement/expedite_options.csv"},
        "signals": {"port_status": "external/port_status.json", "weather_alerts": "external/weather_alerts.json"},
        "docs": {"documents": "docs/documents.json"},
    },
    source_notes={
        "erp": "ERP database (Postgres): warehouses, customers, skus, sales_orders, order_lines. Live system of record.",
        "wms": "Warehouse management system export (CSV): daily inventory_snapshots per warehouse and SKU, reorder_policy, transfer_lanes (inter-DC truck rates).",
        "carrier": "3PL carrier feed (JSON): shipments with pickup/departure/delivery dates, lanes (standard transit days), carriers.",
        "procurement": "Procurement export (CSV): purchase_orders to suppliers (expected vs supplier_eta vs received), suppliers, expedite_options (air freight terms).",
        "signals": "External signals (JSON): port_status and weather_alerts.",
        "docs": "Documents (JSON): contracts, policies and email in docs.documents (doc_type, title, sent_at, sender, recipients, body).",
    },
    samples=[
        {"title": "Pune orders at risk", "tag": "Inbound supply",
         "question": "Why are so many Pune orders at risk of missing their promised date next week?"},
        {"title": "Late Nashik deliveries", "tag": "Transport",
         "question": "Why have deliveries to Nashik been late since September?"},
        {"title": "Mumbai earbud stock-out", "tag": "Demand",
         "question": "Why did Mumbai run out of earbuds in mid-October?"},
        {"title": "Chennai backlog jump", "tag": "Data quality",
         "question": "Why has the Chennai DC's open-order backlog jumped this week?"},
        {"title": "On-time rate drop", "tag": "Multi-cause",
         "question": "Why did our overall on-time delivery rate drop in September and October compared with August?"},
    ],
    optimizer=True,
    default_as_of="2026-10-21",
)

DATASETS: dict[str, Dataset] = {OLIST.key: OLIST, KAVERI.key: KAVERI}


def default_key() -> str:
    key = os.getenv("WHYCHAIN_DATASET", "").strip().lower()
    if key in DATASETS:
        return key
    return OLIST.key if OLIST.available else KAVERI.key


def get_dataset(key: str | None = None) -> Dataset:
    return DATASETS.get((key or "").strip().lower()) or DATASETS[default_key()]
