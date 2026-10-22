"""The investigation graph (LangGraph).

  recall -> scout -> plan -> [investigate x N, in parallel] -> evaluate --revise--> plan
                                                                  |
                                                               accept
                                                                  v
                                                     act (risk & escalation) -> report -> remember
"""
from __future__ import annotations

import operator
import re
import os
import time
import traceback
from pathlib import Path
from datetime import date
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from whychain.agents import nodes
from whychain.agents.executor import Ctx
from whychain.config import AS_OF, SETTINGS  # noqa: F401  (AS_OF re-exported)
from whychain.harness import catalog_text, get_harness
from whychain.llm import LLM
from whychain.memory import Memory
from whychain.trace import Tracer



def _merge(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class State(TypedDict, total=False):
    question: str
    round: int
    data_map: dict
    memories: list
    symptom: str
    tasks: Annotated[list, operator.add]
    open_tasks: list
    findings: Annotated[list, operator.add]
    evidence: Annotated[dict, _merge]
    evaluations: Annotated[list, operator.add]
    verdicts: Annotated[dict, _merge]
    actions: list
    risks: list
    brief: str
    headline: str
    lessons: list
    brief_check: dict
    analysis: dict


def build_graph(ctx: Ctx):
    g = StateGraph(State)
    g.add_node("recall", lambda s: nodes.recall(s, ctx))
    g.add_node("scout", lambda s: nodes.scout(s, ctx))
    g.add_node("plan", lambda s: nodes.plan(s, ctx))
    g.add_node("investigate", lambda p: nodes.investigate(p, ctx))
    g.add_node("evaluate", lambda s: nodes.evaluate(s, ctx))
    g.add_node("act", lambda s: nodes.act(s, ctx))
    g.add_node("report", lambda s: nodes.report(s, ctx))
    g.add_node("remember", lambda s: nodes.remember(s, ctx))

    def dispatch(s: State):
        shared = {"question": s["question"], "data_map": s.get("data_map", {}), "memories": s.get("memories", [])}
        tasks = s.get("open_tasks", [])
        if not tasks:
            return "evaluate"
        ctx.tracer.event("dispatch", "Planner", tasks=[t["id"] for t in tasks])
        return [Send("investigate", {**shared, "task": t}) for t in tasks]

    def route(s: State):
        return "plan" if s["evaluations"][-1].get("decision") == "revise" else "act"

    g.add_edge(START, "recall")
    g.add_edge("recall", "scout")
    g.add_edge("scout", "plan")
    g.add_conditional_edges("plan", dispatch, ["investigate", "evaluate"])
    g.add_edge("investigate", "evaluate")
    g.add_conditional_edges("evaluate", route, {"plan": "plan", "act": "act"})
    g.add_edge("act", "report")
    g.add_edge("report", "remember")
    g.add_edge("remember", END)
    return g.compile()


def make_ctx(harness_pref: str | None = None, listener=None, tracer: Tracer | None = None,
             dataset: str | None = None) -> tuple[Ctx, list[str]]:
    from whychain.datasets import get_dataset

    ds = get_dataset(dataset)
    memory = Memory()
    tracer = tracer or Tracer()
    if tracer.world is None and memory.online:
        tracer.world = memory
    if listener:
        tracer.listeners.append(listener)
    harness, notes = get_harness(harness_pref, dataset=ds.key)
    llm = LLM(tracer=tracer)
    # a run never hangs: after this many seconds it stops calling models and finishes with what it has
    llm.deadline = time.monotonic() + float(os.environ.get("WHYCHAIN_RUN_TIMEOUT_S", "1500"))
    ctx = Ctx(llm=llm, harness=harness, tracer=tracer, memory=memory, settings=SETTINGS, as_of=ds.as_of,
              catalog_text=catalog_text(harness.catalog(), ds.source_notes), dataset=ds)
    return ctx, notes


_VERSION: dict | None = None


def code_version() -> dict:
    """The commit the code was at when this process first ran an investigation (the code it loaded), and whether code
    folders had uncommitted changes then. Results cite it."""
    global _VERSION
    if _VERSION is None:
        _VERSION = _git_version()
    return _VERSION


def _git_version() -> dict:
    import subprocess

    root = Path(__file__).resolve().parent.parent
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True,
                             timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no", "--", "whychain",
                                "meterless_bridge", "evals", "datagen"], cwd=root, capture_output=True, text=True,
                               timeout=10).stdout.strip()
        return {"commit": sha or None, "dirty": bool(dirty)}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


# Words that make a question about this kind of data. A question with none of them (and no place, product or month
# from the data) is answered at once instead of starting an open-ended investigation of nothing.
_TOPIC = re.compile(
    r"\b(late|lateness|delay\w*|deliver\w*|on[- ]time|order\w*|backorder\w*|backlog\w*|stock\w*|shortage\w*|"
    r"short|inventory|reorder\w*|suppl\w*|purchase\w*|po|pos|carrier\w*|transit|ship\w*|freight|warehouse\w*|dc|"
    r"dcs|seller\w*|customer\w*|review\w*|promise\w*|demand|sales|revenue|volume|return\w*|cancel\w*|"
    r"payment\w*|strike\w*|port|ports|weather|holiday\w*|carnival|black friday|peak|promotion\w*|promo\w*|"
    r"risk|logistic\w*|fulfil\w*|dispatch\w*|hand[- ]?over|lane\w*|price\w*|cost\w*|sku\w*|product\w*|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|20\d\d)\b", re.I)


def on_topic(question: str, ctx: Ctx) -> bool:
    if _TOPIC.search(question):
        return True
    q = question.lower()
    try:   # a place, warehouse or category from the data also counts
        names = {str(x).lower() for t in ctx.harness.catalog() for x in [t.source, t.table]}
    except Exception:
        names = set()
    from whychain.analysis.model import BR_STATE_NAMES
    names |= {n.lower() for n in BR_STATE_NAMES.values()}
    names |= {w for w in ("pune", "mumbai", "chennai", "nashik", "surat", "hyderabad", "bengaluru", "thane",
                          "kolhapur", "aurangabad", "coimbatore", "rio", "sao paulo", "são paulo", "voltix")}
    return any(re.search(rf"\b{re.escape(n)}\b", q) for n in names if len(n) > 2)


def investigate(question: str, harness_pref: str | None = None, listener=None, ctx: Ctx | None = None,
                notes: list[str] | None = None, dataset: str | None = None) -> dict:
    """Run one investigation end to end. Returns the final state plus run metadata."""
    notes = list(notes or [])
    if ctx is None:
        ctx, notes = make_ctx(harness_pref, listener, dataset=dataset)
    t = ctx.tracer
    ds_key = getattr(ctx.dataset, "key", None)
    version = code_version()
    t.event("run_start", "System", question=question, dataset=ds_key, harness=ctx.harness.name, harness_notes=notes,
            memory=ctx.memory.status(), models=ctx.llm.available(), today=str(date.today()), code=version)
    if not on_topic(question, ctx):
        # nothing in the question is about orders, deliveries, stock or the places and products in this data
        samples = "; ".join(f'"{x["question"]}"' for x in (getattr(ctx.dataset, "samples", None) or [])[:3])
        brief = ("## Answer\nThis question doesn't mention anything WhyChain can investigate in this data: orders, "
                 "deliveries, stock, suppliers, carriers, places, products or a period. No investigation was started "
                 "and no model was called." + (f"\n\nTry, for example: {samples}" if samples else ""))
        final = {"question": question, "headline": "Not a question about this data: nothing was investigated.",
                 "brief": brief, "tasks": [], "findings": [], "evidence": {}, "verdicts": {}, "actions": [],
                 "risks": [], "evaluations": [], "round": 0}
        t.event("off_topic", "System", note="no supply-chain term, place, product or period in the question")
        stats = t.llm_stats()
        t.event("run_end", "System", status="ok", **stats)
        result = {"run_id": t.run_id, "status": "ok", "dataset": ds_key, "harness": ctx.harness.name,
                  "harness_notes": notes, "memory": ctx.memory.status(), "code": version, "llm": stats, **final}
        t.save("result.json", result)
        t.save("brief.md", f"# {final['headline']}\n\n{brief}")
        return result
    graph = build_graph(ctx)
    try:
        final = graph.invoke({"question": question}, {"recursion_limit": 60})
        # finished, but without a written brief (e.g. the Reporter failed): don't present it as a complete answer
        status = ("ok" if len(str(final.get("brief") or "").strip()) >= 200
                  and not str(final.get("headline") or "").startswith("Incomplete:") else "incomplete")
    except Exception as e:
        t.event("run_error", "System", error=f"{type(e).__name__}: {e}", tb=traceback.format_exc()[-1500:])
        final, status = {"question": question, "brief": f"Run failed: {e}"}, "error"
    stats = t.llm_stats()
    t.event("run_end", "System", status=status, **stats)
    ctx.memory.flush()
    result = {"run_id": t.run_id, "status": status, "dataset": ds_key, "harness": ctx.harness.name, "harness_notes": notes,
              "memory": ctx.memory.status(), "code": version, "llm": stats, **final}
    t.save("result.json", result)
    if final.get("brief"):
        t.save("brief.md", f"# {final.get('headline', '')}\n\n{final['brief']}")
    return result
