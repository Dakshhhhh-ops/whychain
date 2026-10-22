"""End-to-end wiring test with the scripted model and the local harness (no API keys needed).

  python -m pytest -q tests
"""
from __future__ import annotations

from whychain.agents.executor import Ctx
from whychain.config import Settings
from whychain.graph import AS_OF, investigate
from whychain.harness import catalog_text
from whychain.harness.local import LocalHarness
from whychain.memory import Memory
from whychain.trace import Tracer
from tests.fake_llm import FakeLLM


def make_ctx(tmp_path, fixed_analyses=False):
    """fixed_analyses=False sends the question through the open-ended agent loop (planner, evaluator, executor),
    which is what these tests script; the computed analyses have their own tests."""
    tracer = Tracer(runs_dir=tmp_path)
    harness = LocalHarness("kaveri")
    memory = Memory(url="http://127.0.0.1:9", local_path=tmp_path / "memory.json")  # always offline in tests
    return Ctx(llm=FakeLLM(tracer), harness=harness, tracer=tracer, memory=memory, settings=Settings(runs_dir=tmp_path),
               as_of=AS_OF, catalog_text=catalog_text(harness.catalog()), fixed_analyses=fixed_analyses)


def test_full_run(tmp_path):
    ctx = make_ctx(tmp_path)
    res = investigate("Why are so many Pune orders at risk next week?", ctx=ctx)
    assert res["status"] == "ok", res.get("brief")
    kinds = [e["kind"] for e in ctx.tracer.events]

    # the evaluator sent the plan back once, then accepted
    assert [e["decision"] for e in ctx.tracer.events if e["kind"] == "evaluation"] == ["revise", "accept"]
    assert len(res["tasks"]) == 5
    # broken SQL was repaired and the repair counted
    assert any(f["sql_errors_recovered"] >= 1 for f in res["findings"] if f["agent_kind"] == "data")
    # invented quote rejected, real quote kept as evidence
    assert "quote_rejected" in kinds
    assert "T3.D1" in res["evidence"] and res["evidence"]["T3.D1"]["type"] == "doc"
    # citations to evidence that doesn't exist are dropped
    assert all(e in res["evidence"] for f in res["findings"] for e in f["evidence_ids"])
    assert "[T9.Q9]" not in res["brief"]
    # evidence was re-run by the evaluator
    assert any(e["kind"] == "reverify" and e["reproduced"] for e in ctx.tracer.events)
    # bogus join dropped, real joins kept
    joins = {(j["left"], j["right"]) for j in res["data_map"]["joins"]}
    assert ("erp.sales_orders.order_id", "carrier.shipments.order_id") in joins
    assert not any("shipment_id" in r for _, r in joins)
    # expensive action escalated for approval, cheap one not
    by_title = {a["title"]: a for a in res["actions"]}
    assert by_title["Air-freight Voltix to Mumbai"]["needs_approval"]
    assert not by_title["Transfer Voltix stock MUM to PUN"]["needs_approval"]
    assert (tmp_path / res["run_id"] / "trace.jsonl").exists()
    # only real causes reach the risk agent (not the measurement, not the ruled-out check)
    start = next(e for e in ctx.tracer.events if e["kind"] == "agent_start" and e["agent"] == "Risk & Action")
    assert start["causes"] == ["T2", "T3"]


def test_action_executor(tmp_path):
    from whychain.agents.deliver import deliver

    ctx = make_ctx(tmp_path)
    res = investigate("Why are so many Pune orders at risk next week?", ctx=ctx)
    action = next(a for a in res["actions"] if a["title"].startswith("Transfer"))
    out = deliver(ctx, res, action, note="go ahead")
    kinds = [d["kind"] for d in out["deliverables"]]
    assert kinds == ["table", "table", "email"]
    plan = out["deliverables"][0]  # the transfer order is the Optiagent plan, verified optimal
    assert plan["source"] == "X.P1" and plan["rows"] and "units" in plan["columns"]
    assert plan["optimization"]["certified_with"]
    check = out["deliverables"][1]
    assert check["source"] == "X.Q1" and check["rows"] and "shortfall" in check["columns"]
    # a table that doesn't come from one of the agent's own queries is discarded
    assert [r["title"] for r in out["rejected"]] == ["Invented table"]


def test_supply_route(tmp_path):
    """The same question with the computed analyses on: the supply battery answers it from the data, and the brief
    names the planted cause (Voltix POs held at the Chennai port)."""
    ctx = make_ctx(tmp_path, fixed_analyses=True)
    res = investigate("Why are so many Pune orders at risk next week?", ctx=ctx)
    assert res["status"] == "ok", res.get("brief")
    assert res["analysis"]["kind"] == "supply"
    causes = [f for f in res["findings"] if (f.get("fixed") or {}).get("is_cause")]
    assert causes, [f.get("claim") for f in res["findings"]]
    text = " ".join(f["claim"] for f in causes)
    assert "S-14" in text and "Chennai" in text
    assert all(e in res["evidence"] for f in res["findings"] for e in f["evidence_ids"])
    # the port is shown as why the inbound orders are late, not as a second cause with the same share
    assert "held up at Chennai" in res["headline"]
    roots = res["brief"].split("## Root causes")[1].split("\n## ")[0]
    assert "held up at Chennai" in roots and "**A disrupted port" not in roots
    how = res["brief"].split("## How we know")[1]
    assert "Why they are late" in how            # the port's own numbers are in the detail, under the cause
    # the manager summary comes first, in money
    summary = res["brief"].split("## Summary")[1].split("\n## ")[0]
    assert "What happened" in summary and "What to do" in summary and "INR" in summary
    # the transfer says what it covers (overdue orders too), so it isn't read against the next-week count
    transfer = next(a for a in res["actions"] if a["title"].startswith("Transfer"))
    assert "past their promised date" in transfer["steps"]


def test_a_failed_reporter_says_incomplete_and_lists_what_was_confirmed(tmp_path):
    """The open-ended loop can hit the run's time limit before the Reporter writes: the brief must say so, list the
    confirmed findings, and the run must be marked incomplete (not a bare 'Report generation failed')."""
    from whychain.llm import LLMError

    ctx = make_ctx(tmp_path)
    real_chat = ctx.llm.chat

    def chat(role, system, user, agent="", **kw):
        if agent == "Reporter":
            raise LLMError("the run's time limit was reached; no more model calls")
        return real_chat(role, system, user, agent=agent, **kw)

    ctx.llm.chat = chat
    res = investigate("Why are so many Pune orders at risk next week?", ctx=ctx)
    assert res["status"] == "incomplete"
    assert res["headline"].startswith("Incomplete: the brief couldn't be written (the run's time limit was reached")
    assert "## Confirmed findings" in res["brief"]


def test_an_off_topic_question_is_answered_at_once_without_a_model(tmp_path):
    from whychain.graph import on_topic

    ctx = make_ctx(tmp_path)
    assert not on_topic("purple elephant banana tuesday?", ctx)
    assert on_topic("What happened in Pune?", ctx) and on_topic("Why is Bahia so slow?", ctx)
    res = investigate("purple elephant banana tuesday?", ctx=ctx)
    assert res["headline"].startswith("Not a question about this data")
    assert not res["llm"].get("calls")


def test_the_written_record_is_remembered_as_its_quotes_not_the_models_summary(tmp_path):
    """O1: the Document Analyst's summary said the strike 'explains the jump', which the computed event test rules
    out, and memory stored it as CONFIRMED. A context reading is now stored as its verified quotes only."""
    import json

    from whychain.agents.nodes import remember

    ctx = make_ctx(tmp_path)
    quote = "Correios workers began an open-ended national strike on Monday 12 March 2018."
    state = {"question": "Why did late deliveries jump in March 2018?", "lessons": [],
             "verdicts": {"T13": {"status": "confirmed", "is_cause": False}},
             "evidence": {"T13.D1": {"id": "T13.D1", "type": "doc", "doc_id": "D7", "title": "Correios strike",
                                     "sent_at": "2018-03-12", "quote": quote}},
             "findings": [{"task_id": "T13", "statement": "What the written record says about the period",
                           "type": "hypothesis", "agent_kind": "docs", "context_only": True,
                           "claim": "The strike explains the jump to 19% late deliveries.", "evidence_ids": ["T13.D1"]}]}
    remember(state, ctx)
    items = json.loads((tmp_path / "memory.json").read_text(encoding="utf-8"))
    texts = [i.get("content") or i.get("text") or json.dumps(i) for i in items]
    assert any(t.startswith("WRITTEN RECORD") and quote in t for t in texts)
    assert not any("explains the jump" in t for t in texts)
