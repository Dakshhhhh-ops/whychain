"""Agent nodes. Each one reads the shared state, does its job, and returns its part of the state."""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from contextlib import contextmanager
from datetime import date, timedelta

from whychain.agents import prompts as P
from whychain.agents.executor import Ctx, fill, run_loop, verify_quotes
from whychain.harness.base import catalog_text
from whychain.analysis import get_engine
from whychain.analysis.battery import battery_applies, plan_battery, run_task, build_scope
from whychain.analysis.supply import SUPPLY_TOOLS, get_supply_engine, plan_supply, run_supply_task, supply_kind
from whychain.grounding import check_numbers, figure_supported, numbers_in
from whychain.llm import LLMError, extract_json

PROFILE_COL = re.compile(r"(status|_type|channel|tier|category|region|level|warehouse|port_of_entry|service|"
                         r"carrier_id|supplier_id|promo_code|lane_id|dest_city)$", re.I)


SCOUT_VERSION = "2"  # 2: partial joins on small lookup tables are kept

# ============================================================ helpers
def catalog_fingerprint(ctx: Ctx) -> str:
    cols = sorted(f"{t.fqn}:{','.join(c.name for c in t.columns)}" for t in ctx.harness.catalog())
    # bump SCOUT_VERSION when the Scout's logic changes, so maps made the old way are rebuilt
    return hashlib.sha1((SCOUT_VERSION + "|" + "|".join(cols)).encode()).hexdigest()[:12]


def data_map_text(dm: dict) -> str:
    if not dm:
        return "(no data map)"
    lines = ["Sources:"]
    for s in dm.get("sources", []):
        lines.append(f"- {s.get('source')}: {s.get('what_it_holds')} (grain: {s.get('grain')})")
    lines.append("Verified joins (match rate):")
    for j in dm.get("joins", []):
        part = ", partial: some keys have no match" if j.get("partial") else ""
        lines.append(f"- {j['left']} = {j['right']} ({j.get('match_rate', '?')}{part}): {j.get('meaning', '')}")
    if dm.get("date_columns"):
        lines.append("Date columns: " + "; ".join(f"{d['column']} = {d.get('meaning', '')}"
                                                  for d in dm["date_columns"][:20]))
    if dm.get("status_values"):
        lines.append("Value sets: " + "; ".join(f"{s['column']} in {s.get('values')}"
                                                for s in dm["status_values"][:20]))
    for c in dm.get("caveats", [])[:8]:
        lines.append(f"Caveat: {c}")
    return "\n".join(lines)


def memories_text(mems: list[dict]) -> str:
    if not mems:
        return "(no relevant memories)"
    return "\n".join(f"- [{m.get('kind', '?')}, conf {m.get('confidence', '?')}"
                     f"{', from: ' + repr(m['source_question'][:90]) if m.get('source_question') else ''}] "
                     f"{m.get('content', '')[:300]}" for m in mems)


_STOP = set("why did do does the a an to for in of and by how much what is are was were so often around on at with "
            "from this that our we it be been has have had".split())


def question_words(q: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9%]+", (q or "").lower()) if w not in _STOP}


def question_similarity(a: str, b: str) -> float:
    wa, wb = question_words(a), question_words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def source_question(ctx: Ctx, run_id: str | None, cache: dict) -> str | None:
    """The question of the investigation a memory came from (looked up in that run's saved trace)."""
    if not run_id or not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", str(run_id)):
        return None
    if run_id not in cache:
        cache[run_id] = None
        for name in ("result.json", "trace.jsonl"):
            f = ctx.settings.runs_dir / run_id / name
            if f.exists():
                try:
                    first = f.read_text(encoding="utf-8") if name == "result.json" else f.open(encoding="utf-8").readline()
                    cache[run_id] = json.loads(first).get("question")
                except (json.JSONDecodeError, OSError):
                    pass
                if cache[run_id]:
                    break
    return cache[run_id]


SIMILAR_QUESTION = 0.4  # word overlap (Jaccard) needed to reuse another investigation's findings


def evidence_text(ev: dict, max_rows: int = 8) -> str:
    if ev["type"] == "analysis":
        rows = "\n".join("    " + " | ".join("" if v is None else str(v) for v in r) for r in ev["rows"][:max_rows + 4])
        return (f"[{ev['id']}] ANALYSIS (computed by code, verdict: {ev.get('verdict')}): {ev.get('title')}\n"
                f"    {' | '.join(ev['columns'])}\n{rows}")
    if ev["type"] == "doc":
        return f"[{ev['id']}] DOC {ev['doc_id']} ({ev.get('doc_type')}, {ev.get('sent_at') or ''}): \"{ev['quote']}\""
    if ev["type"] == "optimization":
        s = ev.get("summary", {})
        trucks = "; ".join(f"{t['lane']} {t['units']} units, {t['trucks']} truck(s), INR {t['cost_inr']:,.0f}"
                           for t in s.get("trucks", []))
        return (f"[{ev['id']}] OPTIMAL PLAN (Optiagent, verified, optimality proven with {ev.get('certified_with')}): "
                f"need {s.get('need_units')} units over {s.get('skus')} SKUs; trucks: {trucks or 'none'}; "
                f"air {s.get('air_units')} units INR {s.get('air_cost_inr')}; can't arrive in time: "
                f"{s.get('unfilled_units')} {s.get('unfilled_by_dc') or ''}; total INR {ev.get('objective_inr')}")
    rows = "\n".join("    " + " | ".join("" if v is None else str(v) for v in r) for r in ev["rows"][:max_rows])
    more = f"\n    ... {ev['row_count']} rows" if ev["row_count"] > max_rows else ""
    rep = "" if "reproduced" not in ev else (" [re-verified]" if ev["reproduced"] else " [FAILED re-verification]")
    return (f"[{ev['id']}]{rep} {ev.get('purpose', '')[:160]}\n    SQL: {ev['sql'][:400]}\n"
            f"    {' | '.join(ev['columns'])}\n{rows}{more}")


def findings_text(findings: list[dict], evidence: dict, verdicts: dict | None = None, rows: int = 8,
                  checks: dict | None = None) -> str:
    out = []
    for f in findings:
        v = (verdicts or {}).get(f["task_id"])
        vtxt = f"\n  Evaluator: {v['status']} ({v.get('reason', '')})" if v else ""
        c = (checks or {}).get(f["task_id"])
        ctxt = ""
        if c and c["checked"]:
            ctxt = (f"\n  NUMBERS CHECK: all {c['checked']} numbers in the claim were found in the cited rows"
                    if not c["missing"] else
                    f"\n  NUMBERS CHECK: {len(c['missing'])} of {c['checked']} numbers in the claim are NOT in the cited "
                    f"rows: {', '.join(c['missing'][:8])}")
        out.append(f"[{f['task_id']}] ({f['type']}, {f['agent_kind']}) {f['statement']}\n"
                   f"  Investigator verdict: {f['verdict']} (confidence {f.get('confidence')}): {f['claim']}"
                   f"{vtxt}{ctxt}")
        for eid in f["evidence_ids"]:
            if eid in evidence:
                out.append("  " + evidence_text(evidence[eid], rows).replace("\n", "\n  "))
        if f.get("quotes_rejected"):
            out.append(f"  ({f['quotes_rejected']} quote(s) failed verbatim verification and were discarded)")
    return "\n".join(out)


def base_context(state: dict, ctx: Ctx) -> str:
    return (f"QUESTION: {state['question']}\nTODAY: {ctx.as_of} (\"next week\" = the 7 days after today)\n\n"
            f"DATA MAP (from the Data Scout):\n{data_map_text(state.get('data_map', {}))}\n\n"
            f"RELEVANT MEMORY (Meterless H-MEM: earlier investigations of similar questions. These are HINTS, not\n"
            f"evidence: never cite them, and never rule anything out because of them; re-check with a query):\n"
            f"{memories_text(state.get('memories', []))}")


# ============================================================ nodes
def recall(state: dict, ctx: Ctx) -> dict:
    agent = "Memory"
    ctx.tracer.event("agent_start", agent, note="Recalling earlier findings and the cached data map")
    ds_tag = [ctx.dataset.key] if getattr(ctx.dataset, "key", None) else None
    raw = [m for m in ctx.memory.recall(state["question"], k=12, kinds=["finding", "fact", "lesson"],
                                        exclude_run_id=ctx.tracer.run_id, tags=ds_tag)
           if "data_map" not in (m.get("tags") or [])]
    # H-MEM's reference embedder is a hash mock, so recall can return findings from unrelated questions. Findings and
    # facts are reused only when they come from a similar question; general lessons are always kept.
    mems, dropped, qcache = [], 0, {}
    for m in raw:
        if m.get("kind") == "lesson":
            mems.append(m)
            continue
        src = source_question(ctx, m.get("run_id"), qcache)
        if src and question_similarity(src, state["question"]) >= SIMILAR_QUESTION:
            mems.append({**m, "source_question": src})
        else:
            dropped += 1
    mems = mems[:8]
    if dropped:
        ctx.tracer.event("memory_filtered", agent, kept=len(mems), dropped=dropped,
                         note="findings from unrelated or untraceable investigations were not reused")
    fp = catalog_fingerprint(ctx)
    dm = {}
    for m in ctx.memory.recall("data map of company sources and verified joins", k=5, tags=["data_map"]):
        try:
            cand = json.loads(m.get("content", ""))
            if cand.get("fingerprint") == fp:
                dm = cand
                break
        except (json.JSONDecodeError, AttributeError):
            continue
    cache = ctx.settings.runs_dir / f"_data_map_{fp}.json"
    if not dm and cache.exists():
        dm = json.loads(cache.read_text(encoding="utf-8"))
    ctx.tracer.event("agent_end", agent, memories=len(mems), data_map_cached=bool(dm),
                     recalled=[m.get("content", "")[:120] for m in mems])
    return {"memories": mems, "data_map": dm, "round": 0}


def scout(state: dict, ctx: Ctx) -> dict:
    agent = "Data Scout"
    if state.get("data_map"):
        ctx.tracer.event("agent_skip", agent, note="Data map recalled from memory; skipping discovery")
        return {}
    ctx.tracer.event("agent_start", agent, note="Discovering sources, profiling columns, testing joins")
    tables = ctx.harness.catalog()
    samples, profiles = [], []
    for t in tables:
        r = ctx.harness.query(f"SELECT * FROM {t.fqn} LIMIT 2", limit=2)
        if r.ok:
            rows = [[(str(v)[:60] if v is not None else None) for v in row] for row in r.rows]
            samples.append(f"{t.fqn}: {r.columns}\n  {rows}")
        for c in t.columns:
            if PROFILE_COL.search(c.name) and t.source != "docs":
                pr = ctx.harness.query(f"SELECT {c.name}, COUNT(*) AS n FROM {t.fqn} GROUP BY {c.name} "
                                       f"ORDER BY n DESC LIMIT 12", limit=12)
                if pr.ok:
                    vals = [row[0] for row in pr.rows]
                    profiles.append(f"{t.fqn}.{c.name}: {vals}{' ...' if len(vals) == 12 else ''}")
    ctx.tracer.event("profile", agent, tables=len(tables), profiled_columns=len(profiles))
    notes = getattr(ctx.dataset, "source_notes", None)
    user = (f"CATALOG:\n{catalog_text(tables, notes)}\n\nSAMPLE ROWS:\n" + "\n".join(samples) +
            "\n\nVALUE PROFILES:\n" + "\n".join(profiles))
    try:
        dm = ctx.llm.json("work", P.SCOUT, user, agent=agent, max_tokens=3000)
    except LLMError as e:
        ctx.tracer.event("agent_error", agent, error=str(e)[:300])
        dm = {"sources": [], "joins": [], "caveats": ["Scout failed; agents must infer joins from the catalog."]}
    known = {f"{t.fqn}.{c.name}" for t in tables for c in t.columns}
    verified = []
    for j in dm.get("joins", []):
        left, right = str(j.get("left", "")), str(j.get("right", ""))
        if left not in known or right not in known:
            ctx.tracer.event("join_rejected", agent, left=left, right=right, reason="column not in catalog")
            continue
        lt, lc = left.rsplit(".", 1)
        rt, rc = right.rsplit(".", 1)
        sql = (f"SELECT COUNT(*) AS keys_right, SUM(CASE WHEN l.k IS NOT NULL THEN 1 ELSE 0 END) AS matched "
               f"FROM (SELECT DISTINCT {rc} AS k FROM {rt}) r LEFT JOIN (SELECT DISTINCT {lc} AS k FROM {lt}) l "
               f"ON r.k = l.k")
        r = ctx.harness.query(sql, limit=1)
        rate, keys, matched = None, 0, 0
        if r.ok and r.rows and r.rows[0][0]:
            keys, matched = int(r.rows[0][0]), int(r.rows[0][1] or 0)
            rate = round(matched / keys, 2)
        # small lookup tables (a few ports, carriers, warehouses) legitimately match only partly
        partial = rate is not None and rate < 0.5 and keys <= 10 and matched >= 1
        ok = rate is not None and (rate >= 0.5 or partial)
        ctx.tracer.event("join_test", agent, left=left, right=right, match_rate=rate, kept=ok, partial=partial)
        if ok:
            verified.append({**j, "match_rate": rate, **({"partial": True} if partial else {})})
    dm["joins"] = verified
    dm["fingerprint"] = catalog_fingerprint(ctx)
    ctx.memory.write(ctx.tracer.run_id, agent, "fact", json.dumps(dm),
                     {"source": "zetaris-catalog", "harness": ctx.harness.name}, 0.9, ["data_map"])
    (ctx.settings.runs_dir / f"_data_map_{dm['fingerprint']}.json").write_text(json.dumps(dm, indent=1),
                                                                               encoding="utf-8")
    ctx.tracer.event("agent_end", agent, sources=len(dm.get("sources", [])), joins_verified=len(verified))
    return {"data_map": dm}


def engine_for(ctx: Ctx):
    """The analysis engine for this run's dataset, or None if it has no fact model."""
    try:
        return get_engine(ctx.harness)
    except Exception:
        return None


def plan_with_battery(state: dict, ctx: Ctx, eng) -> dict | None:
    """Round 1 for a late-delivery question: a fixed battery of tests run by code, plus one Document Analyst task
    for the written record (context, not a cause)."""
    agent = "Planner"
    try:
        as_of = date.fromisoformat(str(ctx.as_of)[:10])
        b = plan_battery(state["question"], eng, as_of)
    except Exception as e:
        ctx.tracer.event("agent_error", agent, error=f"analysis battery failed, using the model planner: {e}"[:300])
        return None
    new = []
    for i, t in enumerate(b["tasks"], 1):
        new.append({"id": f"T{i}", "round": 1, "type": "measure" if t["tool"] in ("measure", "text") else "hypothesis",
                    "statement": t["statement"], "agent": "tool", "tool": t["tool"], "scope": t["scope"],
                    "params": t["params"], "checks": [f"analysis engine: {t['tool']}"], "priority": 1,
                    "addresses_gap": None})
    new.append({"id": f"T{len(new) + 1}", "round": 1, "type": "hypothesis", "agent": "docs", "context_only": True,
                "statement": "What the written record (news briefs, contracts, email) says about the period, places "
                             "and events in question",
                "checks": ["search docs.documents and signals.events for the period and places in question"],
                "priority": 2, "addresses_gap": None})
    p = b["primary"]
    ctx.tracer.event("plan", agent, round=1, symptom=f"Late deliveries: {p['label']}",
                     reasoning=("A fixed battery of tests, computed by the analysis engine (no model): the symptom, "
                                "each delivery stage, where it is concentrated, volume, data quality, review text and "
                                "the dated events in the period. The Document Analyst reads the written record."),
                     tasks=[{"id": t["id"], "agent": t["agent"], "type": t["type"], "statement": t["statement"]}
                            for t in new])
    return {"tasks": new, "open_tasks": new, "round": 1, "symptom": f"Late deliveries: {p['label']}",
            "analysis": {"primary": p, "scopes": b["scopes"], "named_events": b.get("named_events", []),
                         "context_events": b["context_events"]}}


def plan_with_supply(state: dict, ctx: Ctx, seng) -> dict | None:
    """Round 1 for a stock, backlog or orders-at-risk question: the supply battery, run by code."""
    agent = "Planner"
    try:
        b = plan_supply(state["question"], seng)
    except Exception as e:
        ctx.tracer.event("agent_error", agent, error=f"supply battery failed, using the model planner: {e}"[:300])
        return None
    new = [{"id": f"T{i}", "round": 1, "type": "measure" if t["tool"] in ("supply_measure", "shortage") else "hypothesis",
            "statement": t["statement"], "agent": "tool", "tool": t["tool"], "scope": t["scope"], "params": t["params"],
            "checks": [f"analysis engine: {t['tool']}"], "priority": 1, "addresses_gap": None}
           for i, t in enumerate(b["tasks"], 1)]
    new.append({"id": f"T{len(new) + 1}", "round": 1, "type": "hypothesis", "agent": "docs", "context_only": True,
                "statement": "What the written record (contracts, policies, email) says about the shortage",
                "checks": ["search docs.documents for the suppliers, SKUs, places and dates in question"],
                "priority": 2, "addresses_gap": None})
    spec = b["spec"]
    symptom = (f"Shortage: {spec['kind'].replace('_', ' ')}, {spec['label']}"
               + (f", {', '.join(spec['warehouses'])}" if spec["warehouses"] else "")
               + (f", {', '.join(spec['categories'])}" if spec["categories"] else ""))
    ctx.tracer.event("plan", agent, round=1, symptom=symptom,
                     reasoning=("A fixed battery of supply checks, computed by the analysis engine (no model): the "
                                "shortage, where it sits, late inbound orders and ports, demand, reorder policy and "
                                "duplicate orders. The Document Analyst reads the written record."),
                     tasks=[{"id": t["id"], "agent": t["agent"], "type": t["type"], "statement": t["statement"]}
                            for t in new])
    return {"tasks": new, "open_tasks": new, "round": 1, "symptom": symptom,
            "analysis": {"kind": "supply", "spec": spec, "named_events": []}}


@contextmanager
def _muted(ctx: Ctx):
    """Run code-only steps a second time without repeating their events in the trace."""
    orig = ctx.tracer.event
    ctx.tracer.event = lambda *a, **k: None
    try:
        yield
    finally:
        ctx.tracer.event = orig


def preliminary(state: dict, out: dict, ctx: Ctx) -> None:
    """A preliminary answer in seconds. The computed tests take seconds; the document reading and the model-written
    answer take minutes. So run the tests now (they are kept: the investigate step doesn't run them again), size the
    actions and write the summary in code, and publish it while the rest of the run continues. The final brief is
    built the same way, plus the written record and the short written answer."""
    t0 = time.time()
    findings, evidence, verdicts = [], {}, {}
    for t in out["tasks"]:
        if t.get("agent") != "tool":
            continue
        res = investigate_tool(t, ctx)
        ctx.precomputed[t["id"]] = res
        for f in res["findings"]:
            findings.append(f)
            verdicts[f["task_id"]] = {**f["fixed"], "round": 1}
        evidence.update(res["evidence"])
    pseudo = {**state, **out, "findings": findings, "evidence": evidence, "verdicts": verdicts, "actions": [],
              "evaluations": []}
    try:
        with _muted(ctx):
            if any(v.get("status") == "confirmed" and v.get("is_cause") for v in verdicts.values()):
                acts = act_supply(pseudo, ctx) if out["analysis"].get("kind") == "supply" else act_battery(pseudo, ctx)
                pseudo["actions"] = acts.get("actions", [])
                pseudo["evidence"] = {**evidence, **acts.get("evidence", {})}
            pre = report_battery(pseudo, ctx, preliminary=True)
        ctx.tracer.event("preliminary", "Reporter", headline=pre["headline"], summary=pre["summary"],
                         seconds=round(time.time() - t0, 1), actions=len(pseudo["actions"]),
                         unverified=pre["numbers_check"]["unverified"])
    except Exception as e:   # the full run goes on regardless
        ctx.tracer.event("agent_error", "Reporter", error=f"preliminary answer failed: {e}"[:300])


def plan(state: dict, ctx: Ctx) -> dict:
    agent = "Planner"
    rnd = state.get("round", 0) + 1
    ctx.tracer.event("agent_start", agent, round=rnd)
    route = ctx.route   # chosen at the start of the run (Meterless Scout Intent, or the word rules)
    if rnd == 1 and ctx.fixed_analyses and (route == "supply" if route else supply_kind(state["question"])):
        seng = get_supply_engine(ctx.harness)
        if seng is not None:
            out = plan_with_supply(state, ctx, seng)
            if out is not None:
                preliminary(state, out, ctx)
                return out
    if rnd == 1 and ctx.fixed_analyses and (route == "battery" if route else battery_applies(state["question"])):
        eng = engine_for(ctx)
        if eng is not None:
            out = plan_with_battery(state, ctx, eng)
            if out is not None:
                preliminary(state, out, ctx)
                return out
    existing = state.get("tasks", [])
    if rnd == 1:
        user = base_context(state, ctx) + "\n\nCreate the investigation plan (1 measure task + 3-5 hypotheses)."
        out = ctx.llm.json("reason", P.PLANNER, user, agent=agent)
        raw, cap = out.get("tasks", []), 6
    else:
        ev = state["evaluations"][-1]
        settled = "\n".join(f"- [{t['id']}] {t['statement']} -> {state['verdicts'].get(t['id'], {}).get('status')}"
                            for t in existing)
        gaps = "\n".join(f"- {g.get('what')} (why: {g.get('why')})" for g in ev.get("gaps", []))
        user = (base_context(state, ctx) + f"\n\nTASKS SO FAR:\n{settled}\n\nEVALUATOR SUMMARY: {ev.get('summary')}"
                f"\nRED FLAGS: {ev.get('red_flags')}\nGAPS TO CLOSE:\n{gaps}")
        out = ctx.llm.json("reason", P.PLANNER_REVISE, user, agent=agent)
        raw, cap = out.get("tasks", []), 4
    new = _parse_tasks(raw, existing, rnd, cap)
    if not new:  # a plan with no usable tasks would waste a whole round: ask again, once, saying what's wrong
        ctx.tracer.event("plan_repair", agent, round=rnd, keys=sorted(out.keys()))
        retry_user = (user + "\n\nYour previous reply had no usable tasks. Reply with a JSON object whose \"tasks\" list "
                      "has objects with \"statement\", \"type\", \"agent\" (\"data\" or \"docs\") and \"checks\".")
        out = ctx.llm.json("reason", P.PLANNER if rnd == 1 else P.PLANNER_REVISE, retry_user, agent=agent)
        new = _parse_tasks(out.get("tasks", []), existing, rnd, cap)
    if not new:  # still nothing usable: fall back to measuring the symptom, so the round isn't wasted
        ctx.tracer.event("plan_fallback", agent, round=rnd)
        new = [{"id": f"T{len(existing) + 1}", "round": rnd, "type": "measure",
                "statement": "Measure the symptom precisely against a normal baseline and find where it is concentrated",
                "agent": "data", "checks": ["series over a longer window", "breakdown by segment"], "priority": 1,
                "addresses_gap": None}]
    if new and not any(t["agent"] == "docs" for t in existing + new):
        # the reason behind a pattern is often only written down: always have the documents checked once
        new.append({"id": f"T{len(existing) + len(new) + 1}", "round": rnd, "type": "hypothesis",
                    "statement": "Contracts, policies or email record a change or event that explains this symptom",
                    "agent": "docs", "checks": ["search docs.documents for the entities and dates in the symptom"],
                    "priority": 2, "addresses_gap": None})
    ctx.tracer.event("plan", agent, round=rnd, reasoning=out.get("reasoning"), symptom=out.get("symptom"),
                     tasks=[{"id": t["id"], "agent": t["agent"], "type": t["type"], "statement": t["statement"]}
                            for t in new])
    upd = {"tasks": new, "open_tasks": new, "round": rnd}
    if rnd == 1:
        upd["symptom"] = out.get("symptom", state["question"])
    return upd


TASK_TEXT_KEYS = ("statement", "hypothesis", "description", "task", "title", "check")


def _parse_tasks(raw, existing: list, rnd: int, cap: int) -> list[dict]:
    """Planner tasks, tolerant of near-miss shapes (a list of strings, or "hypothesis"/"description" keys)."""
    if isinstance(raw, dict):
        raw = raw.get("items") or list(raw.values())
    new: list[dict] = []
    for t in (raw or [])[:cap]:
        if isinstance(t, str):
            t = {"statement": t}
        if not isinstance(t, dict):
            continue
        text = next((str(t[k]) for k in TASK_TEXT_KEYS if t.get(k)), "")
        if not text:
            continue
        tid = f"T{len(existing) + len(new) + 1}"
        agent_kind = "docs" if str(t.get("agent", "")).lower() in ("docs", "doc", "documents", "doc analyst") else "data"
        new.append({"id": tid, "round": rnd, "type": t.get("type", "hypothesis"), "statement": text,
                    "agent": agent_kind, "checks": t.get("checks", []),
                    "priority": t.get("priority", 2), "addresses_gap": t.get("addresses_gap")})
    return new


def investigate_tool(t: dict, ctx: Ctx) -> dict:
    """A battery task: computed by the analysis engine, with a coded verdict. No model call."""
    agent = f"Analysis engine {t['id']}"
    ctx.tracer.event("agent_start", agent, task_id=t["id"], statement=t["statement"])
    try:
        if t.get("tool") in SUPPLY_TOOLS:
            f, evidence = run_supply_task(get_supply_engine(ctx.harness), t, t["id"])
        else:
            f, evidence = run_task(engine_for(ctx), t, t["id"])
    except Exception as e:
        f = {"statement": t["statement"], "type": t["type"], "verdict": "inconclusive",
             "claim": f"The analysis could not be computed: {str(e)[:200]}", "key_numbers": [], "evidence_ids": [],
             "fixed": {"status": "needs_more", "is_cause": False, "share_of_symptom": None, "reason": "analysis error"}}
        evidence = {}
        ctx.tracer.event("agent_error", agent, task_id=t["id"], error=str(e)[:300])
    for eid, ev in evidence.items():
        ctx.tracer.event("analysis", agent, task_id=t["id"], evidence_id=eid, tool=ev["tool"], title=ev["title"],
                         verdict=ev["verdict"], rows=ev["row_count"])
    finding = {"task_id": t["id"], "statement": t["statement"], "type": f["type"], "agent_kind": "tool",
               "agent": agent, "verdict": f["verdict"], "claim": f["claim"], "key_numbers": f["key_numbers"],
               "who_knew": None, "confidence": 1.0, "evidence_ids": f["evidence_ids"], "steps": 1,
               "sql_errors_recovered": 0, "quotes_rejected": 0, "round": t["round"], "fixed": f["fixed"]}
    ctx.tracer.event("finding", agent, task_id=t["id"], verdict=finding["verdict"], claim=finding["claim"],
                     evidence=finding["evidence_ids"], confidence=1.0, recovered=0)
    return {"findings": [finding], "evidence": evidence}


SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
PRIVATE_DOC_PROMPT = (
    "You read ONE company document and decide whether it is relevant to a question about a business problem. "
    "Its sentences are numbered. Reply with one JSON object only, no other text: "
    '{"relevant": true or false, "sentences": [the numbers of at most 2 sentences that describe what happened, '
    "when or where]}. A document about a different period or a different topic is not relevant.")


def investigate_docs_private(t: dict, payload: dict, ctx: Ctx) -> dict:
    """The written record, read only on this machine (Meterless Nightshift): code fetches every document and numbers
    its sentences; the local model only says whether a document is relevant and which sentences matter; code copies
    those sentences word for word. Small local models can do that reliably, and the quotes are exact by construction."""
    agent = f"Doc Analyst {t['id']}"
    ctx.tracer.event("agent_start", agent, task_id=t["id"], statement=t["statement"], private=True)
    r = ctx.harness.query("SELECT doc_id, doc_type, title, sent_at, sender, recipients, body FROM docs.documents "
                          "ORDER BY sent_at", limit=60)
    docs = [dict(zip(r.columns, row)) for row in r.rows] if r.ok else []
    focus = f"QUESTION: {payload.get('question', '')}\nFOCUS: {payload.get('symptom') or ''}"
    # the period in question, from the analysis plan: dated documents far from it are not read at all, documents
    # dated inside it are in scope by definition, and quotes nearest the period come first
    an = payload.get("analysis") or {}
    span = an.get("primary") or an.get("spec") or {}
    try:
        p0, p1 = date.fromisoformat(str(span["start"])[:10]), date.fromisoformat(str(span["end"])[:10])
    except (KeyError, TypeError, ValueError):
        p0 = p1 = None

    def gap_days(d: dict) -> int | None:
        try:
            when = date.fromisoformat(str(d.get("sent_at"))[:10])
        except (TypeError, ValueError):
            return None   # undated: contracts and policies apply throughout
        return 0 if p0 is None or p0 <= when <= p1 else (p0 - when).days if when < p0 else (when - p1).days

    docs = [d for d in docs if p0 is None or gap_days(d) is None or gap_days(d) <= 75]
    picked, relevant, failed = [], [], 0
    for d in docs:
        sents = [x.strip() for x in SENTENCE_END.split(str(d.get("body") or "")) if len(x.strip()) >= 12]
        if not sents:
            continue
        in_period = p0 is not None and gap_days(d) == 0
        numbered = "\n".join(f"{i}. {x}" for i, x in enumerate(sents, 1))
        user = f"{focus}\n\nDOCUMENT: {d.get('title')} ({d.get('sent_at') or 'undated'})\n{numbered}"
        try:
            out = ctx.llm.json("local", PRIVATE_DOC_PROMPT, user, agent=agent, max_tokens=150, retries=1)
        except LLMError as e:
            failed += 1
            ctx.tracer.event("agent_error", agent, task_id=t["id"], error=f"{d['doc_id']}: {e}"[:200])
            continue
        nums = []
        for n in out.get("sentences") or []:
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(sents) and n not in nums:
                nums.append(n)
        keep = (out.get("relevant") is True and nums) or in_period
        if in_period and not nums:
            nums = [1]   # dated inside the period: in scope even if the model passed it over; quote its lead
        ctx.tracer.event("read_doc", agent, task_id=t["id"], doc_id=d["doc_id"], found=True,
                         thought="read on this machine: " + ("in the period" if in_period else
                                                             "relevant" if keep else "not relevant"))
        if keep:
            relevant.append(d)
            picked += [{"doc_id": d["doc_id"], "quote": sents[n - 1]} for n in nums[:2]]
    # nearest the period first (undated contracts and policies after the dated record)
    rank = {d["doc_id"]: (gap_days(d) is None, gap_days(d) or 0) for d in docs}
    picked.sort(key=lambda q: rank[q["doc_id"]])
    relevant.sort(key=lambda d: rank[d["doc_id"]])
    qev, rejected = verify_quotes(ctx, t["id"], agent, picked[:8], {d["doc_id"]: d for d in docs})
    shown = [d for d in relevant if any(e["doc_id"] == d["doc_id"] for e in qev.values())]
    claim = ("The written record (read on this machine by Meterless Nightshift) reports: "
             + "; ".join(f"{d.get('title')} ({str(d.get('sent_at') or 'undated')[:10]})" for d in shown[:5]) + "."
             if shown else f"No document in the written record ({len(docs)} read on this machine) bears on the question."
             + (f" {failed} could not be read by the local model." if failed else ""))
    finding = {"task_id": t["id"], "statement": t["statement"], "type": t["type"], "agent_kind": "docs",
               "agent": agent, "verdict": "supports" if qev else "inconclusive", "claim": claim, "key_numbers": {},
               "who_knew": None, "confidence": 0.6 if qev else 0.0, "evidence_ids": list(qev), "steps": len(docs),
               "sql_errors_recovered": 0, "quotes_rejected": rejected, "round": t["round"],
               "context_only": bool(t.get("context_only")), "private": True}
    ctx.tracer.event("finding", agent, task_id=t["id"], verdict=finding["verdict"], claim=claim,
                     evidence=finding["evidence_ids"], confidence=finding["confidence"], recovered=0)
    return {"findings": [finding], "evidence": qev}


def investigate(payload: dict, ctx: Ctx) -> dict:
    """Runs one task. Fanned out in parallel, one call per task."""
    t = payload["task"]
    if t.get("agent") == "tool":
        if t.get("round") == 1 and t["id"] in ctx.precomputed:
            return ctx.precomputed.pop(t["id"])
        return investigate_tool(t, ctx)
    docs = t["agent"] == "docs"
    if docs and getattr(ctx.llm, "private_docs", False):
        return investigate_docs_private(t, payload, ctx)
    agent = f"{'Doc Analyst' if docs else 'Investigator'} {t['id']}"
    ctx.tracer.event("agent_start", agent, task_id=t["id"], statement=t["statement"])
    brief = (base_context(payload, ctx) + f"\n\nYOUR TASK [{t['id']}] ({t['type']}): {t['statement']}\n"
             f"Checks to make: {t.get('checks')}\n\nCATALOG:\n{ctx.catalog_text}")
    if t.get("context_only"):
        brief += ("\n\nThis is a context task. Your claim must say what the documents report (who, what, where, "
                  "when), not whether it explains or caused the symptom: computed tests decide causes, and an event "
                  "in the documents can turn out to be a red herring.")
    system = fill(P.DOC_ANALYST if docs else P.INVESTIGATOR, 5 if docs else 6)
    res = run_loop(ctx, agent=agent, role="local" if docs else "work", system=system, brief=brief,
                   task_id=t["id"], max_steps=5 if docs else 6, allow_docs=docs,
                   finish_keys=("verdict", "claim"))
    f = res.final
    evidence = dict(res.evidence)
    rejected = 0
    ev_ids = list(f.get("evidence", []))
    if docs:
        qev, rejected = verify_quotes(ctx, t["id"], agent, f.get("quotes"), res.docs_read)
        evidence.update(qev)
        ev_ids = list(qev) + ev_ids
    if not ev_ids and evidence:  # model forgot to cite: keep its last successful query
        ev_ids = [list(evidence)[-1]]
    finding = {"task_id": t["id"], "statement": t["statement"], "type": t["type"],
               "agent_kind": "docs" if docs else "data", "agent": agent,
               "verdict": str(f.get("verdict", "inconclusive")), "claim": str(f.get("claim", ""))[:800],
               "key_numbers": f.get("key_numbers", {}), "who_knew": f.get("who_knew"),
               "confidence": f.get("confidence"), "evidence_ids": ev_ids, "steps": res.steps,
               "sql_errors_recovered": res.sql_errors_recovered, "quotes_rejected": rejected,
               "round": t["round"], "context_only": bool(t.get("context_only"))}
    ctx.tracer.event("finding", agent, task_id=t["id"], verdict=finding["verdict"], claim=finding["claim"],
                     evidence=ev_ids, confidence=finding["confidence"], recovered=res.sql_errors_recovered)
    return {"findings": [finding], "evidence": evidence}


def evaluate(state: dict, ctx: Ctx) -> dict:
    agent = "Evaluator"
    rnd = state["round"]
    ctx.tracer.event("agent_start", agent, round=rnd)
    evidence = dict(state.get("evidence", {}))
    this_round = [f for f in state["findings"] if f["round"] == rnd]
    # 1) deterministic re-verification: re-run every SQL result the findings cite
    reverified = {}
    for f in this_round:
        for eid in f["evidence_ids"]:
            ev = evidence.get(eid)
            if ev and ev["type"] == "sql" and "reproduced" not in ev:
                r = ctx.harness.query(ev["sql"], limit=50)
                ok = r.ok and r.row_count == ev["row_count"] and r.rows[:3] == ev["rows"][:3]
                reverified[eid] = {**ev, "reproduced": ok}
                ctx.tracer.event("reverify", agent, evidence_id=eid, reproduced=ok)
    evidence.update(reverified)
    # 2) deterministic claim check: the numbers a data finding states must appear in the rows it cites
    checks = {}
    for f in this_round:
        if f.get("agent_kind") in ("data", "docs"):
            checks[f["task_id"]] = c = check_numbers(f["claim"], finding_evidence(f, evidence), f.get("key_numbers"),
                                                     fetch=refetch(ctx))
            if c["checked"]:
                ctx.tracer.event("numbers_check", agent, task_id=f["task_id"], checked=c["checked"], missing=c["missing"])
    verdicts: dict = {}
    # 3) skeptical review: the model judges only what code didn't decide
    earlier = [f for f in state["findings"] if f["round"] < rnd]
    coded = [f for f in this_round if f.get("fixed")]
    to_judge = [f for f in this_round if not f.get("fixed")]
    coded_txt = "\n".join(f"- [{f['task_id']}] {f['statement']} -> {f['fixed']['status']}"
                           f"{' (cause, ' + str(round(100 * f['fixed']['share_of_symptom'], 1)) + '% of the rise)' if f['fixed'].get('is_cause') and isinstance(f['fixed'].get('share_of_symptom'), float) else ''}"
                           f": {f['fixed']['reason']}" for f in coded)
    if state.get("analysis"):  # battery run: the record is context; its quotes were verified word for word
        for f in to_judge:
            nq = sum(1 for e in f["evidence_ids"] if evidence.get(e, {}).get("type") == "doc")
            verdicts[f["task_id"]] = {"status": "confirmed" if nq else "needs_more", "is_cause": False,
                                      "share_of_symptom": None, "round": rnd, "numbers_check": checks.get(f["task_id"]),
                                      "reason": (f"Context only: {nq} quote(s) verified word for word. Dated events are "
                                                 "judged by the event tests, not by documents.") if nq else
                                                "Context only: no verified quote."}
        to_judge_llm = []
    else:
        to_judge_llm = to_judge
    if to_judge_llm:
        user = (base_context(state, ctx) + f"\n\nSYMPTOM: {state.get('symptom')}\n\n"
                + (f"DECIDED BY CODE (authoritative; don't re-judge these, but use them):\n{coded_txt}\n\n" if coded else "")
                + f"EARLIER FINDINGS (already judged):\n{findings_text(earlier, evidence, state.get('verdicts'), 4) or '(none)'}"
                f"\n\nNEW FINDINGS TO JUDGE (round {rnd}):\n{findings_text(to_judge_llm, evidence, checks=checks)}")
        try:
            out = ctx.llm.json("reason", P.EVALUATOR, user, agent=agent, max_tokens=3000)
        except LLMError as e:
            ctx.tracer.event("agent_error", agent, error=str(e)[:300])
            out = {"verdicts": [], "decision": "accept", "summary": f"Evaluator unavailable: {e}"}
    else:
        out = {"verdicts": [], "decision": "accept", "gaps": [], "red_flags": [], "summary": ""}
    valid_ids = {f["task_id"] for f in state["findings"]}
    for v in out.get("verdicts", []):
        if isinstance(v, dict) and v.get("task_id") in valid_ids:
            finding = next(f for f in state["findings"] if f["task_id"] == v["task_id"])
            status = v.get("status", "needs_more")
            try:
                share = float(v.get("share_of_symptom") or 0)
            except (TypeError, ValueError):
                share = 0.0
            # a confirmed mechanism that explains a material share of the symptom is a cause, even if it was planned as a measurement
            is_cause = (v.get("is_cause", True) and finding["type"] != "measure") or (status == "confirmed" and share >= 0.25)
            # "confirmed" on a refuted hypothesis means the refutation holds: the hypothesis is ruled out
            if status == "confirmed" and str(finding.get("verdict")).lower() == "refutes":
                status, is_cause = "rejected", False
            reason = v.get("reason", "")
            c = checks.get(v["task_id"])
            # a claim with more than a third of its numbers unsupported by its own evidence can't be confirmed
            if status == "confirmed" and c and c["checked"] >= 2 and len(c["missing"]) * 3 > c["checked"]:
                status, is_cause = "needs_more", False
                reason += (f" [Numbers check: {len(c['missing'])} of {c['checked']} numbers in the claim are not in "
                           f"the cited rows ({', '.join(c['missing'][:5])}), so it cannot be confirmed.]")
            verdicts[v["task_id"]] = {"status": status, "reason": reason,
                                      "is_cause": bool(is_cause), "share_of_symptom": v.get("share_of_symptom"),
                                      "round": rnd, "numbers_check": c}
    for f in coded:  # the analysis engine's verdicts, with the same numbers rule as everything else
        fx = dict(f["fixed"])
        c = checks.get(f["task_id"])
        if fx["status"] == "confirmed" and c and c["checked"] >= 2 and len(c["missing"]) * 3 > c["checked"]:
            fx.update(status="needs_more", is_cause=False,
                      reason=fx["reason"] + f" [Numbers check failed: {', '.join(c['missing'][:5])}]")
        verdicts[f["task_id"]] = {**fx, "round": rnd, "numbers_check": c, "decided_by": "code"}
    for f in to_judge:  # the written record gives context; dated events are judged by the event test
        if f.get("context_only") and f["task_id"] in verdicts:
            verdicts[f["task_id"]]["is_cause"] = False
    if coded:
        stage_ids = {t["id"] for t in state.get("tasks", []) if t.get("tool") == "stage"}
        explained = sum(v["share_of_symptom"] for k, v in verdicts.items() if k in stage_ids and v.get("is_cause")
                        and isinstance(v.get("share_of_symptom"), float))
        out["symptom_explained"] = round(min(1.0, max(0.0, explained)), 2) if stage_ids else out.get("symptom_explained")
        causes_txt = ", ".join(f"{f['statement'].lower()} ({round(100 * verdicts[f['task_id']]['share_of_symptom'], 1)}%)"
                               if isinstance(verdicts[f["task_id"]].get("share_of_symptom"), float)
                               else f["statement"].lower()
                               for f in coded if verdicts[f["task_id"]].get("is_cause"))
        ruled = ", ".join(f["statement"].lower() for f in coded if verdicts[f["task_id"]]["status"] == "rejected")
        out["summary"] = ((f"Confirmed by the analysis engine: {causes_txt or 'no cause'}. Ruled out: {ruled or 'nothing'}. ")
                          + (out.get("summary") or ""))
        if not to_judge:
            out["decision"] = "accept"
    decision = out.get("decision", "accept")
    forced = False
    if decision == "revise" and (rnd >= ctx.settings.max_rounds or not out.get("gaps")):
        decision, forced = "accept", True
    out["decision"] = decision
    out["round"] = rnd
    out["forced_accept"] = forced
    causes = [(f, verdicts[f["task_id"]]) for f in this_round
              if verdicts.get(f["task_id"], {}).get("status") == "confirmed" and verdicts[f["task_id"]]["is_cause"]]
    if causes:
        symptom = {"type": "symptom", "name": str(state.get("symptom") or state["question"])[:160]}
        ents = sorted({e for f, _ in causes for e in entity_codes(f["claim"] + " " + f["statement"])})
        ctx.tracer.event("causes_confirmed", agent, round=rnd,
                         status="cause_confirmed",
                         entities=[entity(e) for e in ents],
                         relations=[{"from": {"type": "cause", "name": f["statement"][:160]}, "type": "causes",
                                     "to": symptom, "confidence": v.get("share_of_symptom") or f.get("confidence")}
                                    for f, v in causes]
                                   + [{"from": entity(e), "type": "implicated_in",
                                       "to": {"type": "cause", "name": f["statement"][:160]}}
                                      for f, _ in causes for e in entity_codes(f["claim"] + " " + f["statement"])],
                         facts=[{"predicate": f"root_cause_{f['task_id']}", "value": f["claim"][:300]}
                                for f, _ in causes])
    ctx.tracer.event("evaluation", agent, round=rnd, decision=decision, forced=forced,
                     explained=out.get("symptom_explained"), summary=out.get("summary"),
                     verdicts={k: v["status"] for k, v in verdicts.items()}, gaps=out.get("gaps"),
                     red_flags=out.get("red_flags"))
    return {"evaluations": [out], "verdicts": verdicts, "evidence": reverified}


def finding_evidence(f: dict, evidence: dict) -> list[dict]:
    """A finding's cited evidence plus any of its own results its key numbers point at."""
    ids = list(f.get("evidence_ids", []))
    for k in f.get("key_numbers") or [] if isinstance(f.get("key_numbers"), list) else []:
        if isinstance(k, dict) and k.get("evidence") in evidence and k["evidence"] not in ids:
            ids.append(k["evidence"])
    return [evidence[e] for e in ids if e in evidence]


_UNVERIFIED = " (unverified)"
RULE_CONSTANTS = [10.0, 25.0, 1.5, 2.0, 5.0, 80.0, 14.0]   # thresholds and windows named in coded verdicts and steps
_CITE = re.compile(r"\[([A-Z][A-Za-z0-9]*\.[QDPA]\d+)\]")


def check_brief(md: str, headline: str, state: dict, actions: list[dict], ctx: Ctx | None = None,
                answer_rules: list[float] | None = None, policy: list[float] | None = None) -> tuple[str, str, dict]:
    """Number-check the brief's own text. Each line's numbers must come from the evidence that line cites, from a
    supported number in a finding, or from an action figure that passed its own check. Others are marked inline.
    answer_rules: rule thresholds the model was shown in its facts (e.g. "rose by 25% or more"); the model-written
    answer may repeat those, and nothing else that isn't a supported number."""
    evidence = state.get("evidence", {})
    supported: list[float] = []
    for f in state.get("findings", []):
        c = check_numbers(f["claim"], finding_evidence(f, evidence), f.get("key_numbers"))
        supported += [n.value for n in numbers_in(f["claim"]) if c["support"].get(n.raw, "missing") != "missing"]
    for a in actions:
        supported += [float(a[k]) for k in ("est_cost_inr", "value_protected_inr", "orders_protected", "low_reviews")
                      if isinstance(a.get(k), (int, float))]
    checked, unverified = 0, []
    all_ev = {**evidence, **{e: ev for a in actions for e in a.get("evidence", []) if (ev := evidence.get(e))}}
    in_answer = False

    def mark(line: str) -> str:
        nonlocal checked, in_answer
        if line.startswith("## "):
            in_answer = line.strip().lower() == "## answer"
        body = re.sub(r"^\s*\d+\.\s", lambda m: " " * len(m.group(0)), line)  # list numbers aren't claims
        evs = [all_ev[i] for i in _CITE.findall(line) if i in all_ev]
        extra = list(supported) + (list(answer_rules or []) if in_answer else [])
        if line.startswith("| A") and policy:
            extra += list(policy)   # an action row may state the configured approval threshold (policy, not data)
        if not in_answer:
            # lines written by code may use any cell of the computed tables they cite, and the rule constants;
            # the model-written answer gets the strict check
            extra += RULE_CONSTANTS + [float(x) for ev in evs if ev.get("type") == "analysis"
                                       for row in ev.get("rows", []) for x in row
                                       if isinstance(x, (int, float)) and not isinstance(x, bool)]
        c = check_numbers(body, evs, None, extra_values=extra)
        nums = numbers_in(body)
        checked += len(nums)
        out = line
        for n in sorted(nums, key=lambda n: -n.start):
            if c["support"].get(n.raw) == "missing" and line[n.end:n.end + len(_UNVERIFIED)] != _UNVERIFIED:
                end = n.end + (1 if line[n.end:n.end + 1] == "%" else 0)
                unverified.append(n.raw + ("%" if end > n.end else ""))
                out = out[:end] + _UNVERIFIED + out[end:]
        return out

    md2 = "\n".join(mark(line) for line in md.split("\n"))
    head2 = mark(headline)
    report = {"checked": checked, "unverified": unverified}
    if ctx is not None:
        ctx.tracer.event("brief_numbers_check", "Reporter", checked=checked, unverified=unverified)
    return md2, head2, report


def refetch(ctx: Ctx):
    """Re-run a truncated cited query with a larger limit, so a key number can point at any row of it."""
    def fetch(table):
        if not table.sql:
            return None
        r = ctx.harness.query(table.sql, limit=2000)
        return r.rows if r.ok else None
    return fetch


def ground_action_numbers(a: dict, evidence: list[dict]) -> tuple[dict, list[str]]:
    """Keep an action's cost, value and order count only if each equals a cell in the rows it cites (no arithmetic).
    Zero is always allowed. Returns the cleaned figures and a list of the ones removed."""
    out, removed = {}, []
    for key, alt in (("est_cost", "est_cost_inr"), ("value_protected", "value_protected_inr"), ("orders_protected", None)):
        raw = a.get(key, a.get(alt) if alt else None)
        if raw in (None, ""):
            out[key] = None
            continue
        v = _num(raw)
        if v == 0 or figure_supported(raw, evidence):
            out[key] = v
        else:
            out[key] = None
            removed.append(f"{key}={raw}")
    return out, removed


def act_supply(state: dict, ctx: Ctx) -> dict:
    """Actions for a supply question, sized in code: a proven-optimal stock transfer (Optiagent) when supply is late,
    computed reorder points when demand outran the policy, and a cancellation list for duplicates."""
    from whychain.optimize import optimize_rebalance

    agent = "Risk & Action"
    v = state.get("verdicts", {})
    tasks = {t["id"]: t for t in state.get("tasks", [])}
    spec = state["analysis"]["spec"]
    cur = getattr(ctx.dataset, "currency", "")
    threshold = getattr(ctx.dataset, "approval_threshold", ctx.settings.approval_threshold_inr)
    evidence, actions = {}, []
    confirmed = {tasks[f["task_id"]].get("tool"): f for f in state["findings"]
                 if v.get(f["task_id"], {}).get("is_cause") and f["task_id"] in tasks}

    def ev_of(f):
        return state["evidence"].get(f["evidence_ids"][0], {}) if f and f.get("evidence_ids") else {}

    def add(title, steps, addresses, ev_ids, cost=None, orders=None, approval=None, value=None):
        reasons = list(approval or [])
        if cost is not None and cost > threshold:
            reasons.append(f"cost {cur} {cost:,.0f} exceeds the {cur} {threshold:,.0f} threshold")
        actions.append({"id": f"A{len(actions) + 1}", "title": title, "steps": steps, "addresses": addresses,
                        "orders_protected": orders, "value_protected_inr": value or None, "est_cost_inr": cost,
                        "currency": cur, "evidence": ev_ids, "unsupported_figures": [],
                        "needs_approval": bool(reasons), "approval_reasons": reasons, "decision": "pending",
                        "time_to_effect": "this week", "policy_notes": ""})

    late = confirmed.get("inbound") or confirmed.get("port")
    if late:
        summ = ev_of(late).get("summary", {})
        sup = summ.get("top_supplier")
        if sup:
            try:
                # the question's own scope: its supplier, and its categories when it names some
                opt_scope = {"supplier_id": sup, **({"categories": spec["categories"]} if spec.get("categories") else {})}
                out = optimize_rebalance(ctx.harness, opt_scope, spec["warehouses"] or None, 7, 14)
                sm = out.get("summary", {})
                ok = bool(out.get("verified")) and out.get("outcome") == "verified_optimal"
                ctx.tracer.event("optimize", agent, task_id="A", evidence_id="A.P1" if ok else None, ok=ok,
                                 outcome=out.get("outcome"), need_units=sm.get("need_units"),
                                 truck_units=sm.get("truck_units"), air_units=sm.get("air_units"),
                                 unfilled_units=sm.get("unfilled_units"), objective_inr=out.get("objective_inr"))
                if ok and (sm.get("truck_units") or sm.get("air_units")):
                    from whychain.optimize import orders_covered

                    covered, short_orders, covered_value = orders_covered(
                        ctx.harness, opt_scope, spec["warehouses"] or None, 7, out.get("columns", []),
                        out.get("rows", []), with_value=True)
                    transport = round(float(sm.get("truck_cost_inr") or 0) + float(sm.get("air_cost_inr") or 0))
                    evidence["A.P1"] = {"id": "A.P1", "type": "optimization", "task_id": "A", "agent": agent,
                                        "purpose": f"cover {sup} shortages at {', '.join(spec['warehouses']) or 'the DCs'} within 7 days",
                                        **out, "row_count": len(out.get("rows", [])), "harness": ctx.harness.name}
                    lanes = ", ".join(f"{t['lane']} {t['units']:,} units" for t in sm.get("trucks", []))
                    by = (date.fromisoformat(str(ctx.as_of)[:10]) + timedelta(days=7)).isoformat()
                    what = f"{sup}'s {' and '.join(c.lower() for c in spec['categories'])}" if spec.get("categories") \
                        else f"{sup}'s SKUs"
                    add(f"Transfer stock to cover the {sup} shortage: {lanes or 'air freight only'}"
                        + (f", plus {sm['air_units']:,} units by air" if sm.get("air_units") else ""),
                        f"Proven-optimal plan (Optiagent, duality certificate): {sm.get('truck_units', 0):,} units by truck"
                        f" and {sm.get('air_units', 0):,} by air, covering {round(100 * (sm.get('covered_share') or 0))}% "
                        f"of the {sm.get('need_units', 0):,} units of {what} backordered at "
                        f"{', '.join(spec['warehouses']) or 'the DCs'} with a promised date on or before {by}. That "
                        "includes orders already past their promised date, which still need the stock, not only the "
                        "ones due next week"
                        + (f"; {sm['unfilled_units']:,} units can't arrive in time" if sm.get("unfilled_units") else "")
                        + f". It fully covers {covered} of the {short_orders} orders short in that scope. The cost is "
                        "transport only (trucks and air surcharge); units that can't arrive in time are lost sales, not "
                        "a cost of this action.", [late["task_id"]], ["A.P1"], cost=transport, orders=covered,
                        value=covered_value)
            except Exception as e:
                ctx.tracer.event("agent_error", agent, error=f"optimizer failed: {e}"[:200])
        add(f"Chase the late purchase orders from {summ.get('top_supplier_name') or sup} and plan around "
            f"{summ.get('top_port') or 'the port'}",
            "Ask the supplier for revised ETAs; reroute through an open port or by air where the transfer plan "
            "doesn't cover the need.", [late["task_id"]], late["evidence_ids"])
    pol = confirmed.get("policy") or confirmed.get("demand")
    if pol:
        pev = ev_of(confirmed["policy"]) if confirmed.get("policy") else {}
        stale = [r for r in pev.get("rows", []) if len(r) > 7 and r[7] == "stale"][:5]
        detail = "; ".join(f"{r[1]} at {r[0]} {r[2]} -> {r[5]}" for r in stale)
        promos = ev_of(confirmed["demand"]).get("summary", {}).get("promos") if confirmed.get("demand") else []
        add("Raise reorder points to recent demand" + (f": {detail}" if detail else ""),
            "Recompute reorder points from recent daily demand over the supplier lead time plus safety stock, and "
            "review them monthly." + (f" Share the promotion calendar ({', '.join(promos)}) with planning before "
                                      "campaigns start." if promos else ""),
            [pol["task_id"]], ([confirmed["policy"]["evidence_ids"][0]] if confirmed.get("policy") else [])
            + ([confirmed["demand"]["evidence_ids"][0]] if confirmed.get("demand") else []))
    dup = confirmed.get("duplicates")
    if dup:
        summ = ev_of(dup).get("summary", {})
        ids = summ.get("extra_order_ids", [])
        add(f"Cancel the duplicate orders and fix the {summ.get('channel') or 'order'} connector",
            f"These orders repeat an earlier order's external reference: {', '.join(ids[:15])}"
            + (" and more" if len(ids) > 15 else "") + ". Cancel them after confirming with the customer, then "
            "stop the connector creating repeats.", [dup["task_id"]], dup["evidence_ids"],
            approval=["cancels customer orders"])
    ctx.tracer.event("agent_end", agent, actions=[(a["id"], a["title"], a["needs_approval"]) for a in actions])
    for a in actions:
        if a["needs_approval"]:
            ctx.tracer.event("escalation", agent, action_id=a["id"], title=a["title"], reasons=a["approval_reasons"])
    return {"actions": actions, "risks": [], "evidence": evidence}


def act_battery(state: dict, ctx: Ctx) -> dict:
    """Actions for a late-delivery question, from templates per confirmed cause, sized with computed tables.
    No model: every figure is a cell of a cited result."""
    agent = "Risk & Action"
    v = state.get("verdicts", {})
    tasks = {t["id"]: t for t in state.get("tasks", [])}
    eng = engine_for(ctx)
    primary = state["analysis"]["primary"]
    evidence, actions = {}, []
    causes = [f for f in state["findings"] if v.get(f["task_id"], {}).get("is_cause")]
    tools = {tasks[f["task_id"]].get("tool"): f for f in causes if f["task_id"] in tasks}
    stages = {tasks[f["task_id"]]["params"].get("stage"): f for f in causes
              if tasks.get(f["task_id"], {}).get("tool") == "stage"}
    cur = getattr(ctx.dataset, "currency", "")
    has_reviews = bool(eng.m.reviews_sql)

    def add(title, steps, addresses, ev_ids, orders=None, approval=None, value=None, low=None):
        # value: what the orders it would have turned on time are worth (the value at stake, not the cost of the
        # fix); low: how many of them left a 1- or 2-star review. Both are sums over the cited table's rows.
        actions.append({"id": f"A{len(actions) + 1}", "title": title, "steps": steps, "addresses": addresses,
                        "orders_protected": orders, "value_protected_inr": value or None, "low_reviews": low,
                        "est_cost_inr": None,
                        "currency": cur, "evidence": ev_ids, "unsupported_figures": [],
                        "needs_approval": bool(approval), "approval_reasons": approval or [], "decision": "pending",
                        "time_to_effect": "next orders", "policy_notes": ""})

    if "transit" in stages or "promise" in stages:
        try:
            res = eng.promise_buffer(build_scope(eng, primary))
            evidence["A.A1"] = res.evidence("A.A1", "A", "Analysis engine")
            need = [r for r in res.rows if r[4] > 0][:5]
            if need:
                where = ", ".join(f"{r[0]} +{r[4]} {'day' if r[4] == 1 else 'days'}" for r in need)
                avoided = sum(r[5] - r[6] for r in need)
                add(f"Add delivery-promise buffers where transit outruns the promise: {where}",
                    f"Set each area's promised window so that four in five orders arrive within it. On the orders "
                    f"in question, this would have turned {avoided} late deliveries into on-time ones in these areas.",
                    [stages.get("transit", stages.get("promise"))["task_id"]], ["A.A1"], avoided,
                    ["changes what customers are promised"], value=sum(r[9] for r in need),
                    low=sum(r[10] for r in need) if has_reviews else None)
        except Exception as e:
            ctx.tracer.event("agent_error", agent, error=f"promise buffer failed: {e}"[:200])
    n_ev = [2]   # A.A1 is the promise buffer, A.A2 the carrier options

    def add_ev(res) -> str:
        n_ev[0] += 1
        eid = f"A.A{n_ev[0]}"
        evidence[eid] = {**res.evidence(eid, "A", "Analysis engine"), "harness": ctx.harness.name}
        return eid

    if "transit" in stages:
        f = stages["transit"]
        fair = next((sc for sc in state["analysis"].get("scopes", []) if sc.get("compare_with")), None)
        normal = "in places at a similar distance" if fair else "normally"
        top = []
        try:
            res_t = eng.transit_fix(build_scope(eng, fair or primary))
            top = [r for r in res_t.rows if r[6] > 0 and r[2] - r[3] >= 0.25][:3]
            eid_t = add_ev(res_t) if top else None
        except Exception as e:
            ctx.tracer.event("agent_error", agent, error=f"transit fix failed: {e}"[:200])
        if top:
            saved = sum(r[6] for r in top)
            add(f"Get transit back to normal in {', '.join(r[0] for r in top)}: {saved:,} late orders would have "
                "arrived on time",
                "; ".join(f"{r[0]}: transit {r[2]} days on average against {r[3]} {normal}, over {r[1]:,} orders; "
                          f"{r[6]} of its {r[5]} late orders would have been on time had four in five parcels arrived "
                          f"within {r[4]:g} days" for r in top)
                + ". Agree these recovery targets with the carriers serving the areas, and alert when an area's "
                "weekly average transit moves a day above normal.", [f["task_id"]], [eid_t] + f["evidence_ids"][:1],
                saved, value=sum(r[8] for r in top), low=sum(r[9] for r in top) if has_reviews else None)
        else:
            add("Escalate carrier transit performance with the carrier, with weekly transit monitoring by area",
                "Share the transit figures with the carrier, agree recovery targets, and alert when an area's weekly "
                "average transit moves a day above its normal.", [f["task_id"]], f["evidence_ids"])
    if "transit" in stages and "carrier_id" in eng.m.dims and "warehouse_id" in eng.m.dims:
        # a transit problem concentrated in one carrier: compare it with the carriers serving the same warehouses
        try:
            res_c = eng.carrier_options(build_scope(eng, primary))
            evidence["A.A2"] = res_c.evidence("A.A2", "A", "Analysis engine")
            used = [r for r in res_c.rows if r[4]]
            main = max(used, key=lambda r: r[4]) if used else None
            alts = [r for r in res_c.rows if main and r[0] != main[0] and r[1] >= 30]
            if main and alts and main[5] is not None and alts[0][2] is not None and main[5] - alts[0][2] >= 10:
                best = [a for a in alts if a[2] is not None and a[2] - alts[0][2] <= 2][:2]   # near-ties: name both
                names = " or ".join(a[0] for a in best)
                perf = "; ".join(f"{a[0]} {a[2]}% late, median transit {a[3]} days over {a[1]} orders" for a in best)
                add(f"Switch carrier for these deliveries: from {main[0]} to an alternative carrier ({names})",
                    f"On these orders {main[0]} was {main[5]}% late. From the same warehouses in the same period: "
                    f"{perf}. Move the lane (or trial it first) and keep the transit monitoring in place.",
                    [stages["transit"]["task_id"]], ["A.A2"], None,   # how many it would protect isn't computable
                    ["changes a carrier contract or allocation"])
        except Exception as e:
            ctx.tracer.event("agent_error", agent, error=f"carrier options failed: {e}"[:200])
    stock = next((g for g in state["findings"] if tasks.get(g["task_id"], {}).get("tool") == "stock_wait"
                  and v.get(g["task_id"], {}).get("status") == "confirmed"), None)
    if "handover" in stages and stock:
        # dispatch slowed because orders sat waiting for stock: the fix is the stock, not the warehouse
        summ = state["evidence"].get(stock["evidence_ids"][0], {}).get("summary", {})
        sups = summ.get("suppliers", [])
        whs = sorted(summ.get("warehouses", {}).items(), key=lambda kv: -kv[1])
        named = "; ".join(f"{x[1]} ({x[2]})" + (f", demand lifted by promotion {x[5]}" if x[4] >= 0.2 and x[5] else "")
                          for x in sups[:3])
        add("Clear the stock-outs that held up dispatch" + (f": {named}" if named else ""),
            "Orders waited for stock, not for the warehouse. For each warehouse with held orders ("
            + ", ".join(f"{w}: {n}" for w, n in whs) + "), run a supply investigation (for example \"Why did "
            + (f"{whs[0][0]} run out of {sups[0][2]}?" if whs and sups else "this warehouse run out of stock?")
            + "\") to size a stock transfer and new reorder points, and chase the open purchase orders from these "
            "suppliers. Share promotion plans with planning before campaigns start.",
            [stages["handover"]["task_id"], stock["task_id"]], stock["evidence_ids"])
    elif "handover" in stages:
        f = stages["handover"]
        top = []
        try:
            res_h = eng.handover_fix(build_scope(eng, primary))
            top = [r for r in res_h.rows if r[7] > 0 and r[3] - r[4] >= 0.25][:3]
            eid_h = add_ev(res_h) if top else None
        except Exception as e:
            ctx.tracer.event("agent_error", agent, error=f"hand-over fix failed: {e}"[:200])
        if top:
            sellers = res_h.columns[0] == "seller_id"
            kind = "sellers" if sellers else "warehouses"
            where = f" (based in {', '.join(dict.fromkeys(r[1] for r in top if r[1]))})" if sellers else \
                f" ({', '.join(r[0] for r in top)})"
            saved = sum(r[7] for r in top)
            add(f"Fix hand-over at the {len(top)} slowest {kind}{where}: {saved:,} late orders would have arrived on "
                "time",
                "; ".join(f"{'seller ' + r[0] if sellers else r[0]}: hand-over {r[3]} days on average against "
                          f"{r[4]} normally, over {r[2]:,} orders; {r[7]} of its {r[6]} late orders would have been on "
                          f"time at its normal pace" for r in top)
                + ". Agree a hand-over deadline with each at its normal pace, and review it weekly.",
                [f["task_id"]], [eid_h] + f["evidence_ids"][:1], saved, value=sum(r[8] for r in top),
                low=sum(r[9] for r in top) if has_reviews else None)
        else:
            add("Enforce hand-over deadlines with the slowest senders",
                "Rank sellers (or warehouses) by late hand-overs in the period and agree corrective plans.",
                [f["task_id"]], f["evidence_ids"])
    if "volume" in tools:
        f = tools["volume"]
        vs = state["evidence"].get(f["evidence_ids"][0], {}).get("summary", {})
        dh, dt = vs.get("handover_change_days") or 0, vs.get("transit_change_days") or 0
        days = max(1, math.ceil(max(dh, 0) + max(dt, 0)))
        try:
            res_w = eng.window_buffer(build_scope(eng, primary), days)
            eid_w, avoided, late = add_ev(res_w), res_w.summary["avoided"], res_w.rows[1][1]
            add(f"Plan for the next surge: extend promises by {days} {'day' if days == 1 else 'days'} when orders run "
                f"{vs.get('orders_per_day_ratio')}x the usual level",
                f"Orders ran {vs.get('focus_orders_per_day')} a day against {vs.get('ref_orders_per_day')} in the four "
                f"weeks before; hand-over slowed by {dh} days and transit by {dt}. Pre-book carrier pick-ups for about "
                f"{vs.get('focus_orders_per_day')} orders a day before the next peak, and extend promises by {days} "
                f"{'day' if days == 1 else 'days'} for orders placed in it: on these orders that would have avoided "
                f"{avoided} of {late} late deliveries.", [f["task_id"]], [eid_w] + f["evidence_ids"][:1], avoided,
                ["changes what customers are promised"], value=res_w.summary.get("value"),
                low=res_w.summary.get("low_reviews") if has_reviews else None)
        except Exception as e:
            ctx.tracer.event("agent_error", agent, error=f"surge plan failed: {e}"[:200])
    for f in causes:
        t = tasks.get(f["task_id"], {})
        if t.get("tool") == "event":
            name = f["statement"].split(" (")[0]
            es = state["evidence"].get(f["evidence_ids"][0], {}).get("summary", {})
            jump = es.get("transit_jump_vs_trend_days") or 0
            days = max(1, math.ceil(jump))
            try:
                res_w = eng.window_buffer(build_scope(eng, primary), days, date.fromisoformat(es["start"]),
                                          date.fromisoformat(es["end"]))
                eid_w, avoided, late = add_ev(res_w), res_w.summary["avoided"], res_w.rows[1][1]
                add(f"Playbook for events like {name}: extend promises by {days} {'day' if days == 1 else 'days'} "
                    "for parcels handed over during it",
                    f"Transit rose {jump} days against the prior trend for parcels handed over during the event. "
                    + (f"Extending their promises by {days} {'day' if days == 1 else 'days'} would have avoided "
                       f"{avoided} of {late} late deliveries on these orders. " if late else "")
                    + "When a comparable event starts, extend the promise for parcels in transit and tell the "
                    "customers.", [f["task_id"]], [eid_w] + f["evidence_ids"][:1], avoided or None,
                    ["changes what customers are promised"], value=res_w.summary.get("value") if avoided else None,
                    low=res_w.summary.get("low_reviews") if avoided and has_reviews else None)
            except Exception as e:
                ctx.tracer.event("agent_error", agent, error=f"event playbook failed: {e}"[:200])
        if t.get("tool") == "segments":
            ev = state["evidence"].get(f["evidence_ids"][0], {})
            dim = t.get("params", {}).get("dim")
            label = eng.m.dims.get(dim, dim)
            segs = [r for r in ev.get("rows", []) if not str(r[0]).startswith("(") and (r[1] or 0) >= 30]
            if len(segs) >= 2:
                worst, best = max(segs, key=lambda r: r[3]), min(segs, key=lambda r: r[3])
                add(f"Ship more of these orders from nearby: by {label}, '{worst[0]}' orders were {worst[3]}% late "
                    f"against {best[3]}% for '{best[0]}'",
                    f"'{worst[0]}': {worst[1]:,} orders, {worst[3]}% late; '{best[0]}': {best[1]:,} orders, "
                    f"{best[3]}% late. Route orders to closer sellers or stock where possible, and set promises by "
                    f"{label}.", [f["task_id"]], f["evidence_ids"])
            else:
                add("Address the order mix behind the gap",
                    "Serve these customers from closer stock or sellers where possible, and set promises by distance.",
                    [f["task_id"]], f["evidence_ids"])
    ctx.tracer.event("agent_end", agent, actions=[(a["id"], a["title"], a["needs_approval"]) for a in actions])
    for a in actions:
        if a["needs_approval"]:
            ctx.tracer.event("escalation", agent, action_id=a["id"], title=a["title"], reasons=a["approval_reasons"])
    return {"actions": actions, "risks": [], "evidence": evidence}


def act(state: dict, ctx: Ctx) -> dict:
    agent = "Risk & Action"
    confirmed = [f for f in state["findings"]
                 if state["verdicts"].get(f["task_id"], {}).get("status") == "confirmed"
                 and state["verdicts"][f["task_id"]].get("is_cause", True)]
    if not confirmed:
        ctx.tracer.event("agent_skip", agent, note="No confirmed causes; no actions proposed")
        return {"actions": [], "risks": []}
    ctx.tracer.event("agent_start", agent, causes=[f["task_id"] for f in confirmed])
    if state.get("analysis", {}).get("kind") == "supply":
        return act_supply(state, ctx)
    if state.get("analysis"):
        return act_battery(state, ctx)
    optimizer = getattr(ctx.dataset, "optimizer", True)
    currency = getattr(ctx.dataset, "currency", "INR")
    extra_ev, extra_txt = {}, ""
    if state.get("analysis") and not optimizer:
        # prescriptive numbers for the real dataset: the promise buffer each area needs (computed, not estimated)
        try:
            eng = engine_for(ctx)
            res_b = eng.promise_buffer(build_scope(eng, state["analysis"]["primary"]))
            extra_ev["A.A1"] = res_b.evidence("A.A1", "A", "Analysis engine")
            extra_txt = ("\n\nCOMPUTED FOR YOU (cite it as A.A1; use its cells as the action's numbers):\n"
                         + evidence_text(extra_ev["A.A1"], 12))
            ctx.tracer.event("analysis", agent, task_id="A", evidence_id="A.A1", tool="promise_buffer",
                             title=res_b.title, verdict="measured", rows=len(res_b.rows))
        except Exception as e:
            ctx.tracer.event("agent_error", agent, error=f"promise buffer failed: {e}"[:200])
    threshold = getattr(ctx.dataset, "approval_threshold", ctx.settings.approval_threshold_inr)
    brief = (base_context(state, ctx) + f"\n\nCONFIRMED CAUSES:\n{findings_text(confirmed, state['evidence'])}"
             f"\n\nApproval threshold: actions costing more than {currency} {threshold:,.0f} "
             f"need sign-off from the head of operations.{extra_txt}\n\nCATALOG:\n{ctx.catalog_text}")
    res = run_loop(ctx, agent=agent, role="work", system=fill(P.risk_prompt(optimizer, currency), 8), brief=brief,
                   task_id="A", max_steps=8, allow_docs=True, allow_optimize=optimizer, finish_keys=("actions",))
    f = res.final
    actions = []
    all_ev = {**state["evidence"], **extra_ev, **res.evidence}
    for i, a in enumerate(f.get("actions", []) or [], 1):
        if not isinstance(a, dict):
            continue
        ids = [e for e in (a.get("evidence") or []) if e in all_ev]
        # money and counts must come from the data the action cites; a figure that doesn't is removed, not shown
        grounded, unsupported = ground_action_numbers(a, [all_ev[e] for e in ids])
        cost = grounded["est_cost"]
        conf = _num(a.get("confidence"), 0.5)
        reasons = []
        if cost is not None and cost > threshold:
            reasons.append(f"cost {currency} {cost:,.0f} exceeds the {currency} {threshold:,.0f} threshold")
        if cost is None and a.get("est_cost", a.get("est_cost_inr")) not in (None, 0, "0", ""):
            reasons.append("cost not computed from the data")
        if conf < 0.6:
            reasons.append(f"confidence {conf:.2f} is below 0.6")
        if policy_requires_approval(str(a.get("policy_notes", ""))):
            reasons.append("policy requires approval")
        act_ = {**a, "id": f"A{i}", "est_cost_inr": cost, "currency": currency, "confidence": conf, "evidence": ids,
                "value_protected_inr": grounded["value_protected"], "orders_protected": grounded["orders_protected"],
                "unsupported_figures": unsupported,
                "needs_approval": bool(reasons), "approval_reasons": reasons, "decision": "pending"}
        if unsupported:
            ctx.tracer.event("figures_removed", agent, action_id=act_["id"], figures=unsupported)
        actions.append(act_)
        if reasons:
            ctx.tracer.event("escalation", agent, action_id=act_["id"], title=a.get("title"), reasons=reasons)
    ctx.tracer.event("agent_end", agent, actions=[(a["id"], a.get("title"), a["needs_approval"]) for a in actions])
    return {"actions": actions, "risks": f.get("risks", []) or [], "evidence": {**extra_ev, **res.evidence}}


def tidy_brief(md: str, headline: str) -> tuple[str, str]:
    """Repair common model slips: escaped newlines, a headline that swallowed the brief, repeated sections."""
    md, headline = md.replace("\\n", "\n"), headline.replace("\\n", "\n")
    if "\n" in headline.strip():
        first, rest = headline.strip().split("\n", 1)
        headline = first
        if len(rest.strip()) > len(md.strip()):
            md = rest.strip()
    md = re.sub(r'",\s*"lessons"\s*:.*\Z', "", md, flags=re.S)  # trailing JSON debris
    parts = re.split(r"(?m)^(?=## )", md)
    seen, kept = set(), []
    for part in parts:
        head = part.split("\n", 1)[0].strip().lower()
        if head.startswith("## ") and head in seen:
            continue
        seen.add(head)
        kept.append(part)
    return "".join(kept).strip(), headline.strip()


def parse_brief(text: str) -> dict:
    """Split the Reporter's markdown into headline, brief and lessons. Accepts a JSON reply too."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    t = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", t)
    if t.startswith("{"):
        try:
            j = extract_json(t)
            return {"headline": str(j.get("headline", "")), "brief_markdown": str(j.get("brief_markdown", "")),
                    "lessons": [str(x) for x in j.get("lessons") or []]}
        except ValueError:
            pass
    headline, lessons = "", []
    m = re.search(r"(?im)^\s*\**HEADLINE\**\s*:\s*\**(.+?)\**\s*$", t)
    if m:
        headline = m.group(1).strip()
        t = t[:m.start()] + t[m.end():]
    m = re.search(r"(?im)^\s*\**LESSONS\**\s*:?\s*\**\s*$", t)
    if m:
        lessons = [re.sub(r"^\s*[-*\d.]+\s*", "", ln).strip() for ln in t[m.end():].splitlines()]
        lessons = [ln.strip('"') for ln in lessons if ln][:3]
        t = t[:m.start()]
    if not headline:  # fall back to the first sentence of the answer
        ans = re.search(r"(?is)##\s*Answer\s*\n+(.+?)(?:\n\n|\Z)", t)
        headline = re.split(r"(?<=[.!?])\s", ans.group(1).strip())[0] if ans else ""
    return {"headline": headline, "brief_markdown": t.strip(), "lessons": lessons}


ANSWER_PROMPT = """You write the short answer at the top of a supply chain brief for a busy manager.
Use ONLY the facts given. Copy every number exactly as written, keep the evidence ids in square brackets right after
the numbers they support, and add no new numbers. Two or three sentences, plain English, no headings."""


def _cites(f: dict) -> str:
    return "".join(f"[{e}]" for e in f.get("evidence_ids", [])[:2])


def _short(statement: str) -> str:
    s = statement.strip().rstrip(".")
    return s[0].lower() + s[1:] if s else s


def _no_rise(state: dict, measure: dict) -> bool:
    """The measured late rate is not above the reference (the question's premise doesn't hold)."""
    ev = state["evidence"].get((measure.get("evidence_ids") or [""])[0], {})
    rows = {str(r[0]): r for r in ev.get("rows", [])}
    try:
        cols = ev["columns"]
        f, r = rows["focus"][cols.index("late_pct")], rows["reference"][cols.index("late_pct")]
        return f is not None and r is not None and f <= r
    except (KeyError, ValueError, IndexError):
        return False


def _no_shortage(state: dict, measure: dict) -> bool:
    """A supply question whose scope has no order on backorder (the question's premise doesn't hold)."""
    ev = state["evidence"].get((measure.get("evidence_ids") or [""])[0], {})
    rows = {str(r[0]): r[1] for r in ev.get("rows", []) if r}
    return rows.get("orders with a backordered line") == 0


def why_chain(state: dict, ctx: Ctx, causes: list, held_by: dict, where: list, ruled: list, v: dict) -> None:
    """Write the chain of 'why' to the Meterless World Model as relations: each confirmed cause of the symptom (with
    its share), what explains a cause (a disrupted port, orders waiting for stock), where the problem sits, and what
    was ruled out. The app draws the chain from the World Model. Only links the tests confirmed are written."""
    symptom = {"type": "symptom", "name": str(state.get("symptom") or state["question"])[:160]}
    analysis = state.get("analysis", {})
    of = ("of the short units" if analysis.get("kind") == "supply" else
          "of the gap" if (analysis.get("primary") or {}).get("mode") == "segment" else "of the rise")
    rel = []
    for f in causes:
        node = {"type": "cause", "name": f["statement"][:160]}
        sh = v[f["task_id"]].get("share_of_symptom")
        share = sh if isinstance(sh, float) else None
        rel.append({"from": node, "type": "causes", "to": symptom, "confidence": share,
                    "attrs": {"share_of_symptom": share, "share_of": of, "why_chain": True}})
        if f["task_id"] in held_by:
            rel.append({"from": {"type": "cause", "name": held_by[f["task_id"]]["statement"][:160]}, "type": "explains",
                        "to": node})
    names = getattr(engine_for(ctx), "m", None)
    place_names = getattr(names, "place_names", {}) or {}
    dims = getattr(names, "dims", {}) or {}
    for f in where:
        summ = state["evidence"].get((f.get("evidence_ids") or [""])[0], {}).get("summary", {})
        label = dims.get(summ.get("dimension"), summ.get("dimension") or "segment")
        for x in (summ.get("concentrated_in") or [])[:3]:
            nm = f"{x} ({place_names[x]})" if x in place_names else str(x)
            rel.append({"from": symptom, "type": "concentrated_in", "to": {"type": "place", "name": f"{label}: {nm}"}})
    for f in ruled[:8]:
        rel.append({"from": {"type": "hypothesis", "name": f["statement"][:160]}, "type": "ruled_out_for",
                    "to": symptom})
    if rel:
        ctx.tracer.event("why_chain", "Reporter", relations=rel)


def ask_next(state: dict, ctx: Ctx, causes: list, where: list, held_by: dict) -> list[tuple[str, str]]:
    """The next 'why', from this brief's own findings: (question, reason). Only questions WhyChain answers on its
    computed engines: the place where the rise sits (the same period, scoped to it), and the warehouse and product whose
    stock-outs held up dispatch."""
    out: list[tuple[str, str]] = []
    primary = (state.get("analysis") or {}).get("primary") or {}
    eng = engine_for(ctx)
    m = getattr(eng, "m", None)
    label = str(primary.get("label") or "")
    if m is not None and causes and primary.get("mode") == "time" and " placed " in label and " during " not in label:
        period = label.split(" placed ", 1)[1]
        taken = {str(x) for vals in (primary.get("filters") or {}).values() for x in vals}
        for f in where:
            summ = state["evidence"].get((f.get("evidence_ids") or [""])[0], {}).get("summary", {})
            if summ.get("dimension") != m.map_within:
                continue
            for place in summ.get("concentrated_in") or []:
                if place in taken:
                    continue
                name = (m.place_names or {}).get(place, place)
                out.append((f"Why were deliveries to {name} late for orders placed {period}?",
                            f"The rise is concentrated in {name}: what made it worse there?"))
                break
            if out:
                break
    for f in causes:
        why = held_by.get(f["task_id"])
        summ = state["evidence"].get((why or {}).get("evidence_ids", [""])[0], {}).get("summary", {}) if why else {}
        sups, whs = summ.get("suppliers") or [], sorted((summ.get("warehouses") or {}).items(), key=lambda kv: -kv[1])
        if sups and whs:
            city = whs[0][0]
            try:
                seng = get_supply_engine(ctx.harness)
                names = {str(r.warehouse_id): str(r.name) for r in seng.warehouses().itertuples()} if seng else {}
                city = names.get(whs[0][0], whs[0][0]).split(" ")[0]
            except Exception:
                pass
            out.append((f"Why did {city} run out of {str(sups[0][2]).lower()}?",
                        f"Dispatch slowed because orders waited for {sups[0][1]} stock at {city}: why was it short?"))
            break
    return out[:2]


def _money(cur: str, v) -> str:
    return f"R$ {v:,.0f}" if cur == "BRL" else f"{cur} {v:,.0f}"


def report_battery(state: dict, ctx: Ctx, preliminary: bool = False) -> dict:
    """Brief for a battery run: built from the coded verdicts; the model only phrases a short answer.
    preliminary: stop after the code-written headline and summary (no model call, no trace events)."""
    agent = "Reporter"
    v = state.get("verdicts", {})
    tasks = {t["id"]: t for t in state.get("tasks", [])}
    fs = state["findings"]
    supply = state.get("analysis", {}).get("kind") == "supply"
    measure = next((f for f in fs if tasks.get(f["task_id"], {}).get("tool") in ("measure", "supply_measure")), None)
    causes = sorted([f for f in fs if v.get(f["task_id"], {}).get("is_cause") and v[f["task_id"]]["status"] == "confirmed"],
                    key=lambda f: -(v[f["task_id"]].get("share_of_symptom") or 0) if isinstance(
                        v[f["task_id"]].get("share_of_symptom"), float) else 1)
    ruled = [f for f in fs if v.get(f["task_id"], {}).get("status") == "rejected" and f.get("agent_kind") == "tool"]
    where = [f for f in fs if tasks.get(f["task_id"], {}).get("tool") == "segments"
             and not v.get(f["task_id"], {}).get("is_cause") and v.get(f["task_id"], {}).get("status") == "confirmed"]
    context = [f for f in fs if tasks.get(f["task_id"], {}).get("tool") == "text"]
    quotes = [state["evidence"][e] for f in fs if f.get("agent_kind") == "docs" for e in f["evidence_ids"]
              if state["evidence"].get(e, {}).get("type") == "doc"]
    named = state.get("analysis", {}).get("named_events") or []
    # what the shares are shares of: a rise over time, or a gap between two groups in the same period
    word = "gap" if (state.get("analysis", {}).get("primary") or {}).get("mode") == "segment" else "rise"
    event_f = [f for f in fs if tasks.get(f["task_id"], {}).get("tool") == "event"
               and tasks[f["task_id"]].get("params", {}).get("event_id") in named]
    impact = next((f for f in fs if tasks.get(f["task_id"], {}).get("tool") == "value_at_stake"
                   and v.get(f["task_id"], {}).get("status") == "confirmed"), None)
    cur = getattr(ctx.dataset, "currency", "")

    def share_txt(f):
        sh = v[f["task_id"]].get("share_of_symptom")
        if not isinstance(sh, float):
            return ""
        if supply and tasks.get(f["task_id"], {}).get("tool") == "duplicates":
            # not a share of the short units: the copies' own units, measured against the shortage
            return f" (the extra copies order units equal to {round(100 * sh, 1)}% of the shortage)"
        if not supply and sh > 1:
            return f" ({round(100 * sh, 1)}% of the {word}: more than all of it, because other factors offset part)"
        return f" ({round(100 * sh, 1)}% {'of the short units' if supply else 'of the ' + word})"

    # A disrupted port is why inbound orders are late, and waiting for stock is why dispatch slowed: each is shown
    # under the cause it explains, not as a second cause beside it.
    held_by: dict[str, dict] = {}
    tool_of = {f["task_id"]: tasks.get(f["task_id"], {}).get("tool") for f in fs}
    stock = next((f for f in fs if tool_of[f["task_id"]] == "stock_wait"
                  and v.get(f["task_id"], {}).get("status") == "confirmed"), None)
    hand = next((f for f in causes if tool_of[f["task_id"]] == "stage"
                 and tasks[f["task_id"]].get("params", {}).get("stage") == "handover"), None)
    if stock and hand:
        held_by[hand["task_id"]] = stock
    elif stock:
        where.append(stock)
    if supply:
        tool = {f["task_id"]: tasks.get(f["task_id"], {}).get("tool") for f in causes}
        inb = next((f for f in causes if tool[f["task_id"]] == "inbound"), None)
        port = next((f for f in causes if tool[f["task_id"]] == "port"), None)
        if inb and port:
            causes = [f for f in causes if f is not port]
            held_by[inb["task_id"]] = port

    def cause_txt(f):
        port = held_by.get(f["task_id"])
        if not port:
            return _short(f["statement"]) + share_txt(f)
        summ = state["evidence"].get((port.get("evidence_ids") or [""])[0], {}).get("summary", {})
        if tool_of.get(port["task_id"]) == "stock_wait":
            names = [x[1] for x in summ.get("suppliers", [])[:2]]
            return (_short(f["statement"]) + share_txt(f) + ", mostly orders waiting for stock"
                    + (f" of {' and '.join(names)} SKUs" if names else ""))
        name = summ.get("top_port")
        why = str((summ.get("port_status") or {}).get(name, "")).split(": ", 1)[-1].strip().lower()
        held = (f", held up at {name}" + (f" by a {why}" if why else "")) if name else ", held up at a disrupted port"
        return _short(f["statement"]) + held + share_txt(f)

    def held_txt(f):
        """', mostly orders waiting for stock of X SKUs' or ', held up at <port>': the reason under a cause."""
        port = held_by.get(f["task_id"])
        if not port:
            return ""
        summ = state["evidence"].get((port.get("evidence_ids") or [""])[0], {}).get("summary", {})
        if tool_of.get(port["task_id"]) == "stock_wait":
            names = [x[1] for x in summ.get("suppliers", [])[:2]]
            return ", mostly orders waiting for stock" + (f" of {' and '.join(names)} SKUs" if names else "")
        name = summ.get("top_port")
        why = str((summ.get("port_status") or {}).get(name, "")).split(": ", 1)[-1].strip().lower()
        return (f", held up at {name}" + (f" by a {why}" if why else "")) if name else ", held up at a disrupted port"

    # A stage re-tested against a fairer comparison (places at a similar distance) is the same cause, checked
    # again: shown under it, not as a second cause (its share would double-count the rise).
    also: dict[str, list[tuple[str, dict]]] = {}

    def _stage(f):
        t = tasks.get(f["task_id"], {})
        return t.get("params", {}).get("stage") if t.get("tool") == "stage" else None

    def _fair(f):
        return bool((tasks.get(f["task_id"], {}).get("scope") or {}).get("compare_with"))

    for f in [f for f in causes if _stage(f) and _fair(f)]:
        main = next((g for g in causes if _stage(g) == _stage(f) and not _fair(g)), None)
        if main:
            causes = [g for g in causes if g is not f]
            also.setdefault(main["task_id"], []).append(("Against places at a similar distance", f))

    # named conditions the battery can't test as dated events (long-running, e.g. a year of cargo theft)
    untested = ""
    ctx_ids = state.get("analysis", {}).get("context_events") or []
    if ctx_ids and not event_f and not supply:
        try:
            evs = engine_for(ctx).events()
            names = [str(evs[evs["event_id"] == x].iloc[0]["name"]) for x in ctx_ids if (evs["event_id"] == x).any()]
            names = [n[0].lower() + n[1:] if len(n) > 1 and n[1].islower() else n for n in names]
        except Exception:
            names = []
        if names:
            untested = (f"The data can't test {' or '.join(names)} directly: it is a long-running condition, not a "
                        "dated event. What the data shows:")

    # headline, in code
    if event_f:
        # a question about a dated event: say yes or no, and how much (or why not), from the event test's own numbers
        e = event_f[0]
        st = v[e["task_id"]]["status"]
        name = e["statement"].split(" (")[0]
        es = state["evidence"].get((e.get("evidence_ids") or [""])[0], {}).get("summary", {})
        if st == "confirmed":
            headline = (f"Yes: {name} delayed deliveries. For parcels handed over during it, carrier transit rose "
                        f"{es.get('transit_jump_vs_trend_days')} days and the late rate "
                        f"{es.get('late_jump_vs_trend_pp')} points against the trend before it.")
        elif es.get("elevated_before"):
            headline = (f"No: {name} did not cause the delays. They were already there: carrier transit was "
                        f"{es.get('pre_event_transit_days')} days just before it, against a normal "
                        f"{es.get('normal_transit_days')}.")
        else:
            headline = f"No: {name} did not cause the delays. {v[e['task_id']]['reason']}"
    elif causes:
        top = (", and " if held_by else " and ").join(cause_txt(f) for f in causes[:2])
        headline = f"Main causes: {top}."
        if measure:
            headline = measure["claim"].split(":")[0] + f". {headline}"
    elif measure and supply and not causes and _no_shortage(state, measure):
        headline = ("There is no shortage to explain: " + measure["claim"].split(":")[0][0].lower()
                    + measure["claim"].split(":")[0][1:] + ".")
    elif measure and not supply and _no_rise(state, measure):
        lead, tail = (("It did not rise: ", ". There is no rise to explain.") if word == "rise" else
                      ("They are not late more often: ", ". There is no gap to explain."))
        headline = lead + measure["claim"].split(":")[0][0].lower() + measure["claim"].split(":")[0][1:] + tail
    else:
        headline = "No cause could be confirmed from the data."
    if untested:
        headline = f"{untested} {headline}"

    lines = ["## Root causes"]
    how = []   # the detail behind each line (medians, percentiles, counterfactuals), shown folded in the app
    if causes:
        for i, f in enumerate(causes, 1):
            lines.append(f"{i}. **{f['statement']}**{share_txt(f)}{held_txt(f)}. {_cites(f)}")
            how.append(f"- **{f['statement']}**{share_txt(f)}: {f['claim']} {_cites(f)}")
            if f["task_id"] in held_by:
                why = held_by[f["task_id"]]
                label = "Why dispatch slowed" if tool_of.get(why["task_id"]) == "stock_wait" else "Why they are late"
                how.append(f"   - {label}: {why['claim']} {_cites(why)}")
            for label, g in also.get(f["task_id"], []):
                lines.append(f"   - {label}{share_txt(g)}. {_cites(g)}")
                how.append(f"   - {label}{share_txt(g)}: {g['claim']} {_cites(g)}")
    else:
        lines.append("No cause passed the tests.")
    if impact:
        how.append(f"- **{impact['statement']}**: {impact['claim']} {_cites(impact)}")
    if where:
        lines += ["", "## Where it happened"] + [f"- {f['claim']} {_cites(f)}" for f in where]
    lines += ["", "## Ruled out"] + ([f"- **{f['statement']}**: {v[f['task_id']]['reason']} {_cites(f)}" for f in ruled]
                                     or ["- Nothing was ruled out."])
    if context or quotes:
        lines += ["", "## What customers and the written record say"]
        for f in context:
            lines.append(f"- {f['claim']} {_cites(f)}")
        for q in quotes[:4]:
            when = str(q.get("sent_at") or "")[:10]
            lines.append(f"- {q.get('title') or q.get('doc_id')}{f' ({when})' if when and when != 'None' else ''}: "
                         f"\"{q['quote']}\" [{q['id']}]")
    acts = state.get("actions", [])
    if acts:
        lines += ["", "## Recommended actions", "| Action | Protects | Value at stake | Est. cost | Approval |",
                  "|---|---|---|---|---|"]
        for a in acts:
            prot = f"{int(a['orders_protected']):,} orders" if isinstance(a.get("orders_protected"), (int, float)) and a.get("orders_protected") else "—"
            val = (_money(cur, a["value_protected_inr"]) if isinstance(a.get("value_protected_inr"), (int, float))
                   and a.get("value_protected_inr") else "—")
            if val != "—" and a.get("low_reviews"):
                val += f" ({a['low_reviews']:,} left a 1- or 2-star review)"
            cost = _money(cur, a["est_cost_inr"]) if isinstance(a.get("est_cost_inr"), (int, float)) else "not computed (no cost data)"
            appr = "needs approval: " + "; ".join(a.get("approval_reasons") or []) if a.get("needs_approval") else "within limits"
            lines.append(f"| {a['id']}. {a.get('title', '')} {''.join(f'[{e}]' for e in a.get('evidence', [])[:2])} | {prot} | {val} | {cost} | {appr} |")
    stage_causes = [f for f in causes if tasks.get(f["task_id"], {}).get("tool") == "stage"]
    stage_sh = [round(100 * v[f["task_id"]]["share_of_symptom"], 1) for f in stage_causes
                if isinstance(v[f["task_id"]].get("share_of_symptom"), float)]
    if supply:
        lines += ["", "## How we know"] + how + [
                  "- Each check reports the share of the short units it is involved in (for duplicates: the units "
                  "the extra copies order). Shares overlap: one SKU can be both late inbound and under a stale "
                  "reorder point.",
                  "- Every number above was computed by the analysis engine from the cited results, and checked "
                  "against their cells. Verdicts follow fixed rules (see the README), not a model's judgement."]
    else:
        lines += ["", "## How we know"] + how + [
              (f"- The confirmed stages ({' and '.join(f'{x}%' for x in stage_sh)}) together explain "
               f"{round(sum(stage_sh), 1)}% of the measured {word}"
               + ("; the rest is mix and interactions." if sum(stage_sh) <= 100 else
                  " (more than all of it: other effects, such as the mix of orders, offset part of it).")
               if len(stage_sh) > 1 else
               f"- The confirmed stage explains {stage_sh[0]}% of the measured {word}." if stage_sh else
               "- No single delivery stage explains the symptom in this scope."),
              "- Every number above was computed by the analysis engine from the cited results, and checked against "
              "their cells. Verdicts follow fixed rules (see the README), not a model's judgement."]
    body = "\n".join(lines)

    def _event_name(f):
        return f["statement"].split(" (")[0]

    if event_f or not measure:
        what = headline
    elif supply:
        what = f"{measure['claim']} {_cites(measure)}"
    else:
        what = measure["claim"].split(":")[0] + f". {_cites(measure)}"
    if impact and not (measure and not supply and _no_rise(state, measure)):
        what += f" {impact['claim']} {_cites(impact)}"
    if causes:
        parts = [cause_txt(f) for f in causes[:3]]
        why = (", ".join(parts[:-1]) + " and " + parts[-1]) if len(parts) > 1 else parts[0]
        why = why[0].upper() + why[1:] + "."
    elif measure and ((supply and _no_shortage(state, measure)) or (not supply and _no_rise(state, measure))):
        why = "Nothing to explain: the problem the question assumes isn't in the data."
    else:
        why = "No single cause passed the tests."
    ruled_events = [_event_name(f) for f in ruled if tasks.get(f["task_id"], {}).get("tool") == "event"][:3]
    if ruled_events and not event_f:
        why += " Ruled out: " + "; ".join(ruled_events) + "."
    todo = []
    for a in sorted(acts, key=lambda a: -(a.get("value_protected_inr") or 0))[:2]:
        bits = []
        if isinstance(a.get("orders_protected"), (int, float)) and a.get("orders_protected"):
            bits.append(f"{int(a['orders_protected']):,} orders")
        if isinstance(a.get("value_protected_inr"), (int, float)) and a.get("value_protected_inr"):
            bits.append(f"{_money(cur, a['value_protected_inr'])} of orders at stake"
                        + (f", {a['low_reviews']:,} with a 1- or 2-star review" if a.get("low_reviews") else ""))
        if isinstance(a.get("est_cost_inr"), (int, float)):
            bits.append(f"costs {_money(cur, a['est_cost_inr'])}")
        bits.append("needs approval" if a.get("needs_approval") else "within limits")
        todo.append(f"{a['id']}. {a.get('title', '').split(':')[0]} ({'; '.join(bits)})")
    todo_txt = " ".join(t + "." for t in todo) if todo else (
        "Nothing: there is no problem to fix." if why.startswith("Nothing to explain") else
        "No action could be sized from the data.")
    summary = f"- **What happened:** {what}\n- **Why:** {why}\n- **What to do:** {todo_txt}"
    if not preliminary:
        why_chain(state, ctx, causes, held_by, where, ruled, v)
        nxt = ask_next(state, ctx, causes, where, held_by) if not supply else []
        if nxt:
            body += "\n\n## Ask why next\n" + "\n".join(f"- {q} ({why})" for q, why in nxt)
    if preliminary:
        threshold = getattr(ctx.dataset, "approval_threshold", None) or ctx.settings.approval_threshold_inr
        md_p, head_p, chk = check_brief(f"## Summary\n{summary}", headline, state, acts, None,
                                        policy=[float(threshold)] if threshold else None)
        return {"headline": head_p, "summary": md_p.split("\n", 1)[1], "numbers_check": chk}

    if event_f:
        # the event test (by the week parcels were handed over) answers the question; the late rate of orders placed
        # in the event's window is context, and can even fall while the event delays parcels already in transit
        e = event_f[0]
        lead = [f"EVENT TEST (answers the question): {e['statement']}: {e['claim']} {_cites(e)}"]
        sym = ([f"CONTEXT, orders placed during the window (by purchase date, not the event test): {measure['claim']} "
                f"{_cites(measure)}"] if measure else [])
    else:
        lead, sym = [], ([f"SYMPTOM: {measure['claim']} {_cites(measure)}"] if measure else [])
    if untested:
        lead.append(f"NOTE: {untested} The answer should say so first.")
    facts = "\n".join([f"HEADLINE: {headline}"] + lead + sym
                      + [f"CAUSE: {f['statement']}{share_txt(f)}: {f['claim']} {_cites(f)}"
                         + (f" WHY: {held_by[f['task_id']]['claim']} {_cites(held_by[f['task_id']])}"
                            if f["task_id"] in held_by else "") for f in causes[:3]]
                      + [f"RULED OUT: {f['statement']}: {v[f['task_id']]['reason']}" for f in ruled[:5]]
                      + ([f"VALUE AT STAKE: {impact['claim']} {_cites(impact)}"] if impact else []))
    answer = ""
    try:
        res = ctx.llm.chat("work", ANSWER_PROMPT, f"QUESTION: {state['question']}\n\nFACTS:\n{facts}", agent=agent,
                           max_tokens=600, temperature=0.2)
        answer = re.sub(r"(?m)^#+.*$", "", res.text).strip()
    except LLMError as e:
        ctx.tracer.event("agent_error", agent, error=str(e)[:200])
    if len(answer) < 40:
        # no model answer (the model failed or returned nothing): the computed headline, said plainly
        answer = (headline + (f" {_cites(measure)}" if measure else "")
                  + " (Written by code: the language model was unavailable for this summary.)")
    md = f"## Summary\n{summary}\n\n## Answer\n{answer}\n\n{body}"
    # the answer may repeat rule thresholds and the numbers in the code-written ruled-out reasons it was given
    rules = [n.value for n in numbers_in(facts) if n.value in RULE_CONSTANTS] + \
        [n.value for f in ruled[:5] for n in numbers_in(str(v[f["task_id"]].get("reason", "")))]
    threshold = getattr(ctx.dataset, "approval_threshold", None) or ctx.settings.approval_threshold_inr
    md, headline, brief_check = check_brief(md, headline, state, acts, ctx, answer_rules=rules,
                                            policy=[float(threshold)] if threshold else None)
    ctx.tracer.event("agent_end", agent, headline=headline, invalid_citations_removed=[])
    return {"brief": md, "headline": headline, "lessons": [], "brief_check": brief_check}


def report(state: dict, ctx: Ctx) -> dict:
    agent = "Reporter"
    ctx.tracer.event("agent_start", agent)
    if state.get("analysis"):
        return report_battery(state, ctx)
    v = state.get("verdicts", {})
    currency = getattr(ctx.dataset, "currency", "INR")
    actions = json.dumps([{"id": a.get("id"), "title": a.get("title"), "steps": a.get("steps"),
                           "orders_protected": a.get("orders_protected"),
                           f"value_protected_{currency}": a.get("value_protected_inr"),
                           f"est_cost_{currency}": a.get("est_cost_inr"), "time_to_effect": a.get("time_to_effect"),
                           "needs_approval": a.get("needs_approval"), "approval_reasons": a.get("approval_reasons"),
                           "evidence": a.get("evidence")} for a in state.get("actions", [])], default=str)
    last = state["evaluations"][-1] if state.get("evaluations") else {}
    user = (base_context(state, ctx) + f"\n\nSYMPTOM: {state.get('symptom')}\n\nALL FINDINGS:\n"
            f"{findings_text(state['findings'], state['evidence'], v, 6)}\n\nACTIONS:\n{actions}\n\n"
            f"RISKS: {json.dumps(state.get('risks', []), default=str)}\n\n"
            f"EVALUATOR'S FINAL VIEW: explained={last.get('symptom_explained')}, {last.get('summary')}\n\n"
            f"Money amounts are in {currency}.")
    out = {"headline": "Report generation failed", "brief_markdown": "", "lessons": []}
    # plain markdown, not JSON: long markdown inside a JSON string is where models slip. Writing needs no deep
    # reasoning, so the fast role goes first; a too-short brief is retried once with the reasoning role.
    for role in ("work", "reason"):
        try:
            res = ctx.llm.chat(role, P.REPORTER, user, agent=agent, max_tokens=4500, temperature=0.3)
        except LLMError as e:
            out["brief_markdown"] = f"Reporter unavailable: {e}"
            continue
        out = parse_brief(res.text)
        if len(out["brief_markdown"]) >= 400 and "## " in out["brief_markdown"]:
            break
        ctx.tracer.event("report_retry", agent, chars=len(out["brief_markdown"]), provider=res.provider)
    if not str(out.get("brief_markdown", "")).lstrip().startswith("#") and out.get("headline") == "Report generation failed":
        # the model couldn't write the brief (e.g. the run's time limit): say so, and list what was confirmed
        why = str(out.get("brief_markdown", "")).replace("Reporter unavailable: ", "").strip() or "no reply"
        conf = [f for f in state["findings"] if v.get(f["task_id"], {}).get("status") == "confirmed"]
        out["headline"] = (f"Incomplete: the brief couldn't be written ({why[:120]}). "
                           + (f"{len(conf)} findings were confirmed; they are listed below." if conf
                              else "No finding was confirmed."))
        out["brief_markdown"] = ("## Confirmed findings\n" + "\n".join(
            f"- **{f['statement']}**: {f['claim']} {_cites(f)}" for f in conf)) if conf else ""
    md, raw_headline = tidy_brief(str(out.get("brief_markdown", "")), str(out.get("headline", "")))
    out["headline"] = raw_headline
    # a bare task citation like [T2] becomes that task's evidence ids, so it stays clickable
    ev_by_task = {f["task_id"]: f["evidence_ids"] for f in state["findings"]}
    md = re.sub(r"\[(T\d+)\]", lambda m: "".join(f"[{e}]" for e in ev_by_task.get(m.group(1), [])[:2]) or m.group(0), md)
    known = set(state["evidence"])
    bad = sorted({c for c in re.findall(r"\[([A-Z][A-Za-z0-9]*\.[QDPA]\d+)\]", md) if c not in known})
    for c in bad:
        md = md.replace(f"[{c}]", "[citation removed]")
    headline = re.sub(r"\s*\[[^\]]*\]", "", str(out.get("headline", ""))).strip()
    md, headline, brief_check = check_brief(md, headline, state, state.get("actions", []), ctx)
    ctx.tracer.event("agent_end", agent, headline=headline, invalid_citations_removed=bad)
    return {"brief": md, "headline": headline, "lessons": out.get("lessons", []) or [], "brief_check": brief_check}


def remember(state: dict, ctx: Ctx) -> dict:
    agent = "Memory"
    run_id = ctx.tracer.run_id
    v = state.get("verdicts", {})
    ds_tags = [ctx.dataset.key] if getattr(ctx.dataset, "key", None) else []
    n = 0
    for f in state["findings"]:
        st = v.get(f["task_id"], {}).get("status")
        if st not in ("confirmed", "rejected"):
            continue
        ev = [state["evidence"][e] for e in f["evidence_ids"] if e in state["evidence"]]
        prov = {"source": "zetaris" if ctx.harness.name == "zetaris" else ctx.harness.name,
                "query": ev[0].get("sql") if ev and ev[0]["type"] == "sql" else None,
                "doc": ev[0].get("doc_id") if ev and ev[0]["type"] == "doc" else None,
                "evidence_ids": f["evidence_ids"], "question": state["question"]}
        is_cause = v.get(f["task_id"], {}).get("is_cause", True)
        if f.get("context_only"):
            # private documents: memory recall embeds text on NVIDIA's cloud, so the record's quotes stay out of it
            if getattr(ctx.llm, "private_docs", False):
                continue
            # the written record, as context: keep only what was verified (the quotes), not the model's summary
            quotes = [e for e in ev if e.get("type") == "doc" and e.get("quote")]
            if not quotes:
                continue
            text = f"WRITTEN RECORD ({ctx.as_of}): " + " | ".join(
                f"{q.get('title') or q.get('doc_id')}" + (f" ({str(q.get('sent_at'))[:10]})" if q.get("sent_at") else "")
                + f": \"{q['quote']}\"" for q in quotes[:4])
            ctx.memory.write(run_id, agent, "fact", text, prov, float(f.get("confidence") or 0.6),
                             ["investigation", "written record"] + ds_tags)
            n += 1
            continue
        label = ("MEASURED" if f["type"] == "measure" else "CONFIRMED CAUSE" if st == "confirmed" and is_cause
                 else "CONFIRMED" if st == "confirmed" else "RULED OUT")
        text = f"{label} ({ctx.as_of}): {f['statement']} -> {f['claim']}"
        ctx.memory.write(run_id, agent, "finding" if label == "CONFIRMED CAUSE" else "fact", text, prov,
                         float(f.get("confidence") or 0.6), ["investigation", st] + ds_tags)
        n += 1
    for lesson in state.get("lessons", []):
        ctx.memory.write(run_id, "Reporter", "lesson", str(lesson), {"source": "reporter", "run_id": run_id},
                         0.7, ["lesson"] + ds_tags)
        n += 1
    cons = ctx.memory.consolidate(run_id)
    ctx.tracer.event("memory_write", agent, items=n, consolidation=cons)
    return {}


CODE = re.compile(r"\b(S-\d{2}|PO-\d{4,}|SO-\d{5,}|C0\d|(?:MUM|PUN|CHN)-[A-Z]{3}|MUM|PUN|CHN|CU-\d{4}|"
                  r"[A-Z]{3}-\d{4})\b")


def entity_codes(text: str) -> set[str]:
    return set(CODE.findall(text or ""))


def entity(code: str) -> dict:
    kind = ("supplier" if code.startswith("S-") else "purchase_order" if code.startswith("PO-") else
            "sales_order" if code.startswith("SO-") else "carrier" if re.fullmatch(r"C0\d", code) else
            "customer" if code.startswith("CU-") else "lane" if re.fullmatch(r"[A-Z]{3}-[A-Z]{3}", code) else
            "warehouse" if code in ("MUM", "PUN", "CHN") else "sku")
    return {"type": kind, "key": code, "name": code}


APPROVAL_NEEDED = re.compile(r"\b(requires?|required|needs?|needed|subject to|must get|must have)\b[^.;]{0,50}"
                             r"\b(approval|sign-?off|authori[sz]ation)\b", re.I)
APPROVAL_NOT_NEEDED = re.compile(r"\b(no|not|without|within)\b[^.;]{0,30}\b(approval|sign-?off|limits?)\b", re.I)


def policy_requires_approval(notes: str) -> bool:
    """True when the agent's policy note says approval IS needed ("requires sign-off from ..."),
    not when it merely mentions approval ("within limits, no approval needed")."""
    return bool(APPROVAL_NEEDED.search(notes)) and not APPROVAL_NOT_NEEDED.search(notes)


def _num(v, default: float = 0.0) -> float:
    try:
        if isinstance(v, str):
            v = re.sub(r"[^\d.\-]", "", v)
        return float(v)
    except (TypeError, ValueError):
        return default
