"""The investigation graph (LangGraph).

  recall -> scout -> plan -> [investigate x N, in parallel] -> evaluate --revise--> plan
                                                                  |
                                                               accept
                                                                  v
                                                     act (risk & escalation) -> report -> remember
"""
from __future__ import annotations

import operator
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


def code_version() -> dict:
    """The commit the code was at, and whether code folders had uncommitted changes (results cite it)."""
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
    graph = build_graph(ctx)
    try:
        final = graph.invoke({"question": question}, {"recursion_limit": 60})
        # finished, but without a written brief (e.g. the Reporter failed): don't present it as a complete answer
        status = "ok" if len(str(final.get("brief") or "").strip()) >= 200 else "incomplete"
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
