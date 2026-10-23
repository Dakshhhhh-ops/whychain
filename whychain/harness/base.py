"""The data harness: the only way agents touch company data.

Agents always write Spark SQL (the Zetaris Lightning dialect) against two-part names such as
`erp.sales_orders` or `carrier.shipments`. The Zetaris harness runs it as-is. The local
harness translates it to DuckDB, so agents behave the same in both modes.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Protocol

READ_ONLY = re.compile(r"^\s*(with|select|show|describe|desc|explain)\b", re.I)

# What each source is, in business terms. Shown to the agents alongside the live catalog.
SOURCE_NOTES = {
    "erp": "ERP database (Postgres): warehouses, customers, skus, sales_orders, order_lines. Live system of record.",
    "wms": "Warehouse management system export (CSV): daily inventory_snapshots per warehouse and SKU, reorder_policy, transfer_lanes (inter-DC truck rates).",
    "carrier": "3PL carrier feed (JSON): shipments with pickup/departure/delivery dates, lanes (standard transit days), carriers.",
    "procurement": "Procurement export (CSV): purchase_orders to suppliers (expected vs supplier_eta vs received), suppliers, expedite_options (air freight terms).",
    "signals": "External signals (JSON): port_status and weather_alerts.",
    "docs": "Documents (JSON): contracts, policies and email in docs.documents (doc_type, title, sent_at, sender, recipients, body).",
}


@dataclass
class Column:
    name: str
    type: str


@dataclass
class TableInfo:
    source: str
    table: str
    columns: list[Column]
    row_count: int | None = None

    @property
    def fqn(self) -> str:
        return f"{self.source}.{self.table}"


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool
    elapsed_ms: int
    harness: str
    executed_sql: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def preview(self, n: int = 15) -> str:
        """Compact text table for prompts and logs."""
        if self.error:
            return f"ERROR: {self.error}"
        if not self.rows:
            return "(0 rows)"
        lines = [" | ".join(self.columns)]
        for r in self.rows[:n]:
            lines.append(" | ".join("" if v is None else str(v) for v in r))
        more = self.row_count - min(n, len(self.rows))
        if more > 0 or self.truncated:
            lines.append(f"... ({self.row_count}{'+' if self.truncated else ''} rows total)")
        return "\n".join(lines)

    def to_dict(self, max_rows: int = 50) -> dict:
        return {"sql": self.sql, "columns": self.columns, "rows": self.rows[:max_rows], "row_count": self.row_count,
                "truncated": self.truncated, "elapsed_ms": self.elapsed_ms, "harness": self.harness,
                "error": self.error}


class DataHarness(Protocol):
    name: str
    dialect: str

    def catalog(self) -> list[TableInfo]: ...

    def query(self, sql: str, limit: int = 200) -> QueryResult: ...

    def status(self) -> dict: ...


TODAY_FUNCS = re.compile(r"\bcurrent_date\s*(\(\s*\))?|\bcurdate\s*\(\s*\)|\bnow\s*\(\s*\)|"
                         r"\bcurrent_timestamp\s*(\(\s*\))?", re.I)


def pin_today(sql: str, as_of: str | None = None) -> str:
    """The data is a snapshot, so "today" is its as-of date, not the wall clock."""
    if as_of is None:
        from whychain.config import AS_OF as as_of
    return TODAY_FUNCS.sub(f"DATE'{as_of}'", sql)


def check_read_only(sql: str) -> str | None:
    stripped = sql.strip().rstrip(";").strip()
    if ";" in stripped:
        return "Only one statement per query is allowed."
    if not READ_ONLY.match(stripped):
        return "Agents may only run read-only queries (SELECT / WITH / SHOW / DESCRIBE)."
    return None


def catalog_text(tables: list[TableInfo], notes: dict[str, str] | None = None) -> str:
    """Catalog as compact text for prompts."""
    notes = SOURCE_NOTES if notes is None else notes
    out = []
    by_source: dict[str, list[TableInfo]] = {}
    for t in tables:
        by_source.setdefault(t.source, []).append(t)
    for src, ts in by_source.items():
        out.append(f"## {src}: {notes.get(src, '')}")
        for t in ts:
            cols = ", ".join(f"{c.name} {c.type}" for c in t.columns)
            rc = f" [{t.row_count} rows]" if t.row_count is not None else ""
            out.append(f"- {t.fqn}{rc}: {cols}")
    return "\n".join(out)


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = int((time.perf_counter() - self.t0) * 1000)
