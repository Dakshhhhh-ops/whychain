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


def _jwt(lifetime: int) -> str:
    import base64
    import json

    def part(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{part({'alg': 'none'})}.{part({'iat': 1_000_000, 'exp': 1_000_000 + lifetime})}.sig"


def test_a_login_token_is_renewed_before_it_expires_not_after_30_minutes():
    from whychain.harness.zetaris import TOKEN_TTL_S, is_auth_failure, token_expiry

    assert token_expiry(_jwt(900), 5_000.0) == 5_900.0      # the platform's 15-minute tokens, on this machine's clock
    assert token_expiry("not-a-jwt", 5_000.0) == 5_000.0 + TOKEN_TTL_S
    # the local platform answers an expired or invalid token with HTTP 400 and a bare message; SQL errors say more
    assert is_auth_failure(400, "Bad Request") and is_auth_failure(400, "Not Allowed") and is_auth_failure(401, "")
    assert not is_auth_failure(400, "[TABLE_OR_VIEW_NOT_FOUND] The table or view `x`.`y` cannot be found.")
    assert not is_auth_failure(500, "Bad Request")


def test_an_expired_token_is_replaced_and_the_query_retried(monkeypatch):
    import httpx

    from whychain.harness import zetaris as zmod

    monkeypatch.setenv("ZETARIS_USER", "tester")
    monkeypatch.setenv("ZETARIS_PASSWORD", "pw")
    monkeypatch.setenv("ZETARIS_URL", "http://zetaris.test")
    monkeypatch.setattr(zmod, "_TOKENS", {})
    logins, tokens = [], iter([_jwt(900) + "1", _jwt(900) + "2"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            logins.append(1)
            return httpx.Response(200, json={"idToken": next(tokens), "orgId": 7})
        if request.headers["Authorization"].endswith("1"):          # the first token has been invalidated
            return httpx.Response(400, json={"msg": "Bad Request"})
        return httpx.Response(200, json={"headers": ["ok"], "data": [["1"]]})

    z = zmod.ZetarisHarness(dataset="olist")
    assert z.mode == "local"
    z.client = httpx.Client(transport=httpx.MockTransport(handler))
    res = z.query("SELECT 1 AS ok")
    assert res.ok and res.rows == [[1]] and len(logins) == 2
