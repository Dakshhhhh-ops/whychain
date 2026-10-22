"""From a question to a fixed battery of tests, and from test results to findings with coded verdicts.

The battery is the same for every question of a kind, so runs are reproducible. A language model may add a few
extra checks (and the Document Analyst always reads the written record), but the core tests, their numbers and their
verdicts don't depend on a model.

Verdict rules (all in code):
  stage      a cause if it explains >= 10% of the rise (Shapley share); ruled out below that or if it went the other way
  segments   where the rise sits (not a cause by itself); a mix shift that explains >= 25% is a cause
  volume     a cause if orders/day rose >= 25% AND hand-over (+0.5 day) or transit (+1 day) slowed with it
  event      a cause only if the effect starts with the event (vs the prior trend) and, for a regional event, is larger
             in that region; 'partial' if it added to a problem that was already there; otherwise ruled out
  data       an artefact if more than 2% of delivered orders have missing or impossible dates
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date, timedelta

import pandas as pd

from whychain.analysis.engine import (CAUSE_SHARE, Engine, Result, Scope, baseline_from_question, events_in_text,
                                      period_from_question)

LATENESS = re.compile(r"\b(late|lateness|delay|delayed|delays|on[- ]?time|deliver(y|ies|ed)?|transit|slow|slower)\b", re.I)
NOT_LATENESS = re.compile(r"\b(stock[- ]?outs?|out of stock|run out|ran out|backlog|backorder\w*|inventory|"
                          r"at risk|next week|refund|price|revenue)\b", re.I)


def battery_applies(question: str) -> bool:
    """The battery answers questions about late deliveries. Stock, backlog and future-risk questions go to the
    model-driven SQL investigators instead."""
    return bool(LATENESS.search(question)) and not NOT_LATENESS.search(question)
from whychain.analysis.model import BR_REGION

SHORT_EVENT_DAYS = 31     # longer, ongoing situations get a regional comparison instead of an onset test

STATE_NAMES = {
    "acre": "AC", "alagoas": "AL", "amapá": "AP", "amapa": "AP", "amazonas": "AM", "bahia": "BA", "ceará": "CE",
    "ceara": "CE", "distrito federal": "DF", "brasília": "DF", "brasilia": "DF", "espírito santo": "ES",
    "espirito santo": "ES", "goiás": "GO", "goias": "GO", "maranhão": "MA", "maranhao": "MA", "mato grosso do sul": "MS",
    "mato grosso": "MT", "minas gerais": "MG", "pará": "PA", "para": "PA", "paraíba": "PB", "paraiba": "PB",
    "paraná": "PR", "parana": "PR", "pernambuco": "PE", "piauí": "PI", "piaui": "PI", "rio de janeiro": "RJ",
    "rio grande do norte": "RN", "rio grande do sul": "RS", "rondônia": "RO", "rondonia": "RO", "roraima": "RR",
    "santa catarina": "SC", "são paulo": "SP", "sao paulo": "SP", "sergipe": "SE", "tocantins": "TO",
}
REGION_WORDS = {"northeast": "Northeast", "nordeste": "Northeast", "north": "North", "southeast": "Southeast",
                "sudeste": "Southeast", "south": "South", "centre-west": "Centre-West", "center-west": "Centre-West",
                "midwest": "Centre-West"}


# ---------------------------------------------------------------------- reading the question
def places_in_text(q: str, engine: Engine) -> dict:
    """Locations named in the question, as filters on the fact table."""
    t = " " + q.lower() + " "
    m = engine.m
    if m.region_map:
        for word, region in REGION_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", t) and not re.search(rf"\b{re.escape(word)}\s+(america|pole)", t):
                if word == "north" and re.search(r"\bnorth[- ]?east", t):
                    continue
                if word == "south" and re.search(r"\bsouth[- ]?east", t):
                    continue
                return {"region": [region]}
        states = []
        for name, code in sorted(STATE_NAMES.items(), key=lambda kv: -len(kv[0])):
            if re.search(rf"\b{re.escape(name)}\b", t) and code not in states:
                if name in ("para", "pará") and not re.search(r"\b(state of )?par[aá]\b(?! the)", t):
                    continue
                t = t.replace(name, " ")
                states.append(code)
        for code in sorted(set(BR_REGION)):
            if re.search(rf"\b{code}\b", q):
                states.append(code)
        return {m.region_source: sorted(set(states))} if states else {}
    f = engine.facts()
    for dim in (m.map_within, "warehouse_id"):
        if dim not in f.columns:
            continue
        vals = [v for v in f[dim].dropna().astype(str).unique() if len(v) > 2 and re.search(rf"\b{re.escape(v.lower())}\b", t)]
        if vals:
            return {dim: sorted(vals)}
    return {}


def similar_distance(engine: Engine, filters: dict, tolerance: float = 3.0) -> dict | None:
    """States (or cities) whose median promised days are within `tolerance` of the focus's: a proxy for distance
    from the sellers, since promises are set by distance. Returns None if fewer than two qualify."""
    d = engine.delivered()
    dim = engine.m.map_within
    focus = engine._apply(d, filters)
    if focus.empty:
        return None
    target = float(focus["p"].median())
    med = d.groupby(dim)["p"].agg(["median", "size"])
    focus_vals = set(focus[dim].astype(str))
    comp = [str(k) for k, r in med.iterrows() if abs(r["median"] - target) <= tolerance and r["size"] >= 300
            and str(k) not in focus_vals]
    return {dim: sorted(comp)} if len(comp) >= 2 else None


def _scope_spec(mode: str, label: str, **kw) -> dict:
    return {"mode": mode, "label": label, **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in kw.items()}}


def build_scope(engine: Engine, spec: dict) -> Scope:
    if spec["mode"] == "time":
        base = spec.get("baseline")
        return engine.time_scope(date.fromisoformat(spec["start"]), date.fromisoformat(spec["end"]),
                                 spec.get("filters") or None, spec["label"],
                                 (date.fromisoformat(base[0]), date.fromisoformat(base[1])) if base else None)
    start = date.fromisoformat(spec["start"]) if spec.get("start") else None
    end = date.fromisoformat(spec["end"]) if spec.get("end") else None
    return engine.segment_scope(spec["filters"], spec.get("compare_with"), start, end, spec["label"])


def plan_battery(question: str, engine: Engine, as_of: date) -> dict:
    """The scopes and the fixed tests for a question."""
    period = period_from_question(question, as_of)
    places = places_in_text(question, engine)
    ev = engine.events()
    named = events_in_text(question, ev)
    explicit_base = baseline_from_question(question, as_of)
    scopes = []
    names = engine.m.place_names
    place_label = ", ".join(f"{v} ({names[v]})" if v in names else v for vals in places.values() for v in vals)
    short_named = [x for x in named if _duration(ev, x) is not None and _duration(ev, x) <= SHORT_EVENT_DAYS]
    if short_named:  # a question about a dated event: the event's own window is the scope
        period = None
    if period:
        a, b, lab = period
        scopes.append(_scope_spec("time", f"orders to {place_label} placed {lab}" if places else f"orders placed {lab}",
                                  start=a, end=b, filters=places,
                                  baseline=[explicit_base[0].isoformat(), explicit_base[1].isoformat()]
                                  if explicit_base else None))
    if not period and short_named:
        e = ev[ev["event_id"] == short_named[0]].iloc[0]
        a = pd.Timestamp(e["start_date"]).date()
        b = (pd.Timestamp(e["end_date"]) if pd.notna(e["end_date"]) and str(e["end_date"]) not in ("", "None")
             else pd.Timestamp(a)).date() + timedelta(days=1)
        if (b - a).days < 7:
            b = a + timedelta(days=7)
        scopes.append(_scope_spec("time", f"orders placed during {e['name']} ({a} to {b - timedelta(days=1)})",
                                  start=a, end=b, filters=places))
    if places and not period and not short_named:
        scopes.append(_scope_spec("segment", f"orders to {place_label}", filters=places))
        comp = similar_distance(engine, places)
        if comp:
            scopes.append(_scope_spec("segment", f"orders to {place_label}", filters=places, compare_with=comp))
    if not scopes:  # no period, place or event: the latest full month vs its normal baseline
        end = (pd.Timestamp(as_of).replace(day=1)).date()
        start = (pd.Timestamp(end) - pd.DateOffset(months=1)).date()
        scopes.append(_scope_spec("time", f"orders placed {start:%B %Y}", start=start, end=end))

    primary = scopes[0]
    tasks: list[dict] = []

    def add(tool: str, statement: str, scope: dict, **params):
        tasks.append({"tool": tool, "statement": statement, "scope": scope, "params": params})

    time_mode = primary["mode"] == "time"
    geo = engine.m.map_within
    add("measure", f"Measure the symptom: late deliveries for {primary['label']}, against "
        + ("a normal baseline" if time_mode else "the other orders"), primary)
    hand_label = "Sellers handed orders to the carrier later" if engine.m.region_map else "Warehouses dispatched orders later"
    add("stage", hand_label + (" than normal" if time_mode else " than elsewhere"), primary, stage="handover")
    if engine.m.stock_sql:  # datasets with stock data: was dispatch held up by stock-outs?
        add("stock_wait", "Orders waited for stock, which slowed dispatch", primary)
    add("stage", "Carrier transit got slower" if time_mode else "Carrier transit takes longer", primary, stage="transit")
    add("stage", "Promised delivery windows got shorter" if time_mode
        else "Promised windows are too short for the delivery time", primary, stage="promise")
    if time_mode:
        add("segments", f"The rise is concentrated in some places (by {engine.m.dims.get(geo, geo)})", primary, dim=geo)
        second = "seller_state" if "seller_state" in engine.m.dims else "carrier_id"
        add("segments", f"The rise is concentrated in some segments (by {engine.m.dims.get(second, second)})", primary,
            dim=second)
        add("volume", "A surge in orders overloaded hand-over or transit", primary)
    else:
        second = "interstate" if "interstate" in engine.m.dims else "carrier_id"
        add("segments", f"A different mix of orders (by {engine.m.dims.get(second, second)}) explains part of the gap",
            primary, dim=second)
    add("data_quality", "The symptom is a data artefact (missing or impossible dates, duplicates, definition)", primary)
    if engine.m.reviews_sql:
        add("text", "Customer reviews describe the problem", primary)
    # dated events: the ones named in the question, plus short ones overlapping the period
    cands = list(short_named)
    if primary.get("start") and primary["mode"] == "time":
        a, b = pd.Timestamp(primary["start"]), pd.Timestamp(primary["end"])
        for _, e in ev.iterrows():
            s0 = pd.Timestamp(e["start_date"])
            e0 = pd.Timestamp(e["end_date"]) if pd.notna(e["end_date"]) and str(e["end_date"]) not in ("", "None") else s0
            if s0 <= b and e0 >= a - pd.Timedelta(days=21) and str(e["event_id"]) not in cands:
                cands.append(str(e["event_id"]))
    short = [x for x in cands if _duration(ev, x) is not None and _duration(ev, x) <= SHORT_EVENT_DAYS]
    for eid in short[:5]:
        e = ev[ev["event_id"] == eid].iloc[0]
        s0, e0 = pd.Timestamp(e["start_date"]), pd.Timestamp(e["end_date"])
        if True:
            add("event", f"{e['name']} ({s0.date()} to {e0.date()}, {e['region']}) delayed deliveries", primary,
                event_id=eid)
    for extra in scopes[1:]:
        add("measure", f"Compare {extra['label']} with "
            + ("states where promised times are similar (similar distance)" if extra.get("compare_with")
               else "all other orders"), extra)
        if extra.get("compare_with"):
            add("stage", "Transit is slower than in states at a similar distance", extra, stage="transit")
    return {"scopes": scopes, "primary": primary, "tasks": tasks, "named_events": short_named,
            "context_events": [x for x in named if x not in [t["params"].get("event_id") for t in tasks]]}


def _duration(ev: pd.DataFrame, eid: str) -> int | None:
    e = ev[ev["event_id"] == eid]
    if e.empty:
        return None
    e = e.iloc[0]
    if not pd.notna(e["end_date"]) or str(e["end_date"]) in ("", "None", "nan", "NaT"):
        return None
    return int((pd.Timestamp(e["end_date"]) - pd.Timestamp(e["start_date"])).days)


# ---------------------------------------------------------------------- running a test
def _run(engine: Engine, tool: str, spec: dict, **params) -> Result:
    _MEMO = engine.memo
    key = (tool, json.dumps(spec, sort_keys=True, default=str), json.dumps(params, sort_keys=True))
    if key not in _MEMO:
        s = build_scope(engine, spec)
        if tool == "measure":
            _MEMO[key] = engine.measure(s)
        elif tool == "stage":
            _MEMO[key] = engine.stages(s)
        elif tool == "segments":
            _MEMO[key] = engine.segments(s, params["dim"])
        elif tool == "volume":
            _MEMO[key] = engine.volume(s)
        elif tool == "data_quality":
            _MEMO[key] = engine.data_quality(s)
        elif tool == "text":
            _MEMO[key] = engine.text(s)
        elif tool == "event":
            _MEMO[key] = engine.event(params["event_id"], s)
        elif tool == "stock_wait":
            _MEMO[key] = engine.stock_wait(s)
        elif tool == "promise_buffer":
            _MEMO[key] = engine.promise_buffer(s, params.get("dim"))
        else:
            raise ValueError(f"unknown tool {tool}")
    return _MEMO[key]


def _row(res: Result, first_col_value: str) -> int:
    return next(i for i, r in enumerate(res.rows) if str(r[0]) == first_col_value)


def _ref(eid: str, res: Result, row: int, col: str) -> dict:
    return {"value": res.rows[row][res.columns.index(col)], "evidence": eid, "column": col, "row": row}


def run_task(engine: Engine, task: dict, eid_prefix: str) -> tuple[dict, dict]:
    """Run one battery task. Returns the finding (with its coded verdict) and the evidence it cites."""
    tool, spec, params = task["tool"], task["scope"], task.get("params", {})
    res = _run(engine, "stage" if tool == "stage" else tool, spec, **params)
    m_res = _run(engine, "measure", spec)
    eid, mid = f"{eid_prefix}.A1", f"{eid_prefix}.A2"
    evidence = {eid: res.evidence(eid, eid_prefix, "Analysis engine")}
    cite, keys = [eid], []
    fi, ri = _row(m_res, "focus"), _row(m_res, "reference")
    focus_rate, ref_rate = m_res.rows[fi][3], m_res.rows[ri][3]
    rise = round(focus_rate - ref_rate, 1) if focus_rate is not None and ref_rate is not None else None
    if tool != "measure":
        evidence[mid] = m_res.evidence(mid, eid_prefix, "Analysis engine")
        cite.append(mid)
        keys += [_ref(mid, m_res, fi, "late_pct"), _ref(mid, m_res, ri, "late_pct")]
    status, is_cause, share, kind = "rejected", False, None, "hypothesis"

    if tool == "measure":
        kind = "measure"
        s = res.summary
        di = _row(res, "difference (focus - reference)")
        keys += [_ref(eid, res, fi, "late_pct"), _ref(eid, res, ri, "late_pct"), _ref(eid, res, fi, "orders"),
                 _ref(eid, res, fi, "late_orders"), _ref(eid, res, di, "late_pct"), _ref(eid, res, di, "ci_low_pct"),
                 _ref(eid, res, di, "ci_high_pct")]
        claim = (f"{s['focus_late']:,} of {s['focus_orders']:,} delivered {spec['label']} were late ({focus_rate}%), against "
                 f"{ref_rate}% for {s['reference']}: a difference of {res.rows[di][3]} points (confidence interval "
                 f"{res.rows[di][4]} to {res.rows[di][5]}).")
        verdict, status, reason = "measured", "confirmed", "Measured directly by the analysis engine."
    elif tool == "stage":
        st = params["stage"]
        i = _row(res, st)
        r = res.rows[i]
        sh = r[res.columns.index("share_of_rise_pct")]
        share = (sh / 100) if sh is not None and sh == sh else None
        keys += [_ref(eid, res, i, c) for c in ("focus_median_days", "ref_median_days", "focus_p80_days",
                                                 "ref_p80_days", "share_of_rise_pct")]
        then = ("at baseline" if spec["mode"] == "time" else
                "in places at a similar distance" if spec.get("compare_with") else "for the other orders")
        claim = (f"{r[1].capitalize()}: typically {r[2]} days against {r[3]} {then}; four in five orders within {r[4]} days "
                 f"against {r[5]}. " + (f"Had this stage stayed as it was {then}, {sh}% of the {rise}-point rise "
                                       f"(from {ref_rate}% to {focus_rate}% late) would not have happened."
                                       if sh is not None and sh > 0 else
                                       f"This stage moved the other way (share of the {rise}-point rise, from "
                                       f"{ref_rate}% to {focus_rate}% late: {sh}%)."))
        if rise is None or rise <= 0.5:
            verdict, kind = "inconclusive", "hypothesis"
            claim = (f"{r[1].capitalize()}: typically {r[2]} days against {r[3]}; four in five orders within {r[4]} days against "
                     f"{r[5]}. There is no rise to explain in this scope ({focus_rate}% vs {ref_rate}% late).")
            keys = [k for k in keys if k["column"] != "share_of_rise_pct"]
            share, reason = None, "Not applicable: the late rate did not rise in this scope."
        elif share is not None and share >= CAUSE_SHARE and "stage_moved" in res.columns \
                and not r[res.columns.index("stage_moved")]:
            verdict = "refutes"
            reason = (f"The stage itself barely changed (typically {r[2]} vs {r[3]} days, four in five within {r[4]} "
                      f"vs {r[5]}), so its {sh}% share of the rise is noise in the counterfactual, not a cause.")
        elif share is not None and share >= CAUSE_SHARE:
            verdict, status, is_cause = "supports", "confirmed", True
            reason = f"Explains {sh}% of the rise (cause threshold: {int(CAUSE_SHARE * 100)}%)."
        elif share is not None and share < 0:
            verdict = "refutes"
            reason = f"Went the other way (share of the rise: {sh}%)."
        else:
            verdict = "refutes"
            reason = f"Explains only {sh}% of the rise (cause threshold: {int(CAUSE_SHARE * 100)}%)."
    elif tool == "segments":
        s = res.summary
        conc = s["concentrated_in"]
        idx = [i for i, r in enumerate(res.rows) if not str(r[0]).startswith("(")][:3]   # not the summary row
        top = [res.rows[i] for i in idx]
        label = engine.m.dims.get(s["dimension"], s["dimension"])
        keys += [_ref(eid, res, i, "share_of_rise_pct") for i in idx]
        keys += [_ref(eid, res, i, c) for i in idx for c in ("share_of_focus_orders_pct", "focus_late_pct", "ref_late_pct")]
        if len(idx) <= 1:
            keys = keys[:2]
            claim = (f"All of these orders share one {label}" + (f" ({top[0][0]})" if top else "")
                     + ": there is nothing to compare within it.")
        else:
            parts = "; ".join(f"{r[0]}: {r[7]}% of the rise from {r[8]}% of the orders ({r[4]}% -> {r[3]}% late)"
                              for r in top)
            claim = f"Where the rise sits, by {label}: {parts}."
        mix = s.get("mix_share_pct")
        keys.append(_ref(eid, res, len(res.rows) - 1, "share_of_rise_pct"))
        if mix is not None and mix >= 25:
            share, is_cause, verdict, status = mix / 100, True, "supports", "confirmed"
            reason = f"A change in where orders came from (the mix) explains {mix}% of the gap."
            claim += f" Change in the mix of orders by {label}: {mix}% of the gap."
        elif conc:
            verdict, status, kind = "measured", "confirmed", "measure"
            reason = f"Concentrated in {', '.join(conc)} (share of the rise at least 1.5x share of orders). This says where, not why."
        else:
            verdict, status, kind = "measured", "confirmed", "measure"
            reason = "Spread broadly: no segment's share of the rise is well above its share of orders."
    elif tool == "volume":
        s = res.summary
        keys += [_ref(eid, res, 0, "orders_per_day"), _ref(eid, res, 1, "orders_per_day"),
                 _ref(eid, res, 0, "mean_handover_days"), _ref(eid, res, 1, "mean_handover_days"),
                 _ref(eid, res, 0, "mean_transit_days"), _ref(eid, res, 1, "mean_transit_days")]
        keys.append(_ref(eid, res, 2, "orders_per_day"))
        claim = (f"{res.rows[0][2]} orders per day vs {res.rows[1][2]} in the four weeks before ({res.rows[2][2]}x); mean hand-over "
                 f"{res.rows[0][3]} vs {res.rows[1][3]} days, mean transit {res.rows[0][4]} vs {res.rows[1][4]} days.")
        verdict = res.verdict if res.verdict != "measured" else "inconclusive"
        if res.verdict == "supports":
            status, is_cause, reason = "confirmed", True, "Volume rose by 25%+ and the chain slowed with it."
        else:
            reason = ("Volume did not rise enough, or the chain did not slow with it.")
            status = "rejected" if res.verdict == "refutes" else "needs_more"
    elif tool == "stock_wait":
        s = res.summary
        rows = res.rows
        keys += [_ref(eid, res, k, c) for k in (1, 2, 3, 4) for c in ("focus", "reference")
                 if rows[k][res.columns.index(c)] is not None]
        keys += [_ref(eid, res, 5, "focus")]
        held = [(i, r) for i, r in enumerate(rows) if str(r[0]).startswith("orders held for ")]
        keys += [_ref(eid, res, i, "focus") for i, _ in held]
        sups = []
        for (i, r), (sid, name, cat, n, ps, code) in zip(held, s["suppliers"]):
            txt = f"{sid} ({name}, mostly {cat}): {n} orders"
            if ps >= 0.2 and code:
                keys.append(_ref(eid, res, i + 1, "focus"))
                txt += f", with {rows[i + 1][1]}% of its units in the period ordered on promotion {code}"
            sups.append(txt)
        ref_name = "baseline" if spec["mode"] == "time" else "reference"
        claim = (f"{rows[1][1]}% of the delivered orders waited for stock (handed to the carrier after their requested "
                 f"ship date while the warehouse held too little of one of their SKUs), against {rows[1][2]}% in the "
                 f"{ref_name}. They took {rows[2][1]} days to hand over, against {rows[3][1]} for the other orders. "
                 f"Without the waits, hand-over would have averaged {rows[5][1]} days, against {rows[4][1]} actual and "
                 f"{rows[4][2]} in the {ref_name}.")
        if sups:
            claim += " Held for SKUs from " + "; ".join(sups) + "."
        sh = s["share"]   # a share of the hand-over slowdown, not of the late-rate rise: kept in the claim only
        if res.verdict == "supports":
            verdict, status, kind = "supports", "confirmed", "measure"
            reason = ("Waiting for stock accounts for " + ("all" if sh >= 1 else f"{round(100 * sh, 1)}%")
                      + " of the hand-over slowdown (threshold: 25%). It explains the dispatch stage; it is not "
                      "counted as a separate cause.")
        elif res.verdict == "refutes":
            verdict = "refutes"
            reason = "Hand-over slowed, but waiting for stock accounts for less than 25% of it."
        else:
            verdict, kind = "inconclusive", "hypothesis"
            reason = (f"Only {s['waited']} orders waited for stock: too few to judge." if s["waited"] < 5
                      and (s.get("slowdown_days") or 0) > 0 else "Hand-over did not slow in this scope.")
    elif tool == "data_quality":
        s = res.summary
        keys += [_ref(eid, res, k, "result") for k in range(len(res.rows))]
        claim = (f"{res.rows[2][1]} delivered orders have a missing date and {res.rows[3][1]} an impossible date order, "
                 f"out of {res.rows[0][1]:,} in scope; {res.rows[1][1]} not-delivered orders are left out. Late is "
                 f"{res.rows[5][1]}% comparing calendar dates and {res.rows[6][1]}% comparing raw timestamps "
                 f"(WhyChain uses calendar dates: the promise has no time of day).")
        verdict = res.verdict
        status = "confirmed" if res.verdict == "supports" else "rejected"
        is_cause = res.verdict == "supports"
        reason = ("Anomalies exceed 2% of delivered orders." if is_cause else
                  f"Anomalies are {s['anomaly_share_pct']}% of orders: too few to explain the rise.")
    elif tool == "text":
        kind = "measure"
        keys += [_ref(eid, res, k, c) for k in range(len(res.rows)) for c in ("focus_per_1000", "ref_per_1000")]
        claim = "Review mentions per thousand orders (focus vs reference): " + "; ".join(
            f"{r[0]} {r[2]} vs {r[4]}" for r in res.rows) + "."
        verdict, status, reason = "measured", "confirmed", "Counted directly from review text."
    elif tool == "event":
        s = res.summary
        claim = (f"{s['name']} ({s['start']} to {s['end']}): carrier transit was {s['pre_event_transit_days']} days "
                 f"just before it, against a normal {s['normal_transit_days']}; with the event, transit moved "
                 f"{s['transit_jump_vs_trend_days']} days and the late rate {s['late_jump_vs_trend_pp']} points against "
                 f"the prior trend.")
        if s.get("region_aligned") is not None:
            claim += f" Larger in {s['region']} than elsewhere: {'yes' if s['region_aligned'] else 'no'}."
        verdict = {"supports": "supports", "partial": "supports", "refutes": "refutes"}[res.verdict]
        if res.verdict == "supports":
            status, is_cause, reason = "confirmed", True, "The effect starts with the event and wasn't there before."
        elif res.verdict == "partial":
            status, is_cause, reason = "confirmed", True, ("It added to a problem that was already there before it "
                                                          "began (a contributor, not the trigger).")
        else:
            reason = ("The effect was already there before the event." if s["elevated_before"] and not s["effect_with_event"]
                      else "No jump against the prior trend when the event began."
                      if not s["effect_with_event"] else f"Not larger in {s['region']} than elsewhere.")
        keys += [_ref(eid, res, k, c) for k in range(3) for c in ("mean_transit_days", "late_pct")]
    else:
        raise ValueError(tool)
    return ({"statement": task["statement"], "type": kind, "verdict": verdict, "claim": claim, "key_numbers": keys,
             "evidence_ids": cite, "fixed": {"status": status, "is_cause": is_cause, "share_of_symptom": share,
                                             "reason": reason}}, evidence)
