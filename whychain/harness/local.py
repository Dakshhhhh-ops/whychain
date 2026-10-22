"""Local fallback harness: DuckDB over the same sources, using the same two-part names.

Tables a dataset keeps in Postgres (its system of record) are read live through DuckDB's postgres extension when
Postgres is reachable, otherwise from their CSV export. Agents write Spark SQL, which sqlglot translates to DuckDB.
"""
from __future__ import annotations

import datetime as dt
import decimal
import threading
from pathlib import Path

import duckdb
import sqlglot

from whychain.config import SETTINGS
from whychain.datasets import Dataset, get_dataset
from whychain.harness.base import Column, QueryResult, TableInfo, Timer, check_read_only, pin_today


def _jsonable(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


class LocalHarness:
    name = "local-duckdb"
    dialect = "spark"

    def __init__(self, dataset: str | Dataset | None = None, use_postgres: bool = True, data_dir: Path | None = None):
        self.dataset = dataset if isinstance(dataset, Dataset) else get_dataset(dataset)
        self.data_dir = data_dir or self.dataset.data_dir
        self.as_of = self.dataset.as_of
        self.lock = threading.Lock()
        self.con = duckdb.connect()
        self.erp_mode = "csv"
        self._setup(use_postgres)
        self._catalog: list[TableInfo] | None = None

    def _setup(self, use_postgres: bool) -> None:
        c = self.con
        ds = self.dataset
        for schema in ds.sources:
            c.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        if use_postgres and ds.pg_tables:
            try:
                c.execute("INSTALL postgres; LOAD postgres;")
                c.execute(f"ATTACH '{SETTINGS.pg_dsn}' AS pg (TYPE postgres, READ_ONLY)")
                for source, tables in ds.pg_tables.items():
                    for t in tables:
                        c.execute(f"CREATE OR REPLACE VIEW {source}.{t} AS SELECT * FROM pg.{ds.pg_schema}.{t}")
                source, tables = next(iter(ds.pg_tables.items()))
                c.execute(f"SELECT count(*) FROM {source}.{tables[0]}").fetchone()
                self.erp_mode = "postgres"
            except Exception:
                self.erp_mode = "csv"
        if self.erp_mode == "csv":
            for source, tables in ds.pg_tables.items():
                for t in tables:
                    path = (self.data_dir / source / f"{t}.csv").as_posix()
                    c.execute(f"CREATE OR REPLACE VIEW {source}.{t} AS SELECT * FROM read_csv_auto('{path}')")
        for schema, tables in ds.files.items():
            for t, rel in tables.items():
                p = self.data_dir / rel
                if not p.exists() or p.stat().st_size == 0:
                    continue  # e.g. no events loaded yet: the table simply isn't in the catalog
                reader = "read_json_auto" if rel.endswith(".json") else "read_csv_auto"
                c.execute(f"CREATE OR REPLACE VIEW {schema}.{t} AS SELECT * FROM {reader}('{p.as_posix()}')")

    def translate(self, sql: str) -> str:
        try:
            return sqlglot.transpile(sql, read="spark", write="duckdb")[0]
        except Exception:
            return sql

    def query(self, sql: str, limit: int = 200) -> QueryResult:
        sql = sql.strip().rstrip(";")
        err = check_read_only(sql)
        if err:
            return QueryResult(sql, [], [], 0, False, 0, self.name, error=err)
        executed = self.translate(pin_today(sql, self.as_of))
        with self.lock, Timer() as t:
            try:
                cur = self.con.execute(executed)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchmany(limit + 1)
                error = None
            except Exception as e:  # surfaced to the agent so it can repair the SQL
                cols, rows, error = [], [], str(e).split("\n")[0][:400]
        truncated = len(rows) > limit
        rows = [[_jsonable(v) for v in r] for r in rows[:limit]]
        return QueryResult(sql, cols, rows, len(rows), truncated, t.ms, self.name, executed, error)

    def catalog(self) -> list[TableInfo]:
        if self._catalog is not None:
            return self._catalog
        out = []
        for schema in self.dataset.sources:
            tables = [r[0] for r in self.con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = ? ORDER BY 1",
                [schema]).fetchall()]
            for t in tables:
                cols = self.con.execute(f"DESCRIBE {schema}.{t}").fetchall()
                n = self.con.execute(f"SELECT count(*) FROM {schema}.{t}").fetchone()[0]
                out.append(TableInfo(schema, t, [Column(c[0], c[1]) for c in cols], n))
        self._catalog = out
        return out

    def status(self) -> dict:
        try:
            self.con.execute("SELECT 1").fetchone()
            return {"ok": True, "harness": self.name, "erp": self.erp_mode, "dataset": self.dataset.key}
        except Exception as e:
            return {"ok": False, "harness": self.name, "error": str(e)}
