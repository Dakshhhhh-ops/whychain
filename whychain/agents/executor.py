"""The act-observe loop shared by investigators, the document analyst and the risk agent.

Each step the model picks an action (run SQL through the harness, read a document, or finish).
Results become evidence with stable ids (T3.Q2 = task T3, query 2) that later agents must cite.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from whychain.agents.prompts import SQL_RULES
from whychain.harness.base import DataHarness
from whychain.llm import LLM, LLMError
from whychain.trace import Tracer

DOC_ID_OK = re.compile(r"^[A-Za-z0-9_\-.]+$")


@dataclass
class Ctx:
    llm: LLM
    harness: DataHarness
    tracer: Tracer
    memory: object
    settings: object
    as_of: str
    catalog_text: str = ""
    dataset: object = None  # whychain.datasets.Dataset; None in unit tests (treated as the simulated distributor)
    fixed_analyses: bool = True  # False: every question goes through the open-ended agent loop (agent-loop tests)
    precomputed: dict = field(default_factory=dict)  # task id -> result of a computed test already run (round 1)
    route: str | None = None   # battery | supply | agent: which part answers (Meterless Scout, or the word rules)


@dataclass
class LoopResult:
    final: dict
    evidence: dict = field(default_factory=dict)
    steps: int = 0
    sql_errors_recovered: int = 0
    docs_read: dict = field(default_factory=dict)


def fill(prompt: str, max_steps: int) -> str:
    return prompt.replace("{max_steps}", str(max_steps)).replace("{sql_rules}", SQL_RULES)


ALIASES = {"optimise": "optimize", "optimize_rebalance": "optimize", "rebalance": "optimize", "sql": "query", "run_sql": "query", "execute_sql": "query", "run_query": "query", "query_sql": "query",
           "read": "read_doc", "read_document": "read_doc", "open_doc": "read_doc", "final": "finish",
           "done": "finish", "answer": "finish", "conclude": "finish", "final_answer": "finish", "complete": "finish"}
MAX_INVALID = 2  # malformed replies that don't use up the step budget


def normalize_action(msg: dict) -> str:
    """Map near-miss action names to the real ones, and infer the action from the fields when it's missing."""
    a = ALIASES.get(str(msg.get("action") or "").strip().lower(), str(msg.get("action") or "").strip().lower())
    if a in ("query", "search") and not msg.get("sql"):
        # some replies put the SQL under "query" instead of "sql"
        q = msg.get("query") or msg.get("statement")
        if isinstance(q, str) and q.strip().lower().startswith(("select", "with")):
            msg["sql"] = q
    if a not in ("query", "read_doc", "finish", "optimize"):
        if msg.get("sql"):
            a = "query"
        elif msg.get("scope"):
            a = "optimize"
        elif msg.get("doc_id"):
            a = "read_doc"
        elif any(k in msg for k in ("verdict", "claim", "actions", "deliverables", "quotes")):
            a = "finish"
    return a


def run_loop(ctx: Ctx, *, agent: str, role: str, system: str, brief: str, task_id: str,
             max_steps: int = 6, allow_docs: bool = False, allow_optimize: bool = False,
             finish_keys: tuple[str, ...] = ("verdict", "claim")) -> LoopResult:
    res = LoopResult(final={})
    transcript: list[str] = []
    q_n = 0
    o_n = 0
    pending_error = False
    invalid = 0
    step = 0
    while True:
        step += 1
        used = step - invalid
        last_call = used > max_steps
        work = "\n\n".join(transcript) if transcript else "(nothing yet)"
        left = max_steps - used
        footer = ("You have used all your steps. Reply now with action \"finish\" and every field it needs."
                  if last_call else f"Step {used} of {max_steps}. Reply with your next JSON action."
                  + (f" Only {left + 1} step(s) left: finish as soon as you have enough evidence." if left <= 1 else ""))
        user = f"{brief}\n\n--- Work so far ---\n{work}\n\n{footer}"
        try:
            msg = ctx.llm.json(role, system, user, agent=agent)
        except LLMError as e:
            ctx.tracer.event("agent_error", agent, task_id=task_id, error=str(e)[:300])
            res.final = {"action": "finish", "verdict": "inconclusive", "claim": f"Agent failed: {e}",
                         "confidence": 0.0}
            break
        action = normalize_action(msg)
        thought = str(msg.get("thought", ""))[:400]
        if action not in ("query", "read_doc", "finish", "optimize") and thought and not last_call:
            # A reply with only a plan ("I'll search for X") and no action: ask for the action that does it, once.
            nudge = (f"{user}\n\nYour last reply only described a plan: \"{thought[:300]}\". Now reply with the JSON "
                     f"action that carries it out (include \"action\" and every field it needs, e.g. \"sql\").")
            try:
                retry_msg = ctx.llm.json(role, system, nudge, agent=agent)
                if normalize_action(retry_msg) in ("query", "read_doc", "finish", "optimize"):
                    ctx.tracer.event("action_repaired", agent, task_id=task_id)
                    msg, action = retry_msg, normalize_action(retry_msg)
                    thought = str(msg.get("thought", thought))[:400]
            except LLMError:
                pass
        if action == "finish" or last_call:
            res.final = msg
            res.steps = used
            break
        if action == "query" and msg.get("sql"):
            q_n += 1
            eid = f"{task_id}.Q{q_n}"
            r = ctx.harness.query(str(msg["sql"]), limit=50)
            ctx.tracer.event("query", agent, task_id=task_id, evidence_id=eid if r.ok else None, thought=thought,
                             sql=r.sql, ok=r.ok, rows=r.row_count, ms=r.elapsed_ms, harness=r.harness,
                             error=r.error)
            if r.ok:
                if pending_error:
                    res.sql_errors_recovered += 1
                pending_error = False
                res.evidence[eid] = {"id": eid, "type": "sql", "task_id": task_id, "agent": agent,
                                     "purpose": thought, "sql": r.sql, "columns": r.columns, "rows": r.rows[:30],
                                     "row_count": r.row_count, "truncated": r.truncated, "harness": r.harness,
                                     "ms": r.elapsed_ms}
                obs = f"[{eid}] {r.row_count}{'+' if r.truncated else ''} rows\n{r.preview(25)}"
            else:
                pending_error = True
                obs = f"SQL ERROR (not saved as evidence): {r.error}\nFix the SQL and try again."
            transcript.append(f"Step {step}: {json.dumps({'thought': thought, 'action': 'query', 'sql': msg['sql']})}"
                              f"\nResult: {obs}")
        elif action == "optimize" and allow_optimize and isinstance(msg.get("scope"), dict):
            o_n += 1
            eid = f"{task_id}.P{o_n}"
            obs = _optimize_step(ctx, msg, eid, task_id, agent, thought, res)
            shown = {k: msg.get(k) for k in ("thought", "action", "scope", "to_warehouses", "deadline_days")}
            transcript.append(f"Step {step}: {json.dumps(shown)}\nResult: {obs}")
        elif action == "read_doc" and allow_docs and msg.get("doc_id"):
            doc_id = str(msg["doc_id"]).strip()
            doc = read_doc(ctx, doc_id) if DOC_ID_OK.match(doc_id) else None
            ctx.tracer.event("read_doc", agent, task_id=task_id, doc_id=doc_id, found=bool(doc), thought=thought)
            if doc:
                res.docs_read[doc_id] = doc
                obs = (f"doc_id={doc_id} | {doc.get('doc_type')} | {doc.get('title')} | sent_at={doc.get('sent_at')} "
                       f"| from={doc.get('sender')} | to={doc.get('recipients')}\n{str(doc.get('body', ''))[:5000]}")
            else:
                obs = f"No document with doc_id '{doc_id}'. Search docs.documents first."
            transcript.append(f"Step {step}: {json.dumps({'thought': thought, 'action': 'read_doc', 'doc_id': doc_id})}"
                              f"\nResult: {obs}")
        else:
            invalid += 1
            ctx.tracer.event("invalid_action", agent, task_id=task_id, reply=json.dumps(msg)[:300])
            allowed = "query" + (", read_doc" if allow_docs else "") + (", optimize" if allow_optimize else "")
            transcript.append(f"Step {step}: {json.dumps(msg)[:300]}\nResult: invalid action. Use {allowed} or finish.")
            if invalid > MAX_INVALID:  # stop being lenient: these now count against the budget
                invalid = MAX_INVALID
    if res.final.get("verdict") != "inconclusive" or not str(res.final.get("claim", "")).startswith("Agent failed"):
        if not all(res.final.get(k) for k in finish_keys):
            # the final reply is missing what this agent must return (e.g. no "actions"): ask once, explicitly
            ctx.tracer.event("finish_repair", agent, task_id=task_id, missing=[k for k in finish_keys if not res.final.get(k)])
            work = "\n\n".join(transcript) if transcript else "(nothing yet)"
            ask = (f"{brief}\n\n--- Work so far ---\n{work}\n\nStop investigating. Reply now with ONE JSON object with "
                   f"\"action\": \"finish\" and these fields filled in from the work above: {', '.join(finish_keys)}.")
            try:
                again = ctx.llm.json(role, system, ask, agent=agent)
                if all(again.get(k) for k in finish_keys):
                    res.final = again
            except LLMError:
                pass
    ctx.tracer.event("agent_finish", agent, task_id=task_id, steps=used,
                     keys=sorted(k for k in res.final.keys() if k not in ("thought",)),
                     items=len(res.final.get("actions") or res.final.get("deliverables") or []))
    # keep only evidence ids that exist
    cited = msg_ids(res.final.get("evidence"))
    res.final["evidence"] = [e for e in cited if e in res.evidence]
    return res


def _optimize_step(ctx: Ctx, msg: dict, eid: str, task_id: str, agent: str, thought: str, res: LoopResult) -> str:
    """Run the Optiagent-backed optimizer. The agent supplies only the scope; the data comes from the harness."""
    from whychain.optimize import OptimizeError, describe, optimize_rebalance

    args = {"scope": msg.get("scope"), "to_warehouses": msg.get("to_warehouses"),
            "deadline_days": int(msg.get("deadline_days") or 7), "cover_days": int(msg.get("cover_days") or 14)}
    try:
        out = optimize_rebalance(ctx.harness, **args)
    except (OptimizeError, ValueError, TypeError) as e:
        ctx.tracer.event("optimize", agent, task_id=task_id, evidence_id=None, ok=False, error=str(e)[:300], **args)
        return f"OPTIMIZE ERROR (not saved as evidence): {e}"
    except Exception as e:  # the optimizer must never take the whole investigation down
        ctx.tracer.event("optimize", agent, task_id=task_id, evidence_id=None, ok=False,
                         error=f"{type(e).__name__}: {str(e)[:250]}", **args)
        return f"OPTIMIZER UNAVAILABLE ({type(e).__name__}). Estimate the plan from queries instead."
    s = out.get("summary", {})
    ok = bool(out.get("verified")) and out.get("outcome") == "verified_optimal"
    ctx.tracer.event("optimize", agent, task_id=task_id, evidence_id=eid if ok else None, ok=ok,
                     outcome=out.get("outcome"), certified_with=out.get("certified_with"),
                     need_units=s.get("need_units"), truck_units=s.get("truck_units"), air_units=s.get("air_units"),
                     unfilled_units=s.get("unfilled_units"), objective_inr=out.get("objective_inr"), thought=thought, **args)
    if ok:
        res.evidence[eid] = {"id": eid, "type": "optimization", "task_id": task_id, "agent": agent, "purpose": thought,
                             **out, "row_count": len(out.get("rows", [])), "harness": ctx.harness.name}
        return f"[{eid}] " + describe(out)
    return describe(out)


def read_doc(ctx: Ctx, doc_id: str) -> dict | None:
    r = ctx.harness.query("SELECT doc_id, doc_type, title, sent_at, sender, recipients, body FROM docs.documents "
                          f"WHERE doc_id = '{doc_id}'", limit=1)
    if not r.ok or not r.rows:
        return None
    return dict(zip(r.columns, r.rows[0]))


def msg_ids(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        v = re.findall(r"[A-Z][A-Za-z0-9]*\.[QDPA]\d+", v)
    return [str(x).strip("[] ") for x in v]


def _norm(s: str) -> str:
    s = re.sub(r"[*_`|#>]", "", s.lower())
    s = s.replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()


def verify_quotes(ctx: Ctx, task_id: str, agent: str, quotes, docs_read: dict) -> tuple[dict, int]:
    """Keep only quotes that appear verbatim in the cited document. Returns (evidence, rejected_count)."""
    evidence, rejected, k = {}, 0, 0
    for q in quotes or []:
        if not isinstance(q, dict):
            continue
        doc_id, quote = str(q.get("doc_id", "")).strip(), str(q.get("quote", "")).strip()
        if not doc_id or len(quote) < 12:
            rejected += 1
            continue
        doc = docs_read.get(doc_id) or (read_doc(ctx, doc_id) if DOC_ID_OK.match(doc_id) else None)
        if doc and _norm(quote) in _norm(str(doc.get("body", "")) + " " + str(doc.get("title", ""))):
            k += 1
            eid = f"{task_id}.D{k}"
            evidence[eid] = {"id": eid, "type": "doc", "task_id": task_id, "agent": agent, "doc_id": doc_id,
                             "title": doc.get("title"), "doc_type": doc.get("doc_type"),
                             "sent_at": doc.get("sent_at"), "sender": doc.get("sender"),
                             "recipients": doc.get("recipients"), "quote": quote}
        else:
            rejected += 1
            ctx.tracer.event("quote_rejected", agent, task_id=task_id, doc_id=doc_id, quote=quote[:200])
    return evidence, rejected
