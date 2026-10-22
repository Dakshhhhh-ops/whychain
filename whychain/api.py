"""HTTP API for the WhyChain web app.

  python -m whychain.api            # http://localhost:8000 (serves the built frontend too)

Runs execute in background threads. Their trace events stream to the browser over
Server-Sent Events, so the UI shows each agent's work as it happens.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from whychain import graph
from whychain.agents.deliver import deliver
from whychain.config import ROOT, SETTINGS
from whychain.datasets import DATASETS, get_dataset
from whychain.harness import get_harness
from whychain.llm import LLM
from whychain.memory import Memory
from whychain.overview import overview
from whychain.trace import Tracer

FRONTEND = ROOT / "frontend" / "dist"

# Tests swap this for a factory that builds a context with a scripted model.
CTX_FACTORY = graph.make_ctx


@dataclass
class LiveRun:
    run_id: str
    question: str
    dataset: str = ""
    started: float = field(default_factory=time.time)
    events: list = field(default_factory=list)
    done: bool = False
    result: dict | None = None

    def push(self, ev: dict) -> None:
        self.events.append(ev)


LIVE: dict[str, LiveRun] = {}
app = FastAPI(title="WhyChain API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------ helpers
def run_dir(run_id: str) -> Path:
    d = (SETTINGS.runs_dir / run_id).resolve()
    if SETTINGS.runs_dir.resolve() not in d.parents:
        raise HTTPException(400, "bad run id")
    return d


def load_result(run_id: str) -> dict:
    p = run_dir(run_id) / "result.json"
    if not p.exists():
        raise HTTPException(404, "run not found")
    res = json.loads(p.read_text(encoding="utf-8"))
    if res.get("status") == "ok" and len(str(res.get("brief") or "").strip()) < 200:
        res["status"] = "incomplete"  # runs saved before this check existed
    dec = run_dir(run_id) / "decisions.json"
    if dec.exists():
        decisions = json.loads(dec.read_text(encoding="utf-8"))
        for a in res.get("actions", []):
            if a["id"] in decisions:
                a["decision"] = decisions[a["id"]]["decision"]
                a["decision_note"] = decisions[a["id"]].get("note")
    return res


def summary(res: dict) -> dict:
    v = res.get("verdicts", {})
    causes = [f for f in res.get("findings", []) if v.get(f["task_id"], {}).get("status") == "confirmed"
              and v[f["task_id"]].get("is_cause", True)]
    ev = res.get("_events_meta", {})
    return {"run_id": res["run_id"], "question": res.get("question"), "headline": res.get("headline", ""),
            "dataset": res.get("dataset") or "kaveri",
            "status": res.get("status"), "harness": res.get("harness"), "rounds": res.get("round", 0),
            "causes": len(causes), "evidence": len(res.get("evidence", {})),
            "actions": len(res.get("actions", [])),
            "needs_approval": sum(1 for a in res.get("actions", []) if a.get("needs_approval")),
            "model_calls": res.get("llm", {}).get("calls", 0), **ev}


def trace_events(run_id: str) -> list[dict]:
    p = run_dir(run_id) / "trace.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


_status_cache: dict = {}
_shared: dict = {}
_overview_cache: dict = {}


def shared_harness(dataset: str | None = None):
    """One long-lived harness per dataset for the read-only pages (status, overview, sources)."""
    key = get_dataset(dataset).key
    hit = _shared.get(key)
    # a fallback (Zetaris was unreachable) is re-checked after a minute, a healthy harness after five
    if not hit or time.time() - hit["t"] > (60 if hit["h"].name != SETTINGS.harness else 300):
        h, notes = get_harness(dataset=key)
        hit = _shared[key] = {"h": h, "notes": notes, "t": time.time()}
    return hit["h"], hit["notes"]


# ------------------------------------------------------------------ routes
@app.get("/api/datasets")
def datasets() -> dict:
    return {"default": get_dataset().key, "datasets": [d.public() for d in DATASETS.values()]}


@app.get("/api/status")
def status(dataset: str | None = None) -> dict:
    ds = get_dataset(dataset)
    hit = _status_cache.get(ds.key)
    if hit and hit["t"] > time.time() - 20:
        return hit["v"]
    harness, notes = shared_harness(ds.key)
    mem = Memory()
    manifest = ds.manifest
    llm = LLM()
    v = {"harness": {"name": harness.name, "notes": notes, **harness.status()},
         "memory": mem.status(),
         "models": {k: {"configured": p.configured, "model": p.model} for k, p in llm.providers.items()},
         # Ollama alone doesn't count: on small machines it often can't load a model
         "models_ready": any(p.configured for k, p in llm.providers.items() if k != "ollama"),
         "preview": os.environ.get("WHYCHAIN_PREVIEW") == "1",
         "as_of": ds.as_of, "company": manifest.get("company"), "row_counts": manifest.get("row_counts", {}),
         "dataset": ds.public()}
    _status_cache[ds.key] = {"t": time.time(), "v": v}
    return v


@app.get("/api/samples")
def samples(dataset: str | None = None) -> list[dict]:
    return get_dataset(dataset).samples


class NewRun(BaseModel):
    question: str = Field(min_length=8, max_length=500)
    dataset: str | None = None


@app.post("/api/runs")
def start_run(body: NewRun) -> dict:
    tracer = Tracer()
    ds = get_dataset(body.dataset)
    live = LiveRun(tracer.run_id, body.question.strip(), ds.key)
    tracer.listeners.append(live.push)
    LIVE[tracer.run_id] = live

    def work():
        try:
            ctx, notes = CTX_FACTORY(tracer=tracer, dataset=ds.key)
            live.result = graph.investigate(live.question, ctx=ctx, notes=notes)
        except Exception as e:  # make the failure visible to the UI
            tracer.event("run_error", "System", error=f"{type(e).__name__}: {e}")
            tracer.event("run_end", "System", status="error", calls=0, by_provider={}, fallbacks=0, tokens=0)
            tracer.save("result.json", {"run_id": tracer.run_id, "question": live.question, "status": "error",
                                        "dataset": ds.key, "brief": f"Run failed: {e}"})
        finally:
            live.done = True

    threading.Thread(target=work, daemon=True, name=f"run-{tracer.run_id}").start()
    return {"run_id": tracer.run_id, "dataset": ds.key}


@app.get("/api/runs")
def list_runs(limit: int = 50, dataset: str | None = None) -> list[dict]:
    out = []
    for run_id, live in sorted(LIVE.items(), reverse=True):
        if not live.done and (not dataset or live.dataset == dataset):
            out.append({"run_id": run_id, "question": live.question, "status": "running", "headline": "",
                        "dataset": live.dataset})
    if SETTINGS.runs_dir.exists():
        for d in sorted((p for p in SETTINGS.runs_dir.iterdir() if (p / "result.json").exists()), reverse=True):
            if len(out) >= limit:
                break
            try:
                res = load_result(d.name)
                ev = trace_events(d.name)
                if ev:
                    res["_events_meta"] = {"started_at": ev[0].get("ts"), "duration_s": ev[-1].get("t"),
                                           "queries": sum(1 for e in ev if e["kind"] == "query")}
                row = summary(res)
                if not dataset or row["dataset"] == dataset:
                    out.append(row)
            except Exception:
                continue
    return out


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    live = LIVE.get(run_id)
    if live and not live.done:
        return {"run_id": run_id, "question": live.question, "status": "running", "dataset": live.dataset}
    return load_result(run_id)


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, replay: float = 0.0):
    """SSE stream of trace events. Live runs stream as they happen; finished runs replay from the trace
    (replay=N plays it back N times faster than it happened; 0 sends everything at once)."""
    live = LIVE.get(run_id)
    if not live and not (run_dir(run_id) / "trace.jsonl").exists():
        raise HTTPException(404, "run not found")

    async def gen():
        if live:
            i = 0
            last_beat = time.time()
            while True:
                batch = live.events[i:]
                for ev in batch:
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                i += len(batch)
                if live.done and i >= len(live.events):
                    break
                if time.time() - last_beat > 10:
                    yield ": keep-alive\n\n"
                    last_beat = time.time()
                await asyncio.sleep(0.2)
        else:
            prev_t = 0.0
            for ev in trace_events(run_id):
                if replay > 0:
                    await asyncio.sleep(min(max(ev.get("t", 0) - prev_t, 0) / replay, 1.5))
                    prev_t = ev.get("t", 0)
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Decision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    note: str | None = Field(default=None, max_length=500)


@app.get("/api/runs/{run_id}/trace")
def run_trace(run_id: str) -> list[dict]:
    live = LIVE.get(run_id)
    return list(live.events) if live else trace_events(run_id)


@app.get("/api/runs/{run_id}/trace.jsonl")
def run_trace_file(run_id: str):
    p = run_dir(run_id) / "trace.jsonl"
    if not p.exists():
        raise HTTPException(404, "run not found")
    return FileResponse(p, media_type="application/x-ndjson", filename=f"whychain-{run_id}.jsonl")


@app.post("/api/runs/{run_id}/actions/{action_id}")
def decide(run_id: str, action_id: str, body: Decision) -> dict:
    res = load_result(run_id)
    action = next((a for a in res.get("actions", []) if a["id"] == action_id), None)
    if not action:
        raise HTTPException(404, "action not found")
    path = run_dir(run_id) / "decisions.json"
    decisions = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    decisions[action_id] = {"decision": body.decision, "note": body.note, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path.write_text(json.dumps(decisions, indent=1), encoding="utf-8")
    Memory().write(run_id, "Manager", "fact",
                   f"Manager {body.decision} action '{action.get('title')}' for question: {res.get('question')}"
                   + (f" Note: {body.note}" if body.note else ""),
                   {"source": "manager", "origin": "user_correction", "action_id": action_id}, 0.95,
                   ["decision", body.decision])
    if body.decision == "approved":
        start_delivery(run_id, res, action, body.note)
    return {"ok": True, "action_id": action_id, **decisions[action_id]}


# ------------------------------------------------------------------ action execution
DELIVERY: dict[tuple[str, str], dict] = {}


def delivery_dir(run_id: str, action_id: str) -> Path:
    return run_dir(run_id) / "actions" / f"{run_id}-{action_id}"


def start_delivery(run_id: str, res: dict, action: dict, note: str | None) -> None:
    key = (run_id, action["id"])
    if key in DELIVERY or (delivery_dir(run_id, action["id"]) / "deliverables.json").exists():
        return
    tracer = Tracer(run_id=f"{run_id}-{action['id']}", runs_dir=run_dir(run_id) / "actions")
    state = {"status": "running", "events": []}
    tracer.listeners.append(state["events"].append)
    DELIVERY[key] = state

    def work():
        try:
            ctx, _ = CTX_FACTORY(tracer=tracer, dataset=res.get("dataset") or "kaveri")
            deliver(ctx, res, action, note)
            state["status"] = "done"
        except Exception as e:
            state.update(status="error", error=f"{type(e).__name__}: {e}")
            tracer.save("deliverables.json", {"action_id": action["id"], "status": "error", "error": state["error"],
                                              "deliverables": []})

    threading.Thread(target=work, daemon=True, name=f"deliver-{run_id}-{action['id']}").start()


@app.get("/api/runs/{run_id}/actions/{action_id}/deliverables")
def get_deliverables(run_id: str, action_id: str) -> dict:
    p = delivery_dir(run_id, action_id) / "deliverables.json"
    state = DELIVERY.get((run_id, action_id))
    if state and state["status"] == "running":
        evs = state["events"]
        last = next((e for e in reversed(evs) if e["kind"] in ("query", "read_doc", "agent_start")), None)
        return {"status": "running", "steps": sum(1 for e in evs if e["kind"] in ("query", "read_doc")),
                "last": (last or {}).get("thought") or (last or {}).get("note") or ""}
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"status": "none", "deliverables": []}


@app.get("/api/runs/{run_id}/actions/{action_id}/deliverables/{index}")
def download_deliverable(run_id: str, action_id: str, index: int):
    p = delivery_dir(run_id, action_id) / "deliverables.json"
    if not p.exists():
        raise HTTPException(404, "no deliverables")
    items = json.loads(p.read_text(encoding="utf-8")).get("deliverables", [])
    if not 0 <= index < len(items):
        raise HTTPException(404, "no such deliverable")
    d = items[index]
    slug = re.sub(r"[^a-z0-9]+", "-", d.get("title", "deliverable").lower()).strip("-")[:60] or "deliverable"
    if d["kind"] == "table":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(d["columns"])
        w.writerows(d["rows"])
        body, media, ext = buf.getvalue(), "text/csv", "csv"
    elif d["kind"] == "email":
        body = f"To: {d.get('to')}\nSubject: {d.get('subject')}\n\n{d.get('body')}\n"
        media, ext = "text/plain", "txt"
    else:
        body, media, ext = d.get("body", ""), "text/plain", "txt"
    return Response(body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{action_id}-{slug}.{ext}"'})


@app.get("/api/runs/{run_id}/world")
def run_world(run_id: str) -> dict:
    return Memory().world_state(run_id)


@app.get("/api/memory")
def memory(limit: int = 200, dataset: str | None = None) -> dict:
    m = Memory()
    out = m.list(limit)
    if dataset:
        keys = set(DATASETS)

        def mine(item: dict) -> bool:
            tags = set(item.get("tags") or [])
            return dataset in tags or (dataset == "kaveri" and not tags & keys)  # older memories predate tags

        out["items"] = [i for i in out.get("items", []) if mine(i)]
        out["count"] = len(out["items"])
    return {"status": m.status(), **out}


@app.get("/api/overview")
def get_overview(dataset: str | None = None) -> dict:
    key = get_dataset(dataset).key
    hit = _overview_cache.get(key)
    if hit and hit["t"] > time.time() - 120:
        return hit["v"]
    harness, _ = shared_harness(key)
    v = overview(harness)
    _overview_cache[key] = {"t": time.time(), "v": v}
    return v


_regions_cache: dict = {}


@app.get("/api/regions")
def get_regions(dataset: str | None = None, month: str | None = None) -> dict:
    """Late deliveries by customer state (marketplace data), for one purchase month vs the six months before."""
    from whychain.overview import olist_regions

    ds = get_dataset(dataset)
    if ds.key != "olist":
        raise HTTPException(404, "regions are available for the marketplace dataset")
    if month and not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", month):
        raise HTTPException(400, "month must look like 2018-03")
    key = (ds.key, month)
    if key not in _regions_cache:
        harness, _ = shared_harness(ds.key)
        _regions_cache[key] = olist_regions(harness, month)
    return _regions_cache[key]


@app.get("/api/sources")
def sources(dataset: str | None = None) -> dict:
    from types import SimpleNamespace

    from whychain.agents.nodes import catalog_fingerprint

    ds = get_dataset(dataset)
    harness, notes = shared_harness(ds.key)
    tables = [{"source": t.source, "table": t.table, "fqn": t.fqn, "row_count": t.row_count,
               "columns": [{"name": c.name, "type": c.type} for c in t.columns]} for t in harness.catalog()]
    fp = catalog_fingerprint(SimpleNamespace(harness=harness))
    path = SETTINGS.runs_dir / f"_data_map_{fp}.json"
    data_map = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    return {"harness": harness.name, "notes": notes, "tables": tables, "source_notes": ds.source_notes,
            "data_map": data_map, "dataset": ds.public()}


@app.get("/api/evals")
def evals() -> dict:
    p = ROOT / "evals" / "results.json"
    return {"results": json.loads(p.read_text(encoding="utf-8")) if p.exists() else []}


# ------------------------------------------------------------------ frontend
if FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (FRONTEND / path).resolve()
        if path and FRONTEND.resolve() in f.parents and f.is_file():
            return FileResponse(f)
        return FileResponse(FRONTEND / "index.html")


def main() -> None:
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"WhyChain on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
