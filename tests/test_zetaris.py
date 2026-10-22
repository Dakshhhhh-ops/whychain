"""Offline tests for the Zetaris harness: name mapping, response parsing, and the registration type check.
(The live comparison against a running Zetaris is scripts/register_zetaris.py, which checks every table.)"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from whychain.harness.zetaris import ZetarisHarness, _coerce, parse_response, zetaris_name

ROOT = Path(__file__).resolve().parent.parent


def test_source_names_map_to_registered_names():
    assert zetaris_name("olist", "signals") == "signals"
    assert zetaris_name("kaveri", "signals") == "kaveri_signals"
    z = ZetarisHarness(dataset="kaveri")
    sql = ("WITH late AS (SELECT order_id FROM carrier.shipments WHERE delivered_date > promised_date) "
           "SELECT w.city, COUNT(*) AS n FROM late l JOIN erp.sales_orders so ON so.order_id = l.order_id "
           "JOIN erp.warehouses w ON w.warehouse_id = so.warehouse_id GROUP BY w.city")
    out = z.to_zetaris(sql).lower()
    assert "kaveri_carrier.shipments" in out and "kaveri_erp.sales_orders" in out and "kaveri_erp.warehouses" in out
    assert "from late" in out  # the CTE is not a source and keeps its name
    assert ZetarisHarness(dataset="olist").to_zetaris(sql) == sql  # Olist sources are registered under their own names


def test_parse_response_shapes():
    assert parse_response({"headers": ["a", "b"], "data": [["1", "x"]], "total": 1}) == (["a", "b"], [["1", "x"]])
    assert parse_response({"records": [{"a": 1, "b": "x"}]}) == (["a", "b"], [[1, "x"]])
    assert parse_response({"headers": ["n"], "data": []}) == (["n"], [])


def test_values_come_back_as_numbers_where_they_are_numbers():
    assert _coerce("42") == 42 and _coerce("-3.5") == -3.5 and _coerce("1e3") == 1000.0
    assert _coerce("2018-03-01") == "2018-03-01" and _coerce("S-14") == "S-14" and _coerce(None) is None


def test_registration_keeps_leading_zero_codes_as_text(tmp_path):
    spec = importlib.util.spec_from_file_location("register_zetaris", ROOT / "scripts" / "register_zetaris.py")
    reg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reg)
    zips = tmp_path / "sellers.csv"
    zips.write_text("seller_id,zip,city\nabc,13023,campinas\ndef,01310,sao paulo\n", encoding="utf-8")
    plain = tmp_path / "items.csv"
    plain.write_text("order_id,price,qty\nx,0.50,3\ny,10.0,0\n", encoding="utf-8")
    assert reg.infer_types(zips) is False   # 01310 would become 1310
    assert reg.infer_types(plain) is True   # 0.50 and 0 are ordinary numbers
