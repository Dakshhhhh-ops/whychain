"""Load the real Olist Brazilian e-commerce dataset into WhyChain's separate source systems.

Dataset: "Brazilian E-Commerce Public Dataset by Olist" (Kaggle: olistbr/brazilian-ecommerce), real anonymised
marketplace data, about 100k orders from Sept 2016 to Oct 2018. Licence: CC BY-NC-SA 4.0 (attribution to Olist,
non-commercial use).

  python -m datagen.olist                 # download the CSVs (GitHub mirror of the Kaggle release) and build
  python -m datagen.olist --raw DIR       # build from CSVs you downloaded yourself from Kaggle

The data is split the way a marketplace's systems hold it, and served through the same data harness:
  oms        (Postgres)  orders, customers                 order management system
  sellers    (CSV)       order_items, sellers, products    seller platform / catalog
  payments   (JSON)      order_payments                    payment gateway
  feedback   (JSON)      reviews (real customer text)      review platform
  signals    (JSON)      events                            verified external events (strikes, peak seasons)
  docs       (JSON)      documents                         news briefs on those events, with sources
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "olist" / "raw"
OUT = ROOT / "data" / "olist" / "generated"
EVENTS = Path(__file__).resolve().parent / "olist_events.json"
MIRROR = "https://raw.githubusercontent.com/spdrio/Brazilian-E-Commerce-Public-Dataset-by-Olist/master/files/"
FILES = ["olist_customers_dataset.csv", "olist_order_items_dataset.csv", "olist_order_payments_dataset.csv",
         "olist_order_reviews_dataset.csv", "olist_orders_dataset.csv", "olist_products_dataset.csv",
         "olist_sellers_dataset.csv", "product_category_name_translation.csv"]
AS_OF = "2018-09-01"  # the data has substantial volume through August 2018
TS_COLS = ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
           "order_delivered_customer_date", "order_estimated_delivery_date"]


def download(raw: Path) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        target = raw / f
        if target.exists() and target.stat().st_size > 0:
            continue
        print(f"downloading {f} ...")
        with httpx.stream("GET", MIRROR + f, timeout=120, follow_redirects=True) as r:
            r.raise_for_status()
            with target.open("wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)


def write_jsonl(path: Path, df: pd.DataFrame) -> None:
    """A JSON array with one record per line: Zetaris reads a JSON file as one document, DuckDB reads either."""
    df = df.astype(object).where(pd.notna(df), None)
    recs = [json.dumps(rec, ensure_ascii=False, default=str) for rec in df.to_dict(orient="records")]
    path.write_text("[\n" + ",\n".join(recs) + "\n]\n", encoding="utf-8")


def build(raw: Path, out: Path) -> dict:
    if out.exists():
        shutil.rmtree(out)
    for sub in ["oms", "sellers", "payments", "feedback", "signals", "docs"]:
        (out / sub).mkdir(parents=True, exist_ok=True)
    read = lambda name: pd.read_csv(raw / name, dtype=str, keep_default_na=False, na_values=[""])  # noqa: E731

    orders = read("olist_orders_dataset.csv")
    orders.to_csv(out / "oms/orders.csv", index=False)
    customers = read("olist_customers_dataset.csv")
    customers.to_csv(out / "oms/customers.csv", index=False)

    items = read("olist_order_items_dataset.csv")
    items.to_csv(out / "sellers/order_items.csv", index=False)
    read("olist_sellers_dataset.csv").to_csv(out / "sellers/sellers.csv", index=False)
    products = read("olist_products_dataset.csv").merge(read("product_category_name_translation.csv"),
                                                        on="product_category_name", how="left")
    products.to_csv(out / "sellers/products.csv", index=False)

    payments = read("olist_order_payments_dataset.csv")
    for c in ("payment_sequential", "payment_installments"):
        payments[c] = pd.to_numeric(payments[c])
    payments["payment_value"] = pd.to_numeric(payments["payment_value"])
    write_jsonl(out / "payments/order_payments.json", payments)

    reviews = read("olist_order_reviews_dataset.csv")
    reviews["review_score"] = pd.to_numeric(reviews["review_score"])
    write_jsonl(out / "feedback/reviews.json", reviews)

    events = json.loads(EVENTS.read_text(encoding="utf-8")) if EVENTS.exists() else {"events": [], "briefs": []}
    write_jsonl(out / "signals/events.json", pd.DataFrame(
        [{k: v for k, v in e.items() if k != "source_urls"} | {"source_urls": ", ".join(e.get("source_urls", []))}
         for e in events["events"]]) if events["events"] else pd.DataFrame(
        columns=["event_id", "name", "type", "start_date", "end_date", "region", "affects", "summary", "source_urls"]))
    docs = []
    for b in events.get("briefs", []):
        body = b["body"].strip() + "\n\nSources: " + ", ".join(b.get("source_urls", []))
        (out / "docs" / f"{b['doc_id']}.md").write_text(f"# {b['title']}\n\n{body}\n", encoding="utf-8")
        docs.append({"doc_id": b["doc_id"], "doc_type": "news_brief", "path": f"docs/{b['doc_id']}.md",
                     "title": b["title"], "sent_at": b.get("date"), "sender": "Compiled from public news (see Sources)",
                     "recipients": "operations", "body": body})
    write_jsonl(out / "docs/documents.json", pd.DataFrame(docs) if docs else pd.DataFrame(
        columns=["doc_id", "doc_type", "path", "title", "sent_at", "sender", "recipients", "body"]))

    manifest = {
        "company": "Olist Store (real Brazilian marketplace data, anonymised)",
        "as_of": AS_OF, "real": True,
        "source": "Brazilian E-Commerce Public Dataset by Olist (Kaggle: olistbr/brazilian-ecommerce), CC BY-NC-SA 4.0",
        "row_counts": {"orders": len(orders), "customers": len(customers), "order_items": len(items),
                       "order_payments": len(payments), "reviews": len(reviews), "events": len(events["events"]),
                       "documents": len(docs)},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


DDL = """
CREATE SCHEMA IF NOT EXISTS olist;
DROP TABLE IF EXISTS olist.orders, olist.customers;
CREATE TABLE olist.customers (customer_id text PRIMARY KEY, customer_unique_id text, customer_zip_code_prefix text,
    customer_city text, customer_state text);
CREATE TABLE olist.orders (order_id text PRIMARY KEY, customer_id text REFERENCES olist.customers, order_status text,
    order_purchase_timestamp timestamp, order_approved_at timestamp, order_delivered_carrier_date timestamp,
    order_delivered_customer_date timestamp, order_estimated_delivery_date timestamp);
CREATE INDEX ON olist.orders (order_purchase_timestamp);
CREATE INDEX ON olist.orders (customer_id);
"""


def load_postgres(out: Path) -> dict[str, int]:
    import psycopg

    from whychain.config import SETTINGS

    counts = {}
    with psycopg.connect(SETTINGS.pg_dsn) as conn:
        conn.execute(DDL)
        for t in ("customers", "orders"):
            df = pd.read_csv(out / f"oms/{t}.csv", dtype=str, keep_default_na=False)
            buf = io.StringIO()
            df.to_csv(buf, index=False, header=False)
            buf.seek(0)
            with conn.cursor().copy(f"COPY olist.{t} ({', '.join(df.columns)}) FROM STDIN WITH (FORMAT csv, NULL '')") as cp:
                cp.write(buf.read())
            counts[t] = len(df)
        conn.commit()
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=RAW, help="folder with the original Olist CSVs")
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--no-postgres", action="store_true")
    args = ap.parse_args()
    if not args.no_download:
        download(args.raw)
    m = build(args.raw, OUT)
    print(json.dumps(m["row_counts"], indent=2))
    if not args.no_postgres:
        print("postgres:", load_postgres(OUT))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
