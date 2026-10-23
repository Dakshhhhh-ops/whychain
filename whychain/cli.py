"""Run an investigation from the terminal.

  python -m whychain.cli "Why are so many Pune orders at risk of missing their promised date next week?"
  python -m whychain.cli --harness local "..."
"""
from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.markdown import Markdown

from whychain.graph import investigate

console = Console()
COLORS = {"Planner": "cyan", "Evaluator": "magenta", "Data Scout": "blue", "Memory": "green",
          "Risk & Action": "yellow", "Reporter": "bright_white", "System": "dim"}


def show(ev: dict) -> None:
    kind, agent = ev["kind"], ev["agent"]
    color = next((c for k, c in COLORS.items() if agent.startswith(k)), "white")
    t = f"[dim]{ev['t']:>6.1f}s[/dim] [{color}]{agent:<18}[/{color}]"
    if kind == "plan":
        console.print(f"{t} plan round {ev['round']}: {ev.get('reasoning', '')}")
        for task in ev["tasks"]:
            console.print(f"         [{task['id']}] ({task['agent']}) {task['statement']}")
    elif kind == "query":
        mark = "[green]ok[/green]" if ev["ok"] else f"[red]error[/red] {ev.get('error', '')[:120]}"
        console.print(f"{t} SQL {ev.get('evidence_id') or ''} {mark} ({ev['rows']} rows, {ev['ms']} ms): "
                      f"{ev['sql'][:140].replace(chr(10), ' ')}")
    elif kind == "read_doc":
        console.print(f"{t} read {ev['doc_id']}")
    elif kind == "finding":
        console.print(f"{t} [bold]{ev['verdict']}[/bold]: {ev['claim'][:220]}")
    elif kind == "evaluation":
        console.print(f"{t} [bold]{ev['decision']}[/bold] {ev.get('verdicts')} explained={ev.get('explained')}")
        for g in ev.get("gaps") or []:
            console.print(f"         gap: {g.get('what')}")
    elif kind == "escalation":
        console.print(f"{t} [yellow]ESCALATE[/yellow] {ev['action_id']} {ev.get('title')}: {ev['reasons']}")
    elif kind in ("llm_fallback", "agent_error", "run_error", "quote_rejected", "join_rejected"):
        console.print(f"{t} [red]{kind}[/red] {ev.get('error') or ev.get('quote') or ev}")
    elif kind == "join_test":
        console.print(f"{t} join {ev['left']} = {ev['right']}: {ev['match_rate']} {'kept' if ev['kept'] else 'dropped'}")
    elif kind in ("agent_start", "agent_skip"):
        console.print(f"{t} {ev.get('note') or ev.get('statement') or 'started'}")
    elif kind == "run_end":
        console.print(f"{t} done: {ev['calls']} model calls {ev['by_provider']}, {ev['fallbacks']} fallback{'' if ev['fallbacks'] == 1 else 's'}")


def main(argv=None) -> int:
    from whychain.config import utf8_output
    utf8_output()
    ap = argparse.ArgumentParser(description="WhyChain: explain why supply chain problems happen")
    ap.add_argument("question")
    ap.add_argument("--harness", choices=["zetaris", "local"], default=None)
    ap.add_argument("--dataset", choices=["olist", "kaveri"], default=None, help="default: olist")
    ap.add_argument("--private-docs", action="store_true", default=None,
                    help="read the written record only on this machine (Meterless Nightshift)")
    args = ap.parse_args(argv)
    ctx = notes = None
    if args.private_docs:
        from whychain.graph import make_ctx
        ctx, notes = make_ctx(args.harness, show, dataset=args.dataset, private_docs=True)
    res = investigate(args.question, args.harness, listener=show, dataset=args.dataset, ctx=ctx, notes=notes)
    console.rule(res.get("headline") or "Result")
    console.print(Markdown(res.get("brief", "")))
    console.print(f"\n[dim]run {res['run_id']} · trace: runs/{res['run_id']}/trace.jsonl[/dim]")
    return 0 if res["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
