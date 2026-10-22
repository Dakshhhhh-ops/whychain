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
    assert "Why they are late" in roots and "**A disrupted port" not in roots
    # the transfer says what it covers (overdue orders too), so it isn't read against the next-week count
    transfer = next(a for a in res["actions"] if a["title"].startswith("Transfer"))
    assert "past their promised date" in transfer["steps"]
