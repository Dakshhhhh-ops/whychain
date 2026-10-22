"""Run trace: every agent step is an event.

Events go to runs/<run_id>/trace.jsonl, to any live listener (the UI), and to the Meterless
World Model (via the bridge) so the investigation's state is event-sourced.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from whychain.config import SETTINGS


class Tracer:
    def __init__(self, run_id: str | None = None, world=None, runs_dir: Path | None = None):
        self.run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.dir = (runs_dir or SETTINGS.runs_dir) / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "trace.jsonl"
        self.events: list[dict] = []
        self.listeners: list[Callable[[dict], None]] = []
        self.world = world
        self._lock = threading.Lock()
        self._t0 = time.time()

    def event(self, kind: str, agent: str, **data) -> dict:
        with self._lock:
            ev = {"seq": len(self.events) + 1, "t": round(time.time() - self._t0, 2), "ts": datetime.now().isoformat(),
                  "run_id": self.run_id, "kind": kind, "agent": agent, **data}
            self.events.append(ev)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, default=str) + "\n")
        if self.world is not None:
            self.world.record(self.run_id, kind, agent, data, ev["seq"])
        for fn in list(self.listeners):
            try:
                fn(ev)
            except Exception:
                pass
        return ev

    def save(self, name: str, obj) -> Path:
        p = self.dir / name
        if isinstance(obj, str):
            p.write_text(obj, encoding="utf-8")
        else:
            p.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
        return p

    def llm_stats(self) -> dict:
        calls = [e for e in self.events if e["kind"] == "llm_call"]
        by_provider: dict[str, int] = {}
        for e in calls:
            by_provider[e["provider"]] = by_provider.get(e["provider"], 0) + 1
        return {"calls": len(calls), "by_provider": by_provider,
                "fallbacks": sum(1 for e in self.events if e["kind"] == "llm_fallback"),
                "tokens": sum(e.get("prompt_tokens", 0) + e.get("completion_tokens", 0) for e in calls)}
