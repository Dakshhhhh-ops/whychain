"""Register WhyChain's datasets in Zetaris, then check every table against the local harness.

  python scripts/register_zetaris.py                    # both datasets, against the local Zetaris platform
  python scripts/register_zetaris.py --dataset olist
  python scripts/register_zetaris.py --print            # just print the statements (passwords masked)

Before running:
  1. The Zetaris platform is up (the Docker bundle: `docker compose up -d`, then `./verify.sh`), and WhyChain's .env
     has ZETARIS_URL, ZETARIS_USER and ZETARIS_PASSWORD.
  2. WhyChain's Postgres (docker-compose.yml here) is up and loaded (python -m datagen.olist / datagen.load_postgres).
  3. WhyChain's data folders are mounted into the Zetaris containers under ZETARIS_DATA_ROOT
     (default /home/zetaris/customer-data/shared/whychain): the bundle only reads local files under
     /home/zetaris/customer-data/shared. See docs/zetaris-override.yml for the compose override.

Both datasets go into one Zetaris instance; the simulated company's sources get a "kaveri_" prefix (the harness maps
the names back), because both datasets have sources called signals and docs.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whychain.config import env  # noqa: E402
from whychain.datasets import DATASETS, Dataset  # noqa: E402
from whychain.harness.zetaris import ZetarisError, ZetarisHarness, zetaris_name  # noqa: E402

DATA_ROOT = env("ZETARIS_DATA_ROOT", "/home/zetaris/customer-data/shared/whychain")
_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def infer_types(path: Path, sample: int = 5000) -> bool:
    """Let Spark infer column types (numbers, dates) unless a column holds codes with leading zeros, such as zip
    prefixes like 01310, which inference would turn into the number 1310. Such tables stay all-text."""
    with path.open(encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            if i >= sample:
                break
            if any(v and len(v) > 1 and v[0] == "0" and v[1] != "." and _NUM.match(v) for v in row.values()):
                return False
    return True


def statements(ds: Dataset, mask: bool) -> list[str]:
    host = env("ZETARIS_PG_HOST", "host.docker.internal")
    port = env("POSTGRES_PORT", "5433")
    db = env("POSTGRES_DB", "erp")
    user = env("POSTGRES_USER", "whychain")
    pw = "********" if mask else env("POSTGRES_PASSWORD", "whychain-local")
    out = []
    for source in ds.pg_tables:
        z = zetaris_name(ds.key, source)
        out += [f'CREATE DATASOURCE {z} DESCRIBE BY "whychain_{z}" OPTIONS (jdbcdriver "org.postgresql.Driver", '
                f'jdbcurl "jdbc:postgresql://{host}:{port}/{db}", username "{user}", password "{pw}", '
                f'schema "{ds.pg_schema}")',
                f"REGISTER DATASOURCE TABLES FROM {z}"]
    for source, tables in ds.files.items():
        if source in ds.pg_tables:
            continue
        z = zetaris_name(ds.key, source)
        out.append(f'CREATE LIGHTNING DATABASE {z} DESCRIBE BY "whychain_{z}" OPTIONS ()')
        for table, rel in tables.items():
            # JSON files are arrays (Zetaris reads a JSON file as one document); JSON dates stay text, which Spark
            # compares with date literals correctly.
            fmt = "csv" if rel.endswith(".csv") else "json"
            opts = f'path "{DATA_ROOT}/{ds.key}/{rel}"'
            if fmt == "csv":
                opts += ', header "true"' + (', inferSchema "true"' if infer_types(ds.data_dir / rel) else "")
            out.append(f"CREATE LIGHTNING FILESTORE TABLE {table} FROM {z} FORMAT {fmt} OPTIONS ({opts})")
    return out


def drop(ds: Dataset) -> None:
    """Remove the dataset's sources (Zetaris won't replace a table in place, so re-registering starts clean)."""
    z = ZetarisHarness(dataset=ds)
    for source in ds.sources:
        kind = "DATASOURCE" if source in ds.pg_tables else "LIGHTNING DATABASE"
        try:
            z.admin(f"DROP {kind} {zetaris_name(ds.key, source)}")
            print(f"  dropped {kind.lower()} {zetaris_name(ds.key, source)}")
        except ZetarisError as e:
            print(f"  (not dropped: {zetaris_name(ds.key, source)}: {str(e)[:100]})")


def register(ds: Dataset) -> int:
    z = ZetarisHarness(dataset=ds)
    failures = 0
    for s, shown in zip(statements(ds, mask=False), statements(ds, mask=True)):
        try:
            z.admin(s)
            print(f"  ok      {shown[:120]}")
        except ZetarisError as e:
            if "exist" in str(e).lower():
                print(f"  exists  {shown[:120]}")
            else:
                failures += 1
                print(f"  FAIL    {shown[:120]}\n          {e}")
    return failures


def check(ds: Dataset) -> int:
    """Row counts through Zetaris vs the local harness, table by table."""
    from whychain.harness.local import LocalHarness

    z, loc = ZetarisHarness(dataset=ds), LocalHarness(ds.key)
    bad = 0
    tables = [(s, t) for s, ts in ds.pg_tables.items() for t in ts] + \
             [(s, t) for s, ts in ds.files.items() if s not in ds.pg_tables for t in ts]
    for source, table in tables:
        sql = f"SELECT COUNT(*) AS n FROM {source}.{table}"
        rz, rl = z.query(sql, 1), loc.query(sql, 1)
        nz = rz.rows[0][0] if rz.ok and rz.rows else None
        nl = rl.rows[0][0] if rl.ok and rl.rows else None
        ok = nz is not None and nz == nl
        bad += not ok
        print(f"  {'ok  ' if ok else 'DIFF'}  {source}.{table:<22} zetaris={nz}  local={nl}"
              + ("" if rz.ok else f"  ({rz.error[:120]})"))
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", choices=sorted(DATASETS), default=None, help="default: both")
    ap.add_argument("--print", action="store_true", help="print the statements instead of running them")
    ap.add_argument("--fresh", action="store_true", help="drop the dataset's sources in Zetaris first")
    args = ap.parse_args()
    datasets = [DATASETS[args.dataset]] if args.dataset else [d for d in DATASETS.values() if d.available]
    fails = 0
    for ds in datasets:
        print(f"\n== {ds.name}")
        if args.print:
            for s in statements(ds, mask=True):
                print(s + ";")
            continue
        if args.fresh:
            drop(ds)
        fails += register(ds)
        print("  -- check: row counts through Zetaris vs the local harness")
        fails += check(ds)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
