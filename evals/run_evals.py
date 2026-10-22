"""Score WhyChain against the planted scenarios (Kaveri) and the verified real-data cases (Olist).

  python -m evals.run_evals                # all cases
  python -m evals.run_evals --dataset olist
  python -m evals.run_evals --only A-pune-at-risk D-chennai-backlog
  python -m evals.run_evals --rescore 20261022-191118-0024 ...   # score saved runs, no new run
  python -m evals.run_evals --rescore <ids> --commit 44b257b      # label runs made before commits were recorded

Writes evals/results.md (a table for the README) and evals/results.json (or --out <stem>.md/.json).
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


MODEL_NAMES = {"nim": "Nemotron 3.5", "nim-reason": "Nemotron 3.5 (thinking)", "nim-backup": "Nemotron 3 Ultra",
               "xai": "Grok", "nightshift": "Nightshift", "ollama": "Ollama"}


def models_used(by_provider: dict | None) -> str:
    """Share of model calls per model, e.g. 'Nemotron 3.5 80%, Nemotron 3 Ultra 20%'."""
    if not by_provider:
        return "—"
    total = sum(by_provider.values()) or 1
    return ", ".join(f"{MODEL_NAMES.get(k, k)} {round(100 * v / total)}%"
                     for k, v in sorted(by_provider.items(), key=lambda kv: -kv[1]))


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
    # what the investigation established: confirmed causes AND confirmed measurements (e.g. where a rise sits), plus the
    # headline. Red herrings are judged on confirmed causes only (below).
    # confirmed document findings count too: their quotes were verified word for word
    measured = [f for f in res.get("findings", []) if v.get(f["task_id"], {}).get("status") == "confirmed"
                and f not in causes]
    found_text = " ".join([cause_text] + [f"{f['statement']} {f['claim']}".lower() for f in measured]
                          + [str(res.get("headline", "")).lower()])
    groups = case["must_find"]
    hit = [matches(found_text, g) for g in groups]
    need = case.get("min_groups", len(groups))
    # a red herring counts only if a confirmed cause's HYPOTHESIS names it (a claim may mention it to rule it out),
    # or the headline blames it
    statements = " ".join(f["statement"] for f in causes).lower()
    distractor_confirmed = any(matches(statements, g) for g in case.get("distractors") or [])
    headline = str(res.get("headline", ""))
    blamed = [rx for rx in case.get("headline_must_not") or [] if re.search(rx, headline, re.I)]
    # no confirmed cause = nothing was attributed, so there is no red-herring result to credit
    herring_checked = bool(case.get("distractors") or case.get("headline_must_not")) and bool(causes)
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
    ap.add_argument("--rescore", nargs="*", help="run ids to score again from runs/<id>/result.json (no new run)")
    ap.add_argument("--commit", default=None, help="with --rescore: the commit for runs that don't record one")
    ap.add_argument("--out", default=None, help="output stem instead of evals/results (writes .md and .json)")
    args = ap.parse_args()
    cases = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    if args.rescore is not None:
        rows = rescore(cases, args.rescore, args.commit)
        write(rows, Path(args.out) if args.out else HERE / "results")
        return
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
                 providers=res.get("llm", {}).get("by_provider"), commit=_commit(res.get("code")),
                 embedder=(res.get("memory") or {}).get("embedder"))
        rows.append(s)
        print(f"{c['id']:<20} cause={'PASS' if s['root_cause'] else 'FAIL'} ({s['groups_hit']}) "
              f"herring_avoided={s['red_herring_avoided']} action={s['action_ok']} rounds={s['rounds']} "
              f"{s['seconds']}s  {s['headline'][:80]}")
    write(rows, Path(args.out) if args.out else HERE / "results")


def _commit(code: dict | None) -> str | None:
    if not code or not code.get("commit"):
        return None
    return code["commit"] + ("+changes" if code.get("dirty") else "")


def rescore(cases: list[dict], run_ids: list[str], commit: str | None) -> list[dict]:
    """Score saved runs with the current scorer. The case is the one whose question the run asked."""
    runs = HERE.parent / "runs"
    by_q = {c["question"].strip(): c for c in cases}
    rows = []
    for rid in run_ids:
        res = json.loads((runs / rid / "result.json").read_text(encoding="utf-8"))
        c = by_q.get(str(res.get("question", "")).strip())
        if c is None:
            print(f"{rid}: no case asks {res.get('question')!r}; skipped")
            continue
        trace = runs / rid / "trace.jsonl"
        seconds = None
        if trace.exists():
            last = trace.read_text(encoding="utf-8").strip().splitlines()[-1]
            seconds = round(json.loads(last).get("t") or 0) or None
        s = score(c, res)
        s.update(id=c["id"], dataset=c.get("dataset", "kaveri"), held_out=bool(c.get("held_out")), run_id=rid,
                 seconds=seconds, harness=res.get("harness"), providers=res.get("llm", {}).get("by_provider"),
                 commit=_commit(res.get("code")) or commit, embedder=(res.get("memory") or {}).get("embedder"))
        rows.append(s)
        print(f"{c['id']:<20} cause={'PASS' if s['root_cause'] else 'FAIL'} ({s['groups_hit']}) "
              f"herring_avoided={s['red_herring_avoided']} action={s['action_ok']}  {rid}")
    return rows


def write(rows: list[dict], stem: Path) -> None:
    stem.with_suffix(".json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    md = ["| Case | Dataset | Held out | Root cause found | Red herring avoided | Right action | Rounds | Evidence | Model calls | Models used | Time | Harness | Commit | Run |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in rows:
        md.append(f"| {s['id']} | {s['dataset']} | {'yes' if s['held_out'] else ''} | {'✅' if s['root_cause'] else '❌'} {s['groups_hit']} | "
                  f"{'—' if s['red_herring_avoided'] is None else '✅' if s['red_herring_avoided'] else '❌'} | {'—' if s['action_ok'] is None else '✅' if s['action_ok'] else '❌'} | {s['rounds']} | "
                  f"{s['evidence']} | {s['model_calls']} | {models_used(s.get('providers'))} | {s['seconds']}s | "
                  f"{s.get('harness') or '—'} | {s.get('commit') or '—'} | `{s['run_id']}` |")
    n = len(rows) or 1
    held = [r for r in rows if r["held_out"]]
    if held:
        md.append(f"\nHeld-out questions: root cause found in {sum(r['root_cause'] for r in held)}/{len(held)}.")
    md.append(f"\nRoot cause found in {sum(r['root_cause'] for r in rows)}/{n} cases; red herring avoided in "
              f"{sum(bool(r['red_herring_avoided']) for r in rows)}/{sum(r['red_herring_avoided'] is not None for r in rows)} "
              f"cases that define one; right action in "
              f"{sum(bool(r['action_ok']) for r in rows)}/{sum(r['action_ok'] is not None for r in rows)} cases that have one.")
    stem.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
