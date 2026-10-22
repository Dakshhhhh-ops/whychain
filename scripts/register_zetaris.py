"""Register a dataset's sources in Zetaris, so agents can query them all through one endpoint.

  python scripts/register_zetaris.py                     # print the SQL for the default dataset (Olist)
  python scripts/register_zetaris.py --dataset kaveri    # ... or for the simulated company
  python scripts/register_zetaris.py --apply             # run it through the Zetaris REST API

Before running:
  1. Zetaris is up (local Docker install from the hackathon registry), with an API key and org id in .env.
  2. Postgres is reachable from the Zetaris container. With Docker on the same machine that is usually
     jdbc:postgresql://host.docker.internal:5433/erp (set ZETARIS_PG_JDBC_URL to override). Olist's order
     system lives in the "olist" schema of the same database; Kaveri's ERP in "public".
  3. The dataset's files are visible to Zetaris, either uploaded through NDP File System -> file-store, or
     mounted. Set ZETARIS_FILE_PREFIX to the folder Zetaris sees for this dataset, e.g. /data/olist or
     s3a://my-bucket/whychain/olist (it must contain the same sub-folders as data/olist/generated/).

Register one dataset per Zetaris instance at a time: both datasets have sources called "signals" and "docs".

Statement syntax follows zetaris/getting-started (Lightning SQL). Check each one against your instance.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whychain.config import env  # noqa: E402
from whychain.datasets import Dataset, get_dataset  # noqa: E402


def _fmt(path: str) -> str:
    return "CSV" if path.endswith(".csv") else "JSON"


def statements(ds: Dataset, mask: bool) -> list[str]:
    jdbc = env("ZETARIS_PG_JDBC_URL", "jdbc:postgresql://host.docker.internal:5433/erp")
    if ds.pg_schema and ds.pg_schema != "public":
        jdbc += ("&" if "?" in jdbc else "?") + f"currentSchema={ds.pg_schema}"
    user = env("POSTGRES_USER", "whychain")
    pw = "********" if mask else env("POSTGRES_PASSWORD", "whychain-local")
    prefix = env("ZETARIS_FILE_PREFIX", f"/data/{ds.key}").rstrip("/")
    out = []
    for source in ds.pg_tables:
        out += [f'CREATE DATASOURCE {source} DESCRIBE BY "PostgreSQL" OPTIONS (jdbcdriver "org.postgresql.Driver", '
                f'jdbcurl "{jdbc}", username "{user}", password "{pw}")',
                f"REGISTER DATASOURCE TABLES FROM {source}"]
    for db, tables in ds.files.items():
        out.append(f"CREATE LIGHTNING DATABASE {db}")
        for table, rel in tables.items():
            opts = f'path "{prefix}/{rel}"'
            if _fmt(rel) == "CSV":
                opts += ', header "true", inferSchema "true"'
            out.append(f"CREATE LIGHTNING FILESTORE TABLE {table} FROM {db} FORMAT {_fmt(rel)} OPTIONS ({opts})")
    return out


CROSS_CHECK = {
    # one federated query per dataset: the Postgres system joined with a file source
    "olist": ("SELECT c.customer_state, COUNT(DISTINCT o.order_id) AS orders, AVG(r.review_score) AS avg_review "
              "FROM oms.orders o JOIN oms.customers c ON c.customer_id = o.customer_id "
              "JOIN feedback.reviews r ON r.order_id = o.order_id GROUP BY c.customer_state ORDER BY orders DESC"),
    "kaveri": ("SELECT o.warehouse_id, COUNT(DISTINCT s.shipment_id) AS shipments FROM erp.sales_orders o "
               "JOIN carrier.shipments s ON s.order_id = o.order_id GROUP BY o.warehouse_id"),
}


def checks(ds: Dataset) -> list[str]:
    out = [f"SELECT COUNT(*) FROM {src}.{tables[0]}" for src, tables in ds.pg_tables.items()]
    out += [f"SELECT COUNT(*) FROM {src}.{next(iter(tables))}" for src, tables in ds.files.items()]
    if ds.key in CROSS_CHECK:
        out.append(CROSS_CHECK[ds.key])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=None, help="olist (default) or kaveri")
    ap.add_argument("--apply", action="store_true", help="run the statements through the Zetaris REST API")
    args = ap.parse_args()
    ds = get_dataset(args.dataset)
    if not args.apply:
        print(f"-- {ds.name}: paste into the Zetaris SQL editor, one statement at a time --\n")
        for s in statements(ds, mask=True):
            print(s + "\n")
        print("-- Then check:")
        for c in checks(ds):
            print(c)
        return
    from whychain.harness.zetaris import ZetarisError, ZetarisHarness

    z = ZetarisHarness(dataset=ds)
    for s, shown in zip(statements(ds, mask=False), statements(ds, mask=True)):
        try:
            z.admin(s)
            print(f"ok    {shown[:110]}")
        except ZetarisError as e:
            print(f"FAIL  {shown[:110]}\n      {e}")
    print()
    for c in checks(ds):
        r = z.query(c)
        print(f"{'ok  ' if r.ok else 'FAIL'}  {c[:80]} -> {r.rows[:4] if r.ok else r.error}")


if __name__ == "__main__":
    main()
