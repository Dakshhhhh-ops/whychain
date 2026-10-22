"""Zetaris AI Data Harness client (REST).

Zetaris federates every source (the Postgres ERP plus the CSV/JSON file stores) behind one
Spark-SQL endpoint, so a single query can join erp.sales_orders with carrier.shipments
without copying any data. Sources are registered once with scripts/register_zetaris.py.

REST call (from zetaris/getting-started):  POST {base}/sql-editor/sqls/run
headers: Authorization: Bearer <API key>, X-Org-ID: <org id>, X-Request-ID: <uuid>
"""
from __future__ import annotations

import re
import uuid

import httpx

from whychain.config import SETTINGS
from whychain.datasets import Dataset, get_dataset
from whychain.harness.base import Column, QueryResult, TableInfo, Timer, check_read_only, pin_today

SOURCES = ["erp", "wms", "carrier", "procurement", "signals", "docs"]


class ZetarisError(RuntimeError):
    pass


class ZetarisHarness:
    name = "zetaris"
    dialect = "spark"

    def __init__(self, base_url: str | None = None, api_key: str | None = None, org_id: str | None = None,
                 timeout: float = 120.0, dataset: str | Dataset | None = None):
        self.dataset = dataset if isinstance(dataset, Dataset) else get_dataset(dataset)
        self.as_of = self.dataset.as_of
        self.base = (base_url or SETTINGS.zetaris_url).rstrip("/")
        self.api_key = api_key or SETTINGS.zetaris_api_key
        self.org_id = org_id or SETTINGS.zetaris_org_id
        self.client = httpx.Client(timeout=timeout)
        self._catalog: list[TableInfo] | None = None

    def _headers(self) -> dict:
        h = {"Authorization": f"Bearer {self.api_key}", "X-Request-ID": str(uuid.uuid4()),
             "Content-Type": "application/json"}
        if self.org_id:
            h["X-Org-ID"] = str(self.org_id)
        return h

    def _run(self, sql: str, limit: int) -> tuple[list[str], list[list]]:
        body = {"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor", "limit": limit}
        r = self.client.post(f"{self.base}/sql-editor/sqls/run", headers=self._headers(), json=body)
        if r.status_code >= 400:
            raise ZetarisError(f"HTTP {r.status_code}: {r.text[:400]}")
        return parse_response(r.json())

    def query(self, sql: str, limit: int = 200) -> QueryResult:
        sql = sql.strip().rstrip(";")
        err = check_read_only(sql)
        if err:
            return QueryResult(sql, [], [], 0, False, 0, self.name, error=err)
        with Timer() as t:
            try:
                cols, rows = self._run(pin_today(sql, self.as_of), limit + 1)
                error = None
            except (httpx.HTTPError, ZetarisError, ValueError) as e:
                cols, rows, error = [], [], str(e)[:400]
        truncated = len(rows) > limit
        rows = [[_coerce(v) for v in r] for r in rows[:limit]]
        return QueryResult(sql, cols, rows, len(rows), truncated, t.ms, self.name, sql, error)

    def admin(self, sql: str) -> tuple[list[str], list[list]]:
        """Run a DDL / registration statement (setup only, never exposed to agents)."""
        return self._run(sql, 1000)

    def catalog(self) -> list[TableInfo]:
        if self._catalog is not None:
            return self._catalog
        out: list[TableInfo] = []
        for src in self.dataset.sources:
            tables: list[str] = []
            for stmt in (f"SHOW DATASOURCE TABLES {src}", f"SHOW LIGHTNING TABLES {src}"):
                try:
                    cols, rows = self._run(stmt, 500)
                    tables = [_table_name(cols, r) for r in rows]
                    if tables:
                        break
                except (httpx.HTTPError, ZetarisError, ValueError):
                    continue
            for t in tables:
                columns = []
                try:
                    cols, rows = self._run(f"DESCRIBE {src}.{t}", 500)
                    columns = [Column(str(r[0]), str(r[1]) if len(r) > 1 else "") for r in rows
                               if r and r[0] and not str(r[0]).startswith("#")]
                except (httpx.HTTPError, ZetarisError, ValueError):
                    pass
                out.append(TableInfo(src, t, columns))
        self._catalog = out
        return out

    def status(self) -> dict:
        try:
            self._run("SELECT 1", 1)
            return {"ok": True, "harness": self.name, "url": self.base}
        except Exception as e:
            return {"ok": False, "harness": self.name, "url": self.base, "error": str(e)[:200]}


def parse_response(payload) -> tuple[list[str], list[list]]:
    """Accept the shapes Zetaris has been seen to return: {data: [[...]], columns|header|schema: [...]}."""
    if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
        payload = payload["data"]
    data = payload.get("data", payload.get("rows", [])) if isinstance(payload, dict) else payload
    cols_raw = None
    if isinstance(payload, dict):
        for k in ("columns", "header", "headers", "columnNames", "schema", "fields"):
            if payload.get(k):
                cols_raw = payload[k]
                break
    rows: list[list] = []
    cols: list[str] = []
    if data and isinstance(data[0], dict):
        cols = list(data[0].keys())
        rows = [[d.get(c) for c in cols] for d in data]
    else:
        rows = [list(r) for r in (data or [])]
    if cols_raw:
        cols = [c.get("name") or c.get("columnName") or c.get("label") if isinstance(c, dict) else str(c)
                for c in cols_raw]
    if not cols and rows:
        cols = [f"col{i}" for i in range(len(rows[0]))]
    return cols, rows


def _table_name(cols: list[str], row: list) -> str:
    lowered = [c.lower() for c in cols]
    for key in ("tablename", "table_name", "table", "name"):
        if key in lowered:
            return str(row[lowered.index(key)])
    return str(row[-1] if len(row) > 1 else row[0])


def _coerce(v):
    """Zetaris returns values as strings; turn obvious numbers back into numbers."""
    if not isinstance(v, str) or not _NUMBER.match(v):
        return v
    return int(v) if v.lstrip("-").isdigit() else float(v)


_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
