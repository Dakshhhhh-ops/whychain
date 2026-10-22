"""Score WhyChain against the planted scenarios (Kaveri) and the verified real-data cases (Olist).

  python -m evals.run_evals                # all cases
  python -m evals.run_evals --dataset olist
  python -m evals.run_evals --only A-pune-at-risk D-chennai-backlog

Writes evals/results.md (a table for the README) and evals/results.json.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import yaml

from whychain.graph import investigate

HERE = Path(__file__).resolve().parent


def term_pattern(term: str) -> re.Pattern:
    """A synonym must start at a word boundary. Short terms (4 letters or fewer, like "rio", "port", "edi", "air") must
    be whole words; longer ones are stems and may continue ("consolidat" matches "consolidated")."""
    t = str(term).lower().strip()
    tail = r"(?![a-z0-9])" if len(t) <= 4 else ""
    return re.compile(r"(?<![a-z0-9])" + re.escape(t) + tail)


def matches(text: str, group: list[str]) -> bool:
    return any(term_pattern(s).search(text) for s in group)


def score(case: dict, res: dict) -> dict:
    v = res.get("verdicts", {})
    causes = [f for f in res.get("findings", []) if v.get(f["task_id"], {}).get("status") == "confirmed"
              and v[f["task_id"]].get("is_cause", True)]
    cause_text = " ".join(f"{f['statement']} {f['claim']}" for f in causes).lower()
    found_text = cause_text + " " + str(res.get("headline", "")).lower()
    groups = case["must_find"]
    hit = [matches(found_text, g) for g in groups]
    need = case.get("min_groups", len(groups))
    # a red herring counts only if a confirmed cause's HYPOTHESIS names it (a claim may mention it to rule it out),
    # or the headline blames it
    statements = " ".join(f["statement"] for f in causes).lower()
    distractor_confirmed = any(matches(statements, g) for g in case.get("distractors") or [])
    headline = str(res.get("headline", ""))
    blamed = [rx for rx in case.get("headline_must_not") or [] if re.search(rx, headline, re.I)]
    herring_checked = bool(case.get("distractors") or case.get("headline_must_not"))
    actions_text = " ".join(f"{a.get('title', '')} {a.get('steps', '')}" for a in res.get("actions", [])).lower()
    expected = case.get("expected_actions") or []
    action_ok = all(matches(actions_text, g) for g in expected) if expected else None
    rejected = [f["task_id"] for f in res.get("findings", []) if v.get(f["task_id"], {}).get("status") == "rejected"]
    return {"root_cause": sum(hit) >= need, "groups_hit": f"{sum(hit)}/{len(groups)}",
            "red_herring_avoided": (not distractor_confirmed and not blamed) if herring_checked else None, "action_ok": action_ok, "rejected_tasks": rejected,
            "rounds": res.get("round"), "tasks": len(res.get("tasks", [])),
            "evidence": len(res.get("evidence", {})), "model_calls": res.get("llm", {}).get("calls"),
            "fallbacks": res.get("llm", {}).get("fallbacks"), "status": res.get("status"),
            "headline": res.get("headline", "")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--dataset", choices=["olist", "kaveri"], default=None, help="only this dataset's cases")
    ap.add_argument("--harness", choices=["zetaris", "local"], default=None)
    args = ap.parse_args()
    cases = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    if args.dataset:
        cases = [c for c in cases if c.get("dataset", "kaveri") == args.dataset]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    rows = []
    for c in cases:
        t0 = time.time()
        res = investigate(c["question"], args.harness, dataset=c.get("dataset", "kaveri"))
        s = score(c, res)
        s.update(id=c["id"], dataset=c.get("dataset", "kaveri"), held_out=bool(c.get("held_out")), run_id=res["run_id"], seconds=round(time.time() - t0), harness=res.get("harness"),
                 providers=res.get("llm", {}).get("by_provider"))
        rows.append(s)
        print(f"{c['id']:<20} cause={'PASS' if s['root_cause'] else 'FAIL'} ({s['groups_hit']}) "
              f"herring_avoided={s['red_herring_avoided']} action={s['action_ok']} rounds={s['rounds']} "
              f"{s['seconds']}s  {s['headline'][:80]}")
    (HERE / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    md = ["| Case | Dataset | Held out | Root cause found | Red herring avoided | Right action | Rounds | Evidence | Model calls | Time | Run |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in rows:
        md.append(f"| {s['id']} | {s['dataset']} | {'yes' if s['held_out'] else ''} | {'✅' if s['root_cause'] else '❌'} {s['groups_hit']} | "
                  f"{'—' if s['red_herring_avoided'] is None else '✅' if s['red_herring_avoided'] else '❌'} | {'—' if s['action_ok'] is None else '✅' if s['action_ok'] else '❌'} | {s['rounds']} | "
                  f"{s['evidence']} | {s['model_calls']} | {s['seconds']}s | `{s['run_id']}` |")
    n = len(rows) or 1
    held = [r for r in rows if r["held_out"]]
    if held:
        md.append(f"\nHeld-out questions: root cause found in {sum(r['root_cause'] for r in held)}/{len(held)}.")
    md.append(f"\nRoot cause found in {sum(r['root_cause'] for r in rows)}/{n} cases; red herring avoided in "
              f"{sum(bool(r['red_herring_avoided']) for r in rows)}/{sum(r['red_herring_avoided'] is not None for r in rows)} "
              f"cases that define one; right action in "
              f"{sum(bool(r['action_ok']) for r in rows)}/{sum(r['action_ok'] is not None for r in rows)} cases that have one.")
    (HERE / "results.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
