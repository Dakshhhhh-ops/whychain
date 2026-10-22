"""Agent nodes. Each one reads the shared state, does its job, and returns its part of the state."""
from __future__ import annotations

import hashlib
import json
import re

from whychain.agents import prompts as P
from whychain.agents.executor import Ctx, fill, run_loop, verify_quotes
from whychain.harness.base import catalog_text
from whychain.llm import LLMError

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
    return "\n".join(f"- [{m.get('kind', '?')}, conf {m.get('confidence', '?')}] {m.get('content', '')[:300]}"
                     for m in mems)


def evidence_text(ev: dict, max_rows: int = 8) -> str:
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


def findings_text(findings: list[dict], evidence: dict, verdicts: dict | None = None, rows: int = 8) -> str:
    out = []
    for f in findings:
        v = (verdicts or {}).get(f["task_id"])
        vtxt = f"\n  Evaluator: {v['status']} ({v.get('reason', '')})" if v else ""
        out.append(f"[{f['task_id']}] ({f['type']}, {f['agent_kind']}) {f['statement']}\n"
                   f"  Investigator verdict: {f['verdict']} (confidence {f.get('confidence')}): {f['claim']}"
                   f"{vtxt}")
        for eid in f["evidence_ids"]:
            if eid in evidence:
                out.append("  " + evidence_text(evidence[eid], rows).replace("\n", "\n  "))
        if f.get("quotes_rejected"):
            out.append(f"  ({f['quotes_rejected']} quote(s) failed verbatim verification and were discarded)")
    return "\n".join(out)


def base_context(state: dict, ctx: Ctx) -> str:
    return (f"QUESTION: {state['question']}\nTODAY: {ctx.as_of} (\"next week\" = the 7 days after today)\n\n"
            f"DATA MAP (from the Data Scout):\n{data_map_text(state.get('data_map', {}))}\n\n"
            f"RELEVANT MEMORY (from earlier investigations, Meterless H-MEM):\n"
            f"{memories_text(state.get('memories', []))}")


# ============================================================ nodes
def recall(state: dict, ctx: Ctx) -> dict:
    agent = "Memory"
    ctx.tracer.event("agent_start", agent, note="Recalling earlier findings and the cached data map")
    ds_tag = [ctx.dataset.key] if getattr(ctx.dataset, "key", None) else None
    mems = [m for m in ctx.memory.recall(state["question"], k=8, kinds=["finding", "fact", "lesson"],
                                         exclude_run_id=ctx.tracer.run_id, tags=ds_tag)
            if "data_map" not in (m.get("tags") or [])]
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


def plan(state: dict, ctx: Ctx) -> dict:
    agent = "Planner"
    rnd = state.get("round", 0) + 1
    ctx.tracer.event("agent_start", agent, round=rnd)
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
    if rnd == 1 and new and not any(t["agent"] == "docs" for t in new):
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


def investigate(payload: dict, ctx: Ctx) -> dict:
    """Runs one task. Fanned out in parallel, one call per task."""
    t = payload["task"]
    docs = t["agent"] == "docs"
    agent = f"{'Doc Analyst' if docs else 'Investigator'} {t['id']}"
    ctx.tracer.event("agent_start", agent, task_id=t["id"], statement=t["statement"])
    brief = (base_context(payload, ctx) + f"\n\nYOUR TASK [{t['id']}] ({t['type']}): {t['statement']}\n"
             f"Checks to make: {t.get('checks')}\n\nCATALOG:\n{ctx.catalog_text}")
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
               "round": t["round"]}
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
    # 2) skeptical review
    earlier = [f for f in state["findings"] if f["round"] < rnd]
    user = (base_context(state, ctx) + f"\n\nSYMPTOM: {state.get('symptom')}\n\n"
            f"EARLIER FINDINGS (already judged):\n{findings_text(earlier, evidence, state.get('verdicts'), 4) or '(none)'}"
            f"\n\nNEW FINDINGS TO JUDGE (round {rnd}):\n{findings_text(this_round, evidence)}")
    try:
        out = ctx.llm.json("reason", P.EVALUATOR, user, agent=agent, max_tokens=3000)
    except LLMError as e:
        ctx.tracer.event("agent_error", agent, error=str(e)[:300])
        out = {"verdicts": [], "decision": "accept", "summary": f"Evaluator unavailable: {e}"}
    verdicts = {}
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
            verdicts[v["task_id"]] = {"status": status, "reason": v.get("reason", ""),
                                      "is_cause": bool(is_cause), "share_of_symptom": v.get("share_of_symptom"),
                                      "round": rnd}
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


def act(state: dict, ctx: Ctx) -> dict:
    agent = "Risk & Action"
    confirmed = [f for f in state["findings"]
                 if state["verdicts"].get(f["task_id"], {}).get("status") == "confirmed"
                 and state["verdicts"][f["task_id"]].get("is_cause", True)]
    if not confirmed:
        ctx.tracer.event("agent_skip", agent, note="No confirmed causes; no actions proposed")
        return {"actions": [], "risks": []}
    ctx.tracer.event("agent_start", agent, causes=[f["task_id"] for f in confirmed])
    optimizer = getattr(ctx.dataset, "optimizer", True)
    currency = getattr(ctx.dataset, "currency", "INR")
    threshold = getattr(ctx.dataset, "approval_threshold", ctx.settings.approval_threshold_inr)
    brief = (base_context(state, ctx) + f"\n\nCONFIRMED CAUSES:\n{findings_text(confirmed, state['evidence'])}"
             f"\n\nApproval threshold: actions costing more than {currency} {threshold:,.0f} "
             f"need sign-off from the head of operations.\n\nCATALOG:\n{ctx.catalog_text}")
    res = run_loop(ctx, agent=agent, role="work", system=fill(P.risk_prompt(optimizer, currency), 8), brief=brief,
                   task_id="A", max_steps=8, allow_docs=True, allow_optimize=optimizer, finish_keys=("actions",))
    f = res.final
    actions = []
    for i, a in enumerate(f.get("actions", []) or [], 1):
        if not isinstance(a, dict):
            continue
        cost = _num(a.get("est_cost", a.get("est_cost_inr")))
        conf = _num(a.get("confidence"), 0.5)
        reasons = []
        if cost > threshold:
            reasons.append(f"cost {currency} {cost:,.0f} exceeds the {currency} {threshold:,.0f} threshold")
        if conf < 0.6:
            reasons.append(f"confidence {conf:.2f} is below 0.6")
        if policy_requires_approval(str(a.get("policy_notes", ""))):
            reasons.append("policy requires approval")
        ids = [e for e in (a.get("evidence") or []) if e in res.evidence or e in state["evidence"]]
        act_ = {**a, "id": f"A{i}", "est_cost_inr": cost, "currency": currency, "confidence": conf, "evidence": ids,
                "value_protected_inr": _num(a.get("value_protected", a.get("value_protected_inr"))),
                "needs_approval": bool(reasons), "approval_reasons": reasons, "decision": "pending"}
        actions.append(act_)
        if reasons:
            ctx.tracer.event("escalation", agent, action_id=act_["id"], title=a.get("title"), reasons=reasons)
    ctx.tracer.event("agent_end", agent, actions=[(a["id"], a.get("title"), a["needs_approval"]) for a in actions])
    return {"actions": actions, "risks": f.get("risks", []) or [], "evidence": res.evidence}


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


def report(state: dict, ctx: Ctx) -> dict:
    agent = "Reporter"
    ctx.tracer.event("agent_start", agent)
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
    try:
        # writing up needs no deep reasoning, and the fast JSON mode keeps the output well-formed
        out = ctx.llm.json("work", P.REPORTER, user, agent=agent, max_tokens=4500, temperature=0.3)
    except LLMError as e:
        out = {"headline": "Report generation failed", "brief_markdown": f"Reporter unavailable: {e}", "lessons": []}
    md, raw_headline = tidy_brief(str(out.get("brief_markdown", "")), str(out.get("headline", "")))
    out["headline"] = raw_headline
    # a bare task citation like [T2] becomes that task's evidence ids, so it stays clickable
    ev_by_task = {f["task_id"]: f["evidence_ids"] for f in state["findings"]}
    md = re.sub(r"\[(T\d+)\]", lambda m: "".join(f"[{e}]" for e in ev_by_task.get(m.group(1), [])[:2]) or m.group(0), md)
    known = set(state["evidence"])
    bad = sorted({c for c in re.findall(r"\[([A-Z][A-Za-z0-9]*\.[QDP]\d+)\]", md) if c not in known})
    for c in bad:
        md = md.replace(f"[{c}]", "[citation removed]")
    headline = re.sub(r"\s*\[[^\]]*\]", "", str(out.get("headline", ""))).strip()
    ctx.tracer.event("agent_end", agent, headline=headline, invalid_citations_removed=bad)
    return {"brief": md, "headline": headline, "lessons": out.get("lessons", []) or []}


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
