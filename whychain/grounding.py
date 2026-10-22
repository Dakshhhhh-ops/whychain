"""Check that the numbers an agent states actually appear in the data it cites.

Re-running a cited query only proves the query is real. This checks the claim: each number in it must match a value
in the cited rows, allowing for rounding, percentages written as fractions, and one step of arithmetic between two
cited values (a difference, like "up 9.2 points", or a ratio, like "2.1x" or "12% of"). Dates, years and small
counts are skipped, because they are usually part of the question rather than results.
"""
from __future__ import annotations

import re
from itertools import islice

# dates and ranges that would otherwise look like numbers
_DATES = re.compile(
    r"\b\d{4}-\d{2}(-\d{2})?\b"                                   # 2018-03, 2018-03-12
    r"|\b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b"                      # 12/03, 12-13
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(\s*[-–]\s*\d{1,2})?\b"  # Mar 12-13
    r"|\b\d{1,2}(\s*[-–]\s*\d{1,2})?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"  # 12-13 March
    r"|\b[A-Z]\d*\.[QDP]\d+\b"                                    # evidence ids like T2.Q1
    r"|\b[A-Z]{1,3}-\d+\b",                                       # ids like S-14
    re.I)
_NUM = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w.])|(?<![\w.,])-?\d+(?:\.\d+)?(?![\w])")


def numbers_in(text: str) -> list[tuple[str, float, int]]:
    """Numbers worth checking in a claim, as (text, value, decimals)."""
    t = _DATES.sub(" ", str(text or ""))
    out = []
    for m in _NUM.finditer(t):
        raw = m.group(0)
        v = float(raw.replace(",", ""))
        dec = len(raw.split(".")[1]) if "." in raw else 0
        if dec == 0 and (abs(v) < 32 or 1990 <= v <= 2100):  # small counts, days of month, years
            continue
        out.append((raw, v, dec))
    return out


def _cells(rows: list, cap: int = 400) -> list[float]:
    vals = []
    for row in rows or []:
        for c in (row if isinstance(row, (list, tuple)) else [row]):
            try:
                vals.append(float(str(c).replace(",", "")))
            except (TypeError, ValueError):
                continue
            if len(vals) >= cap:
                return vals
    return vals


def _close(claim: float, dec: int, cell: float) -> bool:
    tol = 0.5 * 10 ** (-dec) + 1e-9
    return abs(cell - claim) <= tol or abs(cell * 100 - claim) <= tol or abs(cell / 100 - claim) <= tol


def is_grounded(value: float, dec: int, cells: list[float]) -> bool:
    if any(_close(value, dec, c) for c in cells):
        return True
    small = list(islice(cells, 120))  # one arithmetic step between two cited values
    for i, a in enumerate(small):
        for b in small[i + 1:]:
            for d in (a - b, b - a, a + b):
                if _close(value, dec, d):
                    return True
            for x, y in ((a, b), (b, a)):
                if y and (_close(value, dec, x / y) or _close(value, dec, 100 * x / y)):
                    return True
    return False


def check_claim(text: str, evidence: list[dict]) -> dict:
    """Which numbers in `text` appear in the rows of the cited SQL / optimization evidence."""
    cells: list[float] = []
    quotes = []
    for ev in evidence:
        if ev.get("type") == "sql":
            cells += _cells(ev.get("rows", []))
        elif ev.get("type") == "optimization":
            cells += _cells([[v for v in _flat(ev)]])
        elif ev.get("type") == "doc":
            quotes.append(str(ev.get("quote", "")))
    cells += [v for q in quotes for _, v, _ in numbers_in(q)]
    nums = numbers_in(text)
    missing = [raw for raw, v, dec in nums if not is_grounded(v, dec, cells)]
    return {"checked": len(nums), "missing": missing}


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
