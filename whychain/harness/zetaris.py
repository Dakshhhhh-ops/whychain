"""Zetaris data harness: every source (Postgres plus file stores) behind one Spark-SQL endpoint, so a single query
can join the order system with seller exports, reviews and events without copying data.

Two ways to connect:
  local    the Zetaris platform running in Docker (the hackathon bundle). Log in with the admin account
           (ZETARIS_USER / ZETARIS_PASSWORD) at ZETARIS_URL (default http://localhost:3000), then
           POST /api/proxy/sql-editor/sqls/run-query {"sql", "limit"} -> {"headers": [...], "data": [[...]]}
  cloud    a Zetaris Cloud endpoint with an API key: POST {ZETARIS_REST_URL}/sql-editor/sqls/run

Sources are registered once with scripts/register_zetaris.py. Both datasets live in one Zetaris instance, so the
simulated company's sources are registered with a prefix (kaveri_erp, kaveri_signals, ...) and this harness maps the
names agents use (signals.port_status) to the registered ones (kaveri_signals.port_status).
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import sqlglot
from sqlglot import exp

from whychain.config import SETTINGS, env, is_placeholder
from whychain.datasets import Dataset, get_dataset
from whychain.harness.base import Column, QueryResult, TableInfo, Timer, check_read_only, pin_today

PREFIX = {"olist": "", "kaveri": "kaveri_"}   # Zetaris database name = prefix + source name
CATALOG_TTL_S = 600
TOKEN_TTL_S = 600        # only when a token's own lifetime can't be read (the local platform issues 15-minute tokens)
TOKEN_MARGIN_S = 120     # log in again this long before a token expires
# The local platform answers a request with an expired or invalid token with HTTP 400 and a bare message, not 401.
AUTH_FAILURES = {"bad request", "not allowed", "unauthorized", "forbidden"}
_TOKENS: dict[tuple[str, str], tuple[str, str, float, float]] = {}   # (url, user) -> (token, org, issued, expires)
_TOKEN_LOCK = threading.Lock()
_CATALOGS: dict[tuple[str, str], tuple[float, list[TableInfo]]] = {}


class ZetarisError(RuntimeError):
    pass


def local_login() -> bool:
    """The local platform is used when an admin login is configured (it takes precedence over a cloud API key)."""
    return not is_placeholder(env("ZETARIS_USER"))


def configured() -> bool:
    return local_login() or not is_placeholder(SETTINGS.zetaris_api_key)


def zetaris_name(dataset_key: str, source: str) -> str:
    return f"{PREFIX.get(dataset_key, dataset_key + '_')}{source}"


def token_expiry(token: str, issued: float) -> float:
    """When a login token expires, on this machine's clock: the token's own lifetime (JWT exp - iat) after it was
    issued, else TOKEN_TTL_S."""
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        lifetime = float(claims["exp"]) - float(claims.get("iat", claims["exp"] - TOKEN_TTL_S))
        return issued + max(60.0, lifetime)
    except (IndexError, KeyError, TypeError, ValueError):
        return issued + TOKEN_TTL_S


def is_auth_failure(status: int, msg: str) -> bool:
    return status in (401, 403) or (status == 400 and str(msg).strip().lower() in AUTH_FAILURES)


def _msg(r: httpx.Response) -> str:
    try:
        return (r.json() or {}).get("msg") or r.text
    except (ValueError, AttributeError):
        return r.text


class ZetarisHarness:
    name = "zetaris"
    dialect = "spark"

    def __init__(self, dataset: str | Dataset | None = None, timeout: float = 600.0):
        self.dataset = dataset if isinstance(dataset, Dataset) else get_dataset(dataset)
        self.as_of = self.dataset.as_of
        self.mode = "local" if local_login() or is_placeholder(SETTINGS.zetaris_api_key) else "cloud"
        self.base = (env("ZETARIS_URL", "http://localhost:3000") if self.mode == "local"
                     else SETTINGS.zetaris_url).rstrip("/")
        self.client = httpx.Client(timeout=timeout)
        self._token: str | None = None
        self._org: str | None = SETTINGS.zetaris_org_id or None
        self._token_exp = 0.0
        self._lock = threading.Lock()
        self._catalog: list[TableInfo] | None = None
        self._map = {s: zetaris_name(self.dataset.key, s) for s in self.dataset.sources}

    # ------------------------------------------------------------------ transport
    def _login(self, fresh: bool = False) -> None:
        """Log in to the local platform, sharing the token across harness instances (every investigation makes a new
        harness) until shortly before it expires. A failed login (5xx or a dropped connection) is retried twice with
        a short back-off."""
        key = (self.base, env("ZETARIS_USER"))
        with _TOKEN_LOCK:
            hit = None if fresh else _TOKENS.get(key)
            if hit and time.time() < hit[3] - TOKEN_MARGIN_S:
                self._token, self._org, _, self._token_exp = hit
                return
            last = ""
            for attempt in range(3):
                try:
                    r = self.client.post(f"{self.base}/api/auth/login", timeout=60,
                                         json={"username": env("ZETARIS_USER"), "password": env("ZETARIS_PASSWORD")})
                    if r.status_code < 400:
                        body, now = r.json(), time.time()
                        self._token, self._org = body["idToken"], str(body["orgId"])
                        self._token_exp = token_expiry(self._token, now)
                        _TOKENS[key] = (self._token, self._org, now, self._token_exp)
                        return
                    last = f"HTTP {r.status_code}"
                    if r.status_code < 500:
                        break   # wrong credentials: retrying won't help
                except httpx.HTTPError as e:
                    last = str(e)[:160]
                time.sleep(1 + 2 * attempt)
            raise ZetarisError(f"login failed: {last}")

    def _headers(self) -> dict:
        h = {"X-Request-ID": str(uuid.uuid4()), "Content-Type": "application/json"}
        if self.mode == "local":
            with self._lock:
                if not self._token or time.time() > self._token_exp - TOKEN_MARGIN_S:
                    self._login()
            h["Authorization"] = f"Bearer {self._token}"
        else:
            h["Authorization"] = f"Bearer {SETTINGS.zetaris_api_key}"
        if self._org:
            h["X-Org-ID"] = str(self._org)
        return h

    def _post(self, sql: str, limit: int) -> dict:
        if self.mode == "local":
            url, body = f"{self.base}/api/proxy/sql-editor/sqls/run-query", {"sql": sql, "limit": limit}
        else:
            url = f"{self.base}/sql-editor/sqls/run"
            body = {"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor", "limit": limit}
        r = self.client.post(url, headers=self._headers(), json=body)
        if self.mode == "local" and is_auth_failure(r.status_code, _msg(r)):
            # the token expired or was invalidated (e.g. Zetaris restarted): log in again once
            self._token = None
            with self._lock:
                self._login(fresh=True)
            r = self.client.post(url, headers=self._headers(), json=body)
        if r.status_code >= 400:
            raise ZetarisError(f"HTTP {r.status_code}: {str(_msg(r))[:400]}")
        return r.json()

    def _run(self, sql: str, limit: int) -> tuple[list[str], list[list]]:
        return parse_response(self._post(sql, limit))

    # ------------------------------------------------------------------ the harness interface
    def to_zetaris(self, sql: str) -> str:
        """Rename the dataset's source names to the names registered in Zetaris (no-op for Olist)."""
        if all(k == v for k, v in self._map.items()):
            return sql
        tree = sqlglot.parse_one(sql, read="spark")
        for t in tree.find_all(exp.Table):
            db = t.args.get("db")
            if db is not None and db.name.lower() in self._map:
                t.set("db", exp.to_identifier(self._map[db.name.lower()]))
        return tree.sql(dialect="spark")

    def query(self, sql: str, limit: int = 200) -> QueryResult:
        sql = sql.strip().rstrip(";")
        err = check_read_only(sql)
        if err:
            return QueryResult(sql, [], [], 0, False, 0, self.name, error=err)
        with Timer() as t:
            try:
                executed = self.to_zetaris(pin_today(sql, self.as_of))
                cols, rows = self._run(executed, limit + 1)
                error = None
            except (httpx.HTTPError, ZetarisError, ValueError, sqlglot.errors.SqlglotError) as e:
                cols, rows, error, executed = [], [], str(e)[:400], sql
        truncated = len(rows) > limit
        rows = [[_coerce(v) for v in r] for r in rows[:limit]]
        return QueryResult(sql, cols, rows, len(rows), truncated, t.ms, self.name, executed, error)

    def admin(self, sql: str) -> tuple[list[str], list[list]]:
        """Run a registration statement (setup only, never exposed to agents)."""
        return self._run(sql, 1000)

    def catalog(self) -> list[TableInfo]:
        """Tables, columns and row counts of the dataset's registered sources. Built with parallel requests and
        shared across harness instances for a few minutes, since every investigation starts by reading it."""
        if self._catalog is not None:
            return self._catalog
        key = (self.base, self.dataset.key)
        hit = _CATALOGS.get(key)
        if hit and time.time() - hit[0] < CATALOG_TTL_S:
            self._catalog = hit[1]
            return self._catalog

        def tables_of(src: str) -> list[tuple[str, str]]:
            try:
                _, rows = self._run(f"SHOW TABLES IN {self._map[src]}", 500)
            except (httpx.HTTPError, ZetarisError, ValueError):
                return []
            return [(src, str(r[0]).split(".", 1)[-1]) for r in rows]

        def describe(st: tuple[str, str]) -> TableInfo:
            src, table = st
            zname = self._map[src]
            columns, n = [], None
            try:
                _, drows = self._run(f"DESCRIBE {zname}.{table}", 500)
                columns = [Column(str(d[0]), str(d[1]) if len(d) > 1 else "") for d in drows
                           if d and d[0] and not str(d[0]).startswith("#")]
            except (httpx.HTTPError, ZetarisError, ValueError):
                pass
            try:
                _, crows = self._run(f"SELECT COUNT(*) FROM {zname}.{table}", 1)
                n = int(float(crows[0][0]))
            except (httpx.HTTPError, ZetarisError, ValueError, IndexError):
                pass
            return TableInfo(src, table.lower(), columns, n)

        with ThreadPoolExecutor(max_workers=8) as pool:
            pairs = [p for ps in pool.map(tables_of, self.dataset.sources) for p in ps]
            out = list(pool.map(describe, pairs))
        self._catalog = out
        if out:
            _CATALOGS[key] = (time.time(), out)
        return out

    def status(self, attempts: int = 3) -> dict:
        """Reachable? Under load the platform sometimes answers a query with a transient HTTP 400/5xx, so a failed
        check is retried before the caller falls back to the local copy."""
        err = ""
        for i in range(attempts):
            try:
                self._run("SELECT 1 AS ok", 1)
                return {"ok": True, "harness": self.name, "url": self.base, "mode": self.mode}
            except Exception as e:
                err = str(e)[:200]
                if i + 1 < attempts:
                    time.sleep(1.5 * (i + 1))
        return {"ok": False, "harness": self.name, "url": self.base, "mode": self.mode, "error": err}


def parse_response(payload) -> tuple[list[str], list[list]]:
    """The shapes Zetaris returns: {headers, data: [[...]]} (run-query), {records: [{...}]} (query start), or
    {data|rows, columns|header|schema} (older endpoints)."""
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        recs = payload["records"]
        cols = list(recs[0].keys()) if recs else []
        return cols, [[r.get(c) for c in cols] for r in recs]
    if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
        payload = payload["data"]
    data = payload.get("data", payload.get("rows", [])) if isinstance(payload, dict) else payload
    cols_raw = None
    if isinstance(payload, dict):
        for k in ("headers", "columns", "header", "columnNames", "schema", "fields"):
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


def _coerce(v):
    """Zetaris returns values as strings; turn obvious numbers back into numbers."""
    if not isinstance(v, str) or not _NUMBER.match(v):
        return v
    return int(v) if v.lstrip("-").isdigit() else float(v)


_NUMBER = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")
