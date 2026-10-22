"""Load the ERP tables into Postgres (the company's live ERP database).

Usage:  python -m datagen.load_postgres
"""
from __future__ import annotations

import io

import pandas as pd
import psycopg

from whychain.config import SETTINGS

DDL = """
DROP TABLE IF EXISTS order_lines, sales_orders, skus, customers, warehouses;
CREATE TABLE warehouses (warehouse_id text PRIMARY KEY, name text, city text, state text);
CREATE TABLE customers (customer_id text PRIMARY KEY, name text, city text, region text, tier text, channel text,
    default_warehouse text REFERENCES warehouses, credit_limit_inr numeric);
CREATE TABLE skus (sku text PRIMARY KEY, name text, category text, supplier_id text, unit_cost numeric,
    list_price numeric, case_pack int);
CREATE TABLE sales_orders (order_id text PRIMARY KEY, external_ref text, customer_id text REFERENCES customers,
    warehouse_id text REFERENCES warehouses, channel text, order_date date, created_at timestamp,
    promised_date date, requested_ship_date date, status text, promo_code text, order_value_inr numeric);
CREATE TABLE order_lines (order_id text REFERENCES sales_orders, line_no int, sku text REFERENCES skus, qty int,
    unit_price numeric, discount_pct numeric, line_value numeric, allocated_qty int, backordered_qty int,
    PRIMARY KEY (order_id, line_no));
CREATE INDEX ON sales_orders (warehouse_id, status);
CREATE INDEX ON order_lines (sku);
"""
TABLES = ["warehouses", "customers", "skus", "sales_orders", "order_lines"]


def load(dsn: str | None = None) -> dict[str, int]:
    erp_dir = SETTINGS.data_dir / "erp"
    counts = {}
    with psycopg.connect(dsn or SETTINGS.pg_dsn) as conn:
        conn.execute(DDL)
        for t in TABLES:
            df = pd.read_csv(erp_dir / f"{t}.csv")
            buf = io.StringIO()
            df.to_csv(buf, index=False, header=False)
            buf.seek(0)
            with conn.cursor().copy(f"COPY {t} ({', '.join(df.columns)}) FROM STDIN WITH (FORMAT csv)") as cp:
                cp.write(buf.read())
            counts[t] = len(df)
        conn.commit()
    return counts


if __name__ == "__main__":
    for t, n in load().items():
        print(f"{t:14s} {n:>6}")
