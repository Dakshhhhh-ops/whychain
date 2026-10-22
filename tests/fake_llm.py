"""A scripted stand-in for the model, for testing the graph's wiring without any API keys.

It plays every role with plausible, fixed moves, including deliberate mistakes (a broken
SQL query, an invented quote, a bogus join, an invalid citation) so the recovery and
verification paths get exercised. It is not used in real runs.
"""
from __future__ import annotations

import re

from whychain.llm import LLMResult


class FakeLLM:
    def __init__(self, tracer=None):
        self.tracer = tracer
        self.calls: list[str] = []

    def available(self) -> dict:
        return {"fake": True}

    def chat(self, role, system, user, *, agent, json_mode=False, temperature=0.2, max_tokens=2500):
        if agent == "Reporter":  # the Reporter writes plain markdown
            self.calls.append(agent)
            if self.tracer:
                self.tracer.event("llm_call", agent, provider="fake", model="fake", ms=1)
            return LLMResult(
                "HEADLINE: Pune orders are at risk because the Chennai port strike stranded Voltix stock\n\n"
                "## Answer\nStrike [T2.Q2] [T3.D1]. Bogus cite [T9.Q9]. The Chennai port strike held Voltix (S-14) "
                "pre-build purchase orders at the port, so Pune ran dry while Mumbai held surplus stock.\n\n"
                "## Root causes\n1. Port strike stranding Voltix POs [T2.Q2], about 74% of at-risk orders.\n\n"
                "## Ruled out\nWeather [T5.Q2]: the rain alert did not affect deliveries.\n\n"
                "## Recommended actions\n| Action | Protects | Cost | Approval |\n|---|---|---|---|\n"
                "| Transfer stock from Mumbai to Pune | 26 orders | 41,000 | Yes |\n\n"
                "## Confidence and open questions\nHigh.\n\n"
                "LESSONS:\n- Check port status for suppliers consolidating through a single port\n",
                "fake", "fake", 1)
        return LLMResult("{}", "fake", "fake", 1)

    def json(self, role, system, user, *, agent, retries=2, temperature=0.2, max_tokens=2500) -> dict:
        self.calls.append(agent)
        if self.tracer:
            self.tracer.event("llm_call", agent, provider="fake", model="fake", ms=1)
        step = int(m.group(1)) if (m := re.search(r"Step (\d+) of", user)) else 99
        done = "You have used all your steps" in user
        if agent == "Data Scout":
            return {"sources": [{"source": "erp", "what_it_holds": "orders", "grain": "order"}],
                    "joins": [{"left": "erp.sales_orders.order_id", "right": "carrier.shipments.order_id",
                               "meaning": "order shipment"},
                              {"left": "erp.order_lines.sku", "right": "procurement.purchase_orders.sku",
                               "meaning": "sku on PO"},
                              {"left": "erp.customers.city", "right": "carrier.shipments.shipment_id",
                               "meaning": "bogus join that must be dropped"}],
                    "caveats": ["dates may be strings"]}
        if agent == "Planner":
            if "GAPS TO CLOSE" in user:
                return {"tasks": [{"type": "hypothesis", "agent": "data", "statement": "In-stock Pune orders ship on time, so weather is not the cause",
                                   "checks": ["on-time rate of Pune orders without Voltix lines"]}],
                        "reasoning": "close the weather gap"}
            return {"symptom": "Share of Pune open orders due next week that are backordered",
                    "tasks": [{"type": "measure", "agent": "data", "statement": "Measure Pune backorders due next week"},
                              {"type": "hypothesis", "agent": "data", "statement": "Voltix S-14 inbound POs to Pune are late"},
                              {"type": "hypothesis", "agent": "docs", "statement": "Documents explain the Voltix delay"},
                              {"type": "hypothesis", "agent": "data", "statement": "Heavy rain in Pune delays carriers"}],
                    "reasoning": "measure then test"}
        tid = m.group(1) if (m := re.search(r"YOUR TASK \[(T\d+)\]", user)) else "A"
        if agent.startswith("Investigator"):
            if step == 1:  # deliberately broken SQL to exercise repair
                return {"thought": "count backorders", "action": "query", "sql": "SELECT warehouse, COUNT(*) FROM erp.sales_orders GROUP BY warehouse"}
            if step == 2:
                return {"thought": "fixed column name", "action": "query",
                        "sql": "SELECT warehouse_id, status, COUNT(*) AS n FROM erp.sales_orders WHERE CAST(promised_date AS DATE) BETWEEN DATE'2026-10-21' AND date_add(DATE'2026-10-21', 7) AND status IN ('OPEN','BACKORDERED') GROUP BY warehouse_id, status ORDER BY 1, 2"}
            return {"thought": "enough", "action": "finish", "verdict": "measured" if tid == "T1" else "supports",
                    "claim": f"{tid}: Pune has 26 backordered orders due next week", "key_numbers": {"pune_backordered": 26},
                    "evidence": [f"{tid}.Q1", f"{tid}.Q9"], "confidence": 0.8}
        if agent.startswith("Doc Analyst"):
            if step == 1:
                return {"thought": "search", "action": "query", "sql": "SELECT doc_id, title FROM docs.documents WHERE lower(body) LIKE '%strike%'"}
            if step == 2:
                return {"thought": "read", "action": "read_doc", "doc_id": "2026-10-09_voltix_port_strike_notice"}
            return {"thought": "done", "action": "finish", "verdict": "supports", "claim": "Port strike delays Voltix",
                    "quotes": [{"doc_id": "2026-10-09_voltix_port_strike_notice", "quote": "Dock workers at Chennai port began an indefinite strike this morning."},
                               {"doc_id": "2026-10-09_voltix_port_strike_notice", "quote": "This sentence does not exist in the email."}],
                    "who_knew": "procurement@", "confidence": 0.9}
        if agent == "Evaluator":
            if "round 1)" in user:
                return {"verdicts": [{"task_id": "T1", "status": "confirmed", "reason": "measured"},
                                     {"task_id": "T2", "status": "confirmed", "reason": "late POs", "share_of_symptom": 0.8},
                                     {"task_id": "T3", "status": "confirmed", "reason": "strike email"},
                                     {"task_id": "T4", "status": "needs_more", "reason": "no on-time comparison"}],
                        "symptom_explained": 0.8, "gaps": [{"what": "compare on-time of in-stock Pune orders", "why": "rule out weather"}],
                        "decision": "revise", "summary": "mostly explained"}
            return {"verdicts": [{"task_id": "T4", "status": "rejected", "reason": "in-stock orders on time"},
                                 {"task_id": "T5", "status": "confirmed", "reason": "weather ruled out", "is_cause": False}],
                    "symptom_explained": 0.9, "gaps": [], "decision": "accept", "summary": "explained by strike"}
        if agent == "Risk & Action":
            if step == 1:
                return {"thought": "cheapest way to cover Pune's Voltix shortfall", "action": "optimize",
                        "scope": {"supplier_id": "S-14"}, "to_warehouses": ["PUN"], "deadline_days": 7}
            if step == 2:
                return {"thought": "check Mumbai stock", "action": "query",
                        "sql": "SELECT i.warehouse_id, SUM(i.on_hand) AS on_hand FROM wms.inventory_snapshots i JOIN erp.skus s ON i.sku = s.sku WHERE s.supplier_id = 'S-14' AND CAST(i.snapshot_date AS DATE) = DATE'2026-10-21' GROUP BY i.warehouse_id"}
            # cost taken from the optimizer's plan, as a real agent must (invented figures are removed)
            truck = re.search(r"truck\(s\) INR ([\d,]+)", user)
            cost = float(truck.group(1).replace(",", "")) if truck else 0
            return {"thought": "done", "action": "finish",
                    "actions": [{"title": "Transfer Voltix stock MUM to PUN", "steps": "2 trucks", "addresses": ["T2"],
                                 "est_cost_inr": cost,
                                 "confidence": 0.85, "policy_notes": "", "evidence": ["A.P1", "A.Q1"]},
                                {"title": "Air-freight Voltix to Mumbai", "steps": "air", "addresses": ["T2"],
                                 "est_cost_inr": 950000, "confidence": 0.7, "evidence": ["A.Q1"]}],
                    "risks": [{"risk": "penalties", "exposure_inr": 120000, "evidence": []}]}
        if agent.startswith("Action Executor"):
            if step == 1:
                return {"thought": "optimal transfer plan", "action": "optimize", "scope": {"supplier_id": "S-14"},
                        "to_warehouses": ["PUN"], "deadline_days": 7}
            if step == 2:
                return {"thought": "compute what Pune is short of and what Mumbai can spare", "action": "query",
                        "sql": "SELECT p.sku, p.qty_on_overdue_orders - p.on_hand AS shortfall, m.on_hand AS mumbai_on_hand FROM wms.inventory_snapshots p JOIN wms.inventory_snapshots m ON m.sku = p.sku AND m.snapshot_date = p.snapshot_date AND m.warehouse_id = 'MUM' JOIN erp.skus s ON s.sku = p.sku WHERE p.warehouse_id = 'PUN' AND s.supplier_id = 'S-14' AND CAST(p.snapshot_date AS DATE) = DATE'2026-10-21' AND p.qty_on_overdue_orders > p.on_hand ORDER BY shortfall DESC"}
            return {"thought": "done", "action": "finish", "summary": "Transfer order and customer email prepared",
                    "deliverables": [
                        {"kind": "table", "title": "Stock transfer order MUM to PUN", "from_query": "X.P1"},
                        {"kind": "table", "title": "Shortfall check", "from_query": "X.Q1"},
                        {"kind": "table", "title": "Invented table", "from_query": "X.Q9"},
                        {"kind": "email", "title": "Update to Shree Ganesh", "to": "vikram@shreeganesh-retail.example",
                         "subject": "Recovery plan for this week's orders", "body": "Dear Vikram, ..."}]}
        if agent == "Reporter":
            return {"headline": "Pune orders are at risk because the Chennai port strike stranded Voltix stock",
                    "brief_markdown": "## Answer\nStrike [T2.Q2] [T3.D1]. Bogus cite [T9.Q9].\n## Ruled out\nWeather [T5.Q2]",
                    "lessons": ["Check port status for suppliers consolidating through a single port"]}
        return {}
