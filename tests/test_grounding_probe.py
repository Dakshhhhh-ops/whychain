"""Probe: how often does the numbers check accept a number the agent made up?

Evidence tables are shaped like real investigation results (a monthly series and a per-state breakdown, with rates,
counts, days, money and review scores). Honest claims cite cells through key_numbers. Fabricated numbers of every
kind an agent invents (one-decimal rates, small day counts, round money, counts) are inserted into realistic claim
wordings, including wordings that imply arithmetic. The check must reject at least 90% of them.
"""
import random

from whychain.grounding import check_numbers

STATES = ["SP", "RJ", "MG", "RS", "PR", "SC", "BA", "DF", "ES", "GO", "PE", "CE", "PA", "MT", "MA", "MS", "PB", "PI",
          "RN", "AL", "SE", "TO", "RO", "AM", "AC", "AP", "RR"]


def tables(seed: int):
    rnd = random.Random(seed)
    months = [f"2017-{m:02d}" for m in range(1, 13)] + [f"2018-{m:02d}" for m in range(1, 9)]
    monthly = {"id": "T1.Q1", "type": "sql", "sql": "SELECT month, orders, late_pct, transit_days, review, gmv FROM o "
               "WHERE purchase >= '2017-01-01' AND purchase < '2018-09-01' AND handover >= '2018-02-26' "
               "GROUP BY month LIMIT 100",
               "columns": ["month", "orders", "late_pct", "transit_days", "review", "gmv"],
               "rows": [[m, rnd.randint(800, 7500), round(rnd.uniform(3, 22), 1), round(rnd.uniform(6, 14), 1),
                         round(rnd.uniform(3.8, 4.4), 2), round(rnd.uniform(1e5, 1.2e6), 2)] for m in months]}
    states = {"id": "T2.Q1", "type": "sql", "sql": "SELECT state, orders, late_pct, base_late_pct, transit_days FROM s "
              "WHERE purchase BETWEEN '2018-03-01' AND '2018-03-31' GROUP BY state HAVING COUNT(*) >= 30",
              "columns": ["state", "orders", "late_pct", "base_late_pct", "transit_days"],
              "rows": [[s, rnd.randint(30, 6000), round(rnd.uniform(2, 60), 1), round(rnd.uniform(2, 30), 1),
                        round(rnd.uniform(5, 30), 1)] for s in STATES]}
    return [monthly, states]


def fabricated(rnd: random.Random) -> tuple[str, str]:
    """A made-up number, written the way an agent would write it, and a template to put it in."""
    kind = rnd.choice(["rate1", "rate2", "days", "days1", "count", "money", "review", "points", "ratio"])
    if kind == "rate1":
        return f"{rnd.uniform(1, 60):.1f}", "{x}% of orders were late"
    if kind == "rate2":
        return f"{rnd.uniform(1, 60):.2f}", "the late rate reached {x}%"
    if kind == "days":
        return str(rnd.randint(2, 40)), "transit took {x} days"
    if kind == "days1":
        return f"{rnd.uniform(2, 40):.1f}", "median transit rose to {x} days"
    if kind == "count":
        return f"{rnd.randint(100, 9000):,}", "{x} orders were affected"
    if kind == "money":
        return f"{rnd.choice([500, 1000, 2500, 5000, 10000, 15000, 25000, 50000]):,}", "outreach would cost BRL {x}"
    if kind == "review":
        return f"{rnd.uniform(1, 5):.2f}", "review scores averaged {x}"
    if kind == "points":
        return f"{rnd.uniform(0.5, 30):.1f}", "late deliveries were up {x} points"
    return f"{rnd.uniform(1.2, 5):.1f}", "late orders were {x}x more common"


def honest_claim(ev: list[dict], rnd: random.Random) -> tuple[str, list[dict]]:
    """A real claim: two months' late rates from cited cells, the change between them, and that month's transit."""
    rows = ev[0]["rows"]
    a, b = rnd.sample(range(len(rows)), 2)
    ra, rb = rows[a], rows[b]
    text = (f"Late deliveries rose from {ra[2]}% in {ra[0]} to {rb[2]}% in {rb[0]}, a change of "
            f"{abs(rb[2] - ra[2]):.1f} points, with transit at {rb[3]} days on {rb[1]:,} orders")
    keys = [{"value": ra[2], "evidence": "T1.Q1", "column": "late_pct", "where": {"month": ra[0]}},
            {"value": rb[2], "evidence": "T1.Q1", "column": "late_pct", "where": {"month": rb[0]}}]
    return text, keys


def test_fabricated_numbers_rarely_pass():
    rnd = random.Random(7)
    trials, passed = {}, {}
    for seed in range(40):
        ev = tables(seed)
        for _ in range(25):
            base, keys = honest_claim(ev, rnd)
            x, template = fabricated(rnd)
            c = check_numbers(base + "; " + template.format(x=x), ev, keys)
            trials[template] = trials.get(template, 0) + 1
            passed[template] = passed.get(template, 0) + (c["support"].get(x, "missing") != "missing")
    overall = sum(passed.values()) / sum(trials.values())
    assert overall < 0.10, f"false-pass rate {overall:.1%} over {sum(trials.values())} fabricated numbers"
    worst = max(trials, key=lambda t: passed[t] / trials[t])
    assert passed[worst] / trials[worst] < 0.10, f"{worst!r}: {passed[worst]}/{trials[worst]} fabricated numbers passed"


def test_honest_cited_numbers_pass():
    rnd = random.Random(3)
    ok = total = 0
    for seed in range(40):
        ev = tables(seed)
        text, keys = honest_claim(ev, rnd)
        c = check_numbers(text, ev, keys)
        total += 1
        ok += not c["missing"]
    assert ok / total >= 0.95, f"only {ok}/{total} honest claims fully supported"


def test_reviewers_examples():
    ev = tables(1)
    # invented money and a survivorship-style rate are not supported
    c = check_numbers("Outreach costs BRL 5,000 and 35.31% were late after the strike", ev)
    assert c["missing"] == ["5,000", "35.31"]
    # small whole numbers are checked, not skipped: "13 -> 18 days" needs cited cells
    c = check_numbers("transit went from 13 to 18 days", ev)
    assert c["missing"] == ["13", "18"]
    rows = ev[1]["rows"]
    keys = [{"value": rows[0][4], "evidence": "T2.Q1", "column": "transit_days", "where": {"state": rows[0][0]}},
            {"value": rows[1][4], "evidence": "T2.Q1", "column": "transit_days", "where": {"state": rows[1][0]}}]
    c = check_numbers(f"transit went from {rows[1][4]} to {rows[0][4]} days", ev, keys)
    assert c["missing"] == []
    # a key number pointing at the wrong cell is reported
    bad = [{"value": 99.9, "evidence": "T2.Q1", "column": "late_pct", "where": {"state": "RJ"}}]
    c = check_numbers("RJ was 99.9% late", ev, bad)
    assert c["missing"] == ["99.9"] and c["key_numbers"]["problems"]
