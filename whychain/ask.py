"""Ask Zetaris: a plain-English question becomes one read-only Spark SQL query, written by a coding model
(SpaceXAI's Grok when XAI_API_KEY is set, NVIDIA Nemotron otherwise) and run live through Zetaris.

The model only writes the query. Zetaris runs it; the harness refuses anything that isn't a single read-only
statement; and when Zetaris rejects the SQL, its error goes back to the model to fix, up to three tries. Every
attempt is kept, so the answer shows which model wrote what, and what Zetaris said.
"""
from __future__ import annotations

import time

from whychain.harness.base import DataHarness
from whychain.llm import LLM, LLMError, extract_json

SYSTEM = """You are a SQL coding agent for a company's federated data layer (Zetaris, Spark SQL dialect).
Write ONE read-only query (SELECT or WITH) that answers the question, using only the tables and columns in the
catalog, with their fully qualified names exactly as listed. Prefer clear column aliases. Return at most 50 rows
unless the question asks for more, ordered so the most important rows come first.

Reply with one JSON object and nothing else:
{"sql": "<the query>", "explanation": "<one sentence: what the query computes>"}"""

MAX_ATTEMPTS = 3


def ask(question: str, harness: DataHarness, llm: LLM, catalog: str, as_of: str, dataset: str = "") -> dict:
    question = question.strip()
    base = f"TODAY: {as_of}\nDATASET: {dataset}\n\nCATALOG:\n{catalog}\n\nQUESTION: {question}"
    prompt = base
    attempts: list[dict] = []
    t0 = time.perf_counter()
    for n in range(1, MAX_ATTEMPTS + 1):
        try:
            res = llm.chat("code", SYSTEM, prompt, agent="SQL coder", json_mode=True, max_tokens=2000, temperature=0.1)
        except LLMError as e:
            attempts.append({"n": n, "error": f"model unavailable: {e}"[:300]})
            break
        try:
            out = extract_json(res.text)
        except ValueError as e:
            attempts.append({"n": n, "provider": res.provider, "model": res.model, "error": f"no JSON reply ({e})"[:300]})
            prompt = base + "\n\nYour last reply was not one JSON object. Reply with the JSON object only."
            continue
        sql = str(out.get("sql") or "").strip()
        r = harness.query(sql, limit=50)
        attempt = {"n": n, "provider": res.provider, "model": res.model, "model_ms": res.ms, "sql": sql,
                   "explanation": str(out.get("explanation") or "")[:300], "harness": r.harness,
                   "query_ms": r.elapsed_ms, "error": r.error}
        attempts.append(attempt)
        if r.ok:
            return {"ok": True, "question": question, "dataset": dataset, "sql": sql,
                    "explanation": attempt["explanation"], "columns": r.columns, "rows": r.rows,
                    "row_count": r.row_count, "truncated": r.truncated, "harness": r.harness,
                    "provider": res.provider, "model": res.model, "attempts": attempts,
                    "seconds": round(time.perf_counter() - t0, 1)}
        # Zetaris rejected it: the model sees exactly what went wrong and fixes it
        prompt = (base + f"\n\nYour previous query failed on {r.harness}:\n{sql}\n\nERROR: {r.error}\n\n"
                  "Fix the query (check table and column names against the catalog) and reply again.")
    return {"ok": False, "question": question, "dataset": dataset, "attempts": attempts,
            "error": (attempts[-1].get("error") if attempts else "no attempt"),
            "seconds": round(time.perf_counter() - t0, 1)}
