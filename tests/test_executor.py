"""The act-observe loop recovers from the malformed replies real models send."""
from __future__ import annotations

from whychain.agents.executor import Ctx, run_loop
from whychain.config import Settings
from whychain.harness.local import LocalHarness
from whychain.memory import Memory
from whychain.trace import Tracer


class Scripted:
    """Returns the given replies in order, whatever the prompt."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def json(self, role, system, user, *, agent, **kw):
        self.prompts.append(user)
        return self.replies.pop(0)


def ctx_with(tmp_path, llm):
    return Ctx(llm=llm, harness=LocalHarness("kaveri"), tracer=Tracer(runs_dir=tmp_path),
               memory=Memory(url="http://127.0.0.1:9", local_path=tmp_path / "m.json"),
               settings=Settings(runs_dir=tmp_path), as_of="2026-10-21")


def test_thought_only_reply_is_repaired_without_losing_a_step(tmp_path):
    llm = Scripted([
        {"thought": "I'll count Pune's backordered orders."},  # plan only, no action
        {"thought": "count", "action": "query", "sql": "SELECT COUNT(*) AS n FROM erp.sales_orders WHERE warehouse_id = 'PUN' AND status = 'BACKORDERED'"},
        {"action": "finish", "verdict": "measured", "claim": "counted", "evidence": ["T1.Q1"]},
    ])
    ctx = ctx_with(tmp_path, llm)
    res = run_loop(ctx, agent="Investigator T1", role="work", system="s", brief="b", task_id="T1", max_steps=3)
    assert "T1.Q1" in res.evidence and res.final["evidence"] == ["T1.Q1"]
    assert "only described a plan" in llm.prompts[1]
    assert any(e["kind"] == "action_repaired" for e in ctx.tracer.events)
    assert res.steps == 2


def test_sql_under_the_query_key_is_accepted(tmp_path):
    llm = Scripted([
        {"thought": "count", "action": "query", "query": "SELECT COUNT(*) AS n FROM erp.warehouses"},
        {"action": "finish", "verdict": "measured", "claim": "3 DCs", "evidence": ["T1.Q1"]},
    ])
    res = run_loop(ctx_with(tmp_path, llm), agent="Investigator T1", role="work", system="s", brief="b",
                   task_id="T1", max_steps=3)
    assert res.evidence["T1.Q1"]["rows"] == [[3]]


def test_empty_final_reply_is_asked_again_for_the_required_fields(tmp_path):
    llm = Scripted([
        {"thought": "q", "action": "query", "sql": "SELECT COUNT(*) AS n FROM erp.warehouses"},
        {},  # forced final answer comes back empty
        {"action": "finish", "actions": [{"title": "Do something", "est_cost_inr": 0}]},
    ])
    ctx = ctx_with(tmp_path, llm)
    res = run_loop(ctx, agent="Risk & Action", role="work", system="s", brief="b", task_id="A", max_steps=1,
                   finish_keys=("actions",))
    assert res.final["actions"][0]["title"] == "Do something"
    assert any(e["kind"] == "finish_repair" for e in ctx.tracer.events)
