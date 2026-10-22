"""Meterless Scout Intent: which part of WhyChain answers a question.

Scout runs in the Meterless bridge (meterless_bridge/scout/scout.ts, WhyChain's implementation of the Scout Intent
spec): it scores the question against WhyChain's intent registry (meterless_bridge/scout/whychain-intents.json), checks
it for prompt injection, and returns a signed execution contract. WhyChain follows Scout when it is confident (High or
Medium band, no clarification needed) and its own word rules otherwise; if the bridge is unreachable, the rules decide.
Every run records both, so a disagreement is visible.
"""
from __future__ import annotations

import httpx

# Scout intent -> the part of WhyChain that answers it
ROUTE = {"delivery.explain": "battery", "supply.explain": "supply", "open.investigate": "agent", "GENERAL": "off_topic"}
LABEL = {"battery": "the analysis engine (late deliveries)", "supply": "the supply engine (stock and backorders)",
         "agent": "the open-ended agent loop", "off_topic": "nothing (not a question about this data)",
         "refuse": "nothing (instructions aimed at the agents)"}


def decide(question: str, memory, timeout: float = 20.0) -> dict | None:
    """Scout's decision for a question, or None if the bridge is unreachable or answers with an error."""
    if not getattr(memory, "online", False):
        return None
    try:
        r = httpx.post(f"{memory.url}/scout/decide", timeout=timeout,
                       json={"prompt": question, "user": {"id": "whychain", "role": "analyst"}, "surface": "app"})
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def rules_route(question: str, ctx) -> str:
    """WhyChain's word rules: the fallback."""
    from whychain.analysis.battery import battery_applies
    from whychain.analysis.supply import supply_kind
    from whychain.graph import on_topic

    if not on_topic(question, ctx):
        return "off_topic"
    if supply_kind(question):
        return "supply"
    return "battery" if battery_applies(question) else "agent"


def choose(decision: dict | None, rules: str) -> tuple[str, str]:
    """(route, decided_by). A question Scout's guard blocks is refused whatever the rules say."""
    if not decision:
        return rules, "rules (Scout unavailable)"
    if (decision.get("risk") or {}).get("level") == "block":
        return "refuse", "scout"
    intent = decision.get("intent") or {}
    if intent.get("band") in ("high", "medium") and not decision.get("clarification") \
            and intent.get("primary") in ROUTE:
        return ROUTE[intent["primary"]], "scout"
    return rules, "rules (Scout not confident)"


def summary(decision: dict | None) -> dict:
    """What a run's trace records about Scout's decision (not the full contract)."""
    if not decision:
        return {"available": False}
    intent, risk = decision.get("intent") or {}, decision.get("risk") or {}
    contract = decision.get("executionContract") or {}
    return {"available": True, "intent": intent.get("primary"), "confidence": intent.get("confidence"),
            "band": intent.get("band"), "intents": intent.get("intents"), "risk": risk.get("level"),
            "flags": [f.get("kind") for f in risk.get("flags") or []],
            "tool_plan": [s.get("capability") for s in decision.get("toolPlan") or []],
            "clarification": (decision.get("clarification") or {}).get("reasons") if decision.get("clarification") else None,
            "contract_id": contract.get("contractId"), "signature": (contract.get("signature") or "")[:16] or None,
            "semantic_stage": (decision.get("stages") or {}).get("semantic"), "ms": decision.get("ms")}
