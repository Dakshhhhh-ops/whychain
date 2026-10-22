"""Check that the numbers an agent states come from the data it cites.

Re-running a cited query only proves the query is real. This checks the claim itself. Each number in it must be
supported in one of these ways, from strongest to weakest:

  key      the agent declared it in key_numbers with an exact cell reference (result id, column, row), and that
           cell holds the value (allowing for the claim's rounding, or a fraction written as a percentage)
  row      it sits in the same result row as a verified key number (e.g. the transit days next to a late rate)
  derived  the wording implies arithmetic ("from A to B", "up N points", "X of Y", "N times") and it equals that
           operation on two other supported numbers in the same text
  found    it appears in the cited rows AND is distinctive: two or more decimals, or a whole number of 100 or more
           (a one-decimal rate like "14.1" or a small count like "13" collides with too many cells to count)
  param    it appears in the SQL of a cited query (a window length, a threshold)

Anything else is "missing". Years, dates and evidence ids are not checked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_DATES = re.compile(
    r"\b\d{4}-\d{2}(-\d{2})?\b"                                   # 2018-03, 2018-03-12
    r"|\b\d{1,2}/\d{1,2}(/\d{2,4})?\b"                            # 12/03/2018
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(\s*[-–]\s*\d{1,2})?(?!\d)"  # Mar 12-13
    r"|\b\d{1,2}(\s*[-–]\s*\d{1,2})?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"  # 12-13 March
    r"|\b[A-Z]\d*\.[QDPA]\d+\b"                                    # evidence ids like T2.Q1
    r"|\b[A-Za-z]{1,4}-\d+\b"                                     # ids like S-14, C02 handled below
    r"|\b[A-Z]{1,4}\d+\b",                                        # ids like C02, T2, A1
    re.I)
_NUM = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.,])-?\d+(?:\.\d+)?(?![\w])")

_DIFF = re.compile(r"\b(up|down|rose|rise|rising|fell|fall|falling|drop|dropped|increase[sd]?|decrease[sd]?|higher|lower|"
                   r"more|less|gap|difference|change[sd]?|delta|jump(ed)?|grew|growth|declin\w*|points?|pp|"
                   r"from|to|vs|versus|against|than)\b|→|->|\+")
_TIMES = re.compile(r"\b(times|fold|double[sd]?|triple[sd]?)\b|\d\s*x\b")
_RATIO = re.compile(r"\b(of|share|per|ratio)\b|%")
_SUM = re.compile(r"\b(total|combined|together|sum|plus|altogether|overall)\b")


@dataclass
class Num:
    raw: str
    value: float
    dec: int
    start: int
    end: int
    pct: bool


def numbers_in(text: str) -> list[Num]:
    """Numbers worth checking in a text: not years, dates, ids, 0 or 1."""
    t = str(text or "")
    masked = _DATES.sub(lambda m: " " * len(m.group(0)), t)
    masked = re.sub(r"(?<=\d)[xX](?=\W|$)", " ", masked)  # "2.1x" is the number 2.1
    out = []
    for m in _NUM.finditer(masked):
        raw = m.group(0)
        v = float(raw.replace(",", ""))
        dec = len(raw.split(".")[1]) if "." in raw else 0
        if dec == 0 and (v in (0, 1) or 1990 <= v <= 2100):
            continue
        pct = t[m.end():m.end() + 2].lstrip().startswith("%")
        out.append(Num(raw, v, dec, m.start(), m.end(), pct))
    return out


def _tol(dec: int) -> float:
    return 0.5 * 10 ** (-dec) + 1e-9


def _eq(n: Num, cell: float) -> bool:
    if abs(cell - n.value) <= _tol(n.dec):
        return True
    return n.pct and abs(cell) <= 1.0 and abs(cell * 100 - n.value) <= _tol(n.dec)


def _distinctive(n: Num) -> bool:
    return n.dec >= 2 or (n.dec == 0 and abs(n.value) >= 100)


def _to_float(c) -> float | None:
    if isinstance(c, bool):
        return None
    try:
        return float(str(c).replace(",", ""))
    except (TypeError, ValueError):
        return None


@dataclass
class Table:
    id: str
    columns: list[str]
    rows: list[list]
    sql: str = ""
    truncated: bool = False


def _flat(obj, depth: int = 0):
    if depth > 4:
        return
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _flat(v, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj[:200]:
            yield from _flat(v, depth + 1)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield obj


def tables_from(evidence: list[dict]) -> list[Table]:
    out = []
    for ev in evidence:
        if ev.get("type") == "sql":
            out.append(Table(ev.get("id", ""), [str(c) for c in ev.get("columns", [])], ev.get("rows", []) or [],
                             ev.get("sql", ""), bool(ev.get("truncated"))))
        elif ev.get("type") == "optimization":
            out.append(Table(ev.get("id", ""), [], [list(_flat({k: v for k, v in ev.items() if k != "id"}))]))
        elif ev.get("type") == "doc":
            nums = [n.value for n in numbers_in(ev.get("quote", ""))]
            out.append(Table(ev.get("id", ""), ["quote_number"] * len(nums), [nums]))
        elif ev.get("type") == "analysis":
            out.append(Table(ev.get("id", ""), [str(c) for c in ev.get("columns", [])], ev.get("rows", []) or [],
                             ev.get("sql", ""), False))
    return out


def _cells(rows: list, cap: int = 2000) -> list[float]:
    vals = []
    for row in rows or []:
        for c in (row if isinstance(row, (list, tuple)) else [row]):
            f = _to_float(c)
            if f is not None:
                vals.append(f)
                if len(vals) >= cap:
                    return vals
    return vals


def _row_matches(row: list, columns: list[str], where: dict) -> bool:
    for k, want in (where or {}).items():
        if str(k).lower() not in [c.lower() for c in columns]:
            return False
        got = row[[c.lower() for c in columns].index(str(k).lower())]
        a, b = _to_float(got), _to_float(want)
        if a is not None and b is not None:
            if abs(a - b) > 1e-6:
                return False
        elif str(got).strip().lower() != str(want).strip().lower() and not str(got).strip().lower().startswith(str(want).strip().lower()):
            return False
    return True


def resolve_ref(ref: dict, tables: dict[str, Table], fetch=None) -> tuple[float | None, list | None, str]:
    """The value of the cell a key number points at: (value, the whole row, problem). The row may come from a
    re-fetch of a truncated result, so it is returned itself rather than as an index into the stored sample."""
    t = tables.get(str(ref.get("evidence", "")))
    if t is None:
        return None, None, "cites a result that isn't in the evidence"
    if not t.columns:
        return None, None, "result has no columns"
    col = ref.get("column")
    cols = [c.lower() for c in t.columns]
    if isinstance(col, int) and 0 <= col < len(cols):
        ci = col
    elif str(col).lower() in cols:
        ci = cols.index(str(col).lower())
    else:
        return None, None, f"no column {col!r}"
    rows = t.rows
    where, ri = ref.get("where"), None
    if isinstance(where, dict) and where:
        idx = [i for i, r in enumerate(rows) if _row_matches(r, t.columns, where)]
        if not idx and t.truncated and fetch:
            rows = fetch(t) or rows
            idx = [i for i, r in enumerate(rows) if _row_matches(r, t.columns, where)]
        if not idx:
            return None, None, f"no row where {where}"
        ri = idx[0]
    elif isinstance(ref.get("row"), int) and 0 <= ref["row"] < len(rows):
        ri = ref["row"]
    elif len(rows) == 1:
        ri = 0
    else:
        return None, None, "no row given"
    return _to_float(rows[ri][ci]), list(rows[ri]), ""


_UNIT_WORDS = [
    ("pct", re.compile(r"^\s*(%|percent|pp\b|points?\b|percentage)")),
    ("time", re.compile(r"^\s*(days?|hours?|weeks?|business days?)\b")),
    ("money", re.compile(r"^\s*(brl|inr|reais|rupees)\b")),
    ("count", re.compile(r"^\s*(\w+\s+)?(orders?|units?|items?|sellers?|customers?|reviews?|shipments?|parcels?|"
                         r"lines?|skus?|purchase orders?|pos?)\b")),
]
_MONEY_BEFORE = re.compile(r"(brl|inr|r\$|₹|\$)\s*$")
_COLUMN_KINDS = [  # order matters: "share_of_orders_pct" is a rate, "late_orders" a count, "orders_per_day" a count
    ("pct", re.compile(r"pct|percent|rate|share|ratio|frac|ontime|on_time|_pp$")),
    ("count", re.compile(r"(^|_)(n|cnt|count|orders?|units?|qty|quantity|items?|num|lines?|sellers?|customers?|"
                         r"reviews?|parcels?|mentions?|handed_over)($|_)")),
    ("time", re.compile(r"day|transit|time|lead|hour|delay|dur|lag|week")),
    ("money", re.compile(r"price|value|cost|freight|payment|gmv|revenue|amount|brl|inr|spend|penalty|sales")),
]


def unit_of(text: str, n: Num) -> str | None:
    after = text[n.end:n.end + 30].lower()
    if _MONEY_BEFORE.search(text[max(0, n.start - 6):n.start].lower()):
        return "money"
    for kind, rx in _UNIT_WORDS:
        if rx.search(after):
            return kind
    return None


def column_kind(name: str) -> str | None:
    nm = str(name).lower()
    for kind, rx in _COLUMN_KINDS:
        if rx.search(nm):
            return kind
    return None


def _kind_ok(unit: str | None, column: str | None) -> bool:
    if unit is None or column is None:
        return True
    k = column_kind(column)
    return k is None or k == unit


def _typed_cells(rows: list, columns: list[str]) -> list[tuple[float, str | None]]:
    out = []
    for row in rows or []:
        cells = row if isinstance(row, (list, tuple)) else [row]
        for j, c in enumerate(cells):
            f = _to_float(c)
            if f is not None:
                out.append((f, columns[j] if j < len(columns) else None))
    return out


def _window(text: str, n: Num, before: int = 40, after: int = 12) -> str:
    """The words around a number, within its own clause."""
    pre = text[max(0, n.start - before):n.start]
    cut = max(pre.rfind("; "), pre.rfind(". "), pre.rfind(", "), pre.rfind("\n"))
    pre = pre[cut + 1:] if cut >= 0 else pre
    post = re.split(r"[;\n]|[.,]\s", text[n.end:n.end + after])[0]
    return pre.lower() + " " + post.lower()


def check_numbers(text: str, evidence: list[dict], key_numbers=None, fetch=None, extra_values=()) -> dict:
    """Classify every number in `text` by how the cited evidence supports it (see the module docstring)."""
    tables = tables_from(evidence)
    by_id = {t.id: t for t in tables}
    key_vals, key_rows, key_problems = [], [], []
    for ref in key_numbers if isinstance(key_numbers, list) else []:
        if not isinstance(ref, dict) or _to_float(ref.get("value")) is None:
            continue
        cell, row, why = resolve_ref(ref, by_id, fetch)
        declared = Num(str(ref["value"]), float(_to_float(ref["value"])), len(str(ref["value"]).split(".")[1]) if "." in str(ref["value"]) else 0, 0, 0, True)
        if cell is not None and (_eq(declared, cell) or abs(cell - declared.value) <= max(_tol(declared.dec), 1e-6)):
            key_vals.append((cell, str(ref.get("column"))))
            key_rows.append((by_id[str(ref["evidence"])], row))
        else:
            key_problems.append(f"{ref.get('value')} @ {ref.get('evidence')}.{ref.get('column')}: "
                                f"{why or f'the cell holds {cell}'}")
    for t in tables:  # every number of a verified verbatim quote counts like a cited cell
        if t.columns and set(t.columns) == {"quote_number"}:
            key_vals += [(v, None) for v in _cells(t.rows)]
    row_cells = [tc for t, row in key_rows for tc in _typed_cells([row], t.columns)]
    found_cells = [tc for t in tables for tc in _typed_cells(t.rows, t.columns)]
    # numbers written in the SQL itself (a window, a threshold), not inside quoted literals like '2018-02-26'
    sql_tokens = {float(x) for t in tables
                  for x in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", re.sub(r"'[^']*'|\"[^\"]*\"", " ", t.sql or ""))}
    nums = numbers_in(text)
    units = [unit_of(text, n) for n in nums]
    support: dict[int, str] = {}
    for i, n in enumerate(nums):
        unit = units[i]
        if any(_eq(n, v) and _kind_ok(unit, col) for v, col in key_vals) or any(_eq(n, v) for v in extra_values):
            support[i] = "key"
        elif any(_eq(n, c) and _kind_ok(unit, col) for c, col in row_cells):
            support[i] = "row"
        elif _distinctive(n) and any(_eq(n, c) and _kind_ok(unit, col) for c, col in found_cells):
            support[i] = "found"
        elif n.dec == 0 and n.value in sql_tokens:
            support[i] = "param"
    changed = True
    while changed:  # derived numbers can support others ("from A to B, up N points")
        changed = False
        for i, n in enumerate(nums):
            if i in support:
                continue
            w, u = _window(text, n), units[i]
            others = [(nums[j].value, units[j]) for j in support if j != i]
            # operands must share a unit ("from 14.1% to 19.0%", "1,328 of 7,003")
            pairs = [((a, b), ua or ub) for x, (a, ua) in enumerate(others) for b, ub in others[x + 1:]
                     if ua is None or ub is None or ua == ub]
            cands = []
            if _DIFF.search(w):  # a difference keeps the operands' unit
                cands += [abs(a - b) for (a, b), pu in pairs if u is None or pu is None or pu == u]
            if _SUM.search(w):
                cands += [a + b for (a, b), pu in pairs if u is None or pu is None or pu == u]
            if _TIMES.search(w):  # "2.1x", "twice"
                cands += [r for (a, b), _ in pairs if a and b for r in (a / b, b / a)]
            elif _RATIO.search(w):  # "X of Y (Z%)"
                cands += [r for (a, b), _ in pairs if a and b for r in (100 * a / b, 100 * b / a)]
            if any(abs(c - n.value) <= _tol(n.dec) for c in cands):
                support[i] = "derived"
                changed = True
    missing = [n.raw for i, n in enumerate(nums) if i not in support]
    return {"checked": len(nums), "missing": missing,
            "support": {n.raw: support.get(i, "missing") for i, n in enumerate(nums)},
            "key_numbers": {"verified": len(key_vals), "problems": key_problems}}


# kept for callers of the first version
def check_claim(text: str, evidence: list[dict], key_numbers=None, fetch=None) -> dict:
    return check_numbers(text, evidence, key_numbers, fetch)


def figure_supported(raw, evidence: list[dict]) -> bool:
    """An action's money or count figure: it must equal a cell in the rows the action cites (rounded to the
    figure's own precision; whole money amounts may round a cell with cents). No arithmetic is allowed."""
    v = _to_float(raw)
    if v is None:
        return False
    if v == 0:
        return True
    dec = len(str(raw).split(".")[1]) if "." in str(raw) else 0
    n = Num(str(raw), v, dec, 0, 0, False)
    return any(_eq(n, c) for t in tables_from(evidence) for c in _cells(t.rows))
