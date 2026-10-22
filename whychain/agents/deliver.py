"""The Action Executor: once a manager approves an action, produce the deliverables that carry it out.

Tables (transfer orders, cancellation lists, parameter changes) must be the result of one of the
agent's own queries, which is re-run in full; email drafts are free text grounded in the evidence.
"""
from __future__ import annotations

import json

from whychain.agents import prompts as P
from whychain.agents.executor import Ctx, fill, run_loop
from whychain.agents.nodes import data_map_text, evidence_text, findings_text


def deliver(ctx: Ctx, result: dict, action: dict, note: str | None = None) -> dict:
    agent = f"Action Executor {action['id']}"
    ctx.tracer.event("agent_start", agent, action_id=action["id"], title=action.get("title"),
                     note="Preparing the deliverables for the approved action")
    v = result.get("verdicts", {})
    causes = [f for f in result.get("findings", [])
              if v.get(f["task_id"], {}).get("status") == "confirmed" and v[f["task_id"]].get("is_cause", True)]
    ev_all = result.get("evidence", {})
    action_ev = "\n".join(evidence_text(ev_all[e], 10) for e in action.get("evidence", []) if e in ev_all)
    brief = (f"QUESTION: {result.get('question')}\nTODAY: {ctx.as_of}\n\n"
             f"APPROVED ACTION [{action['id']}]: {action.get('title')}\nSteps: {action.get('steps')}\n"
             f"Policy notes: {action.get('policy_notes') or '-'}\nManager's note: {note or '-'}\n\n"
             f"CONFIRMED CAUSES:\n{findings_text(causes, ev_all, v, 6)}\n\n"
             f"EVIDENCE BEHIND THE ACTION:\n{action_ev or '(none cited)'}\n\n"
             f"DATA MAP:\n{data_map_text(result.get('data_map', {}))}\n\nCATALOG:\n{ctx.catalog_text}")
    optimizer = getattr(ctx.dataset, "optimizer", True)
    res = run_loop(ctx, agent=agent, role="work", system=fill(P.executor_prompt(optimizer), 6), brief=brief,
                   task_id="X", max_steps=6, allow_docs=True, allow_optimize=optimizer, finish_keys=("deliverables",))
    f = res.final
    out, rejected = [], []
    for d in f.get("deliverables", []) or []:
        if not isinstance(d, dict):
            continue
        kind = str(d.get("kind", "")).lower()
        title = str(d.get("title") or kind.title())
        if kind == "table":
            ref = str(d.get("from_query", "")).strip("[] ")
            ev = res.evidence.get(ref)
            if not ev:
                rejected.append({"title": title, "reason": f"from_query '{ref}' is not one of the agent's queries"})
                continue
            if ev["type"] == "optimization":  # the verified optimal plan is the table
                out.append({"kind": "table", "title": title, "note": d.get("note"), "source": ref,
                            "sql": "Optimal plan from Optiagent (verified, optimality certificate). Inputs: "
                                   + "; ".join(ev["queries"].values()),
                            "columns": ev["columns"], "rows": ev["rows"], "row_count": len(ev["rows"]),
                            "harness": ev["harness"],
                            "optimization": {k: ev.get(k) for k in ("summary", "objective_inr", "certified_with", "notes")}})
                continue
            full = ctx.harness.query(ev["sql"], limit=1000)
            if not full.ok or not full.rows:
                rejected.append({"title": title, "reason": full.error or "query returned no rows"})
                continue
            out.append({"kind": "table", "title": title, "note": d.get("note"), "source": ref, "sql": ev["sql"],
                        "columns": full.columns, "rows": full.rows, "row_count": full.row_count,
                        "harness": full.harness})
        elif kind == "email":
            out.append({"kind": "email", "title": title, "to": str(d.get("to", "")), "subject": str(d.get("subject", "")),
                        "body": str(d.get("body", ""))})
        elif d.get("body"):
            out.append({"kind": "document", "title": title, "body": str(d["body"])})
    for r in rejected:
        ctx.tracer.event("deliverable_rejected", agent, **r)
    ctx.tracer.event("deliverables", agent, count=len(out), kinds=[d["kind"] for d in out], rejected=len(rejected))
    ctx.memory.write(ctx.tracer.run_id, agent, "fact",
                     f"Executed approved action '{action.get('title')}' for '{result.get('question')}': "
                     f"{f.get('summary') or ', '.join(d['title'] for d in out)}",
                     {"source": "action-executor", "action_id": action["id"]}, 0.85, ["action", "executed"])
    payload = {"action_id": action["id"], "status": "done", "summary": f.get("summary", ""), "deliverables": out,
               "rejected": rejected, "evidence": res.evidence, "steps": res.steps}
    ctx.tracer.save("deliverables.json", payload)
    return json.loads(json.dumps(payload, default=str))
