"""Shared memory and world state, backed by the Meterless engines (via meterless_bridge/).

- H-MEM holds what agents learn, with provenance: confirmed causes, ruled-out causes, lessons,
  and the Data Scout's map of how the sources join. It persists across runs, so the second
  investigation starts with what the first one learned.
- The World Model receives every trace event, so each run's state is event-sourced.

If the bridge isn't running, memory falls back to a local JSON file and the UI says so.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import uuid
from datetime import datetime

import httpx

from whychain.config import SETTINGS

WORLD_KINDS_SKIP = {"llm_call"}  # high-volume; still in trace.jsonl


class Memory:
    def __init__(self, url: str | None = None, local_path=None):
        self.url = (url or SETTINGS.meterless_url).rstrip("/")
        self.http = httpx.Client(timeout=15.0)
        self.local_path = local_path or SETTINGS.runs_dir / "_local_memory.json"
        self.engines: dict = {}
        self.embedder: str | None = None
        self.scout: dict | None = None
        self.online = self._health()
        self._q: queue.Queue = queue.Queue()
        if self.online:
            threading.Thread(target=self._drain, daemon=True).start()

    # ------------------------------------------------------------------ status
    def _health(self) -> bool:
        try:
            r = self.http.get(f"{self.url}/health", timeout=3.0)
            body = r.json()
            self.engines = body.get("engines", {})
            self.embedder = (body.get("embeddings") or {}).get("embedder")
            self.scout = body.get("scout")
            return r.status_code == 200 and bool(body.get("ok"))
        except Exception:
            return False

    def status(self) -> dict:
        return {"online": self.online, "url": self.url, "engines": self.engines, "embedder": self.embedder,
                "scout": self.scout, "backend": "meterless" if self.online else "local-json"}

    # ------------------------------------------------------------------ H-MEM
    def write(self, run_id: str, agent: str, kind: str, content: str, provenance: dict | None = None,
              confidence: float = 0.7, tags: list[str] | None = None) -> str:
        item = {"run_id": run_id, "agent": agent, "kind": kind, "content": content,
                "provenance": provenance or {}, "confidence": confidence, "tags": tags or []}
        if self.online:
            try:
                r = self.http.post(f"{self.url}/memory/write", json=item)
                r.raise_for_status()
                return str(r.json().get("id"))
            except Exception:
                pass
        return self._local_write(item)

    def recall(self, query: str, k: int = 6, tags: list[str] | None = None, kinds: list[str] | None = None,
               exclude_run_id: str | None = None) -> list[dict]:
        if self.online:
            try:
                body = {"query": query, "k": k}
                if tags:
                    body["tags"] = tags
                if kinds:
                    body["kinds"] = kinds
                if exclude_run_id:
                    body["exclude_run_id"] = exclude_run_id
                r = self.http.post(f"{self.url}/memory/recall", json=body)
                r.raise_for_status()
                return r.json().get("items", [])
            except Exception:
                pass
        return self._local_recall(query, k, tags)

    def consolidate(self, run_id: str) -> dict:
        if self.online:
            try:
                r = self.http.post(f"{self.url}/memory/consolidate", json={"run_id": run_id}, timeout=60.0)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                return {"error": str(e)[:200]}
        return {"promoted": 0, "details": "local fallback: no consolidation"}

    def audit(self, limit: int = 50) -> list[dict]:
        if self.online:
            try:
                r = self.http.get(f"{self.url}/memory/audit", params={"limit": limit})
                r.raise_for_status()
                body = r.json()
                return body.get("items", body.get("audit", body if isinstance(body, list) else []))
            except Exception:
                return []
        return self._local_load()[-limit:]

    def list(self, limit: int = 100) -> dict:
        """Browse memories, newest first (for the Memory page)."""
        if self.online:
            try:
                r = self.http.get(f"{self.url}/memory/list", params={"limit": limit})
                r.raise_for_status()
                return r.json()
            except Exception:
                pass
        items = list(reversed(self._local_load()))[:limit]
        return {"count": len(items), "items": items, "tiers": {}}

    # ------------------------------------------------------------------ World Model
    def record(self, run_id: str, kind: str, agent: str, data: dict, seq: int | None = None) -> None:
        if self.online and kind not in WORLD_KINDS_SKIP:
            ev = {"run_id": run_id, "type": kind, "actor": agent, "payload": _small(data)}
            if seq is not None:
                ev["event_id"] = f"{run_id}-{seq}"  # makes retries idempotent in the World Model
            self._q.put(ev)

    def world_state(self, run_id: str) -> dict:
        if not self.online:
            return {}
        try:
            r = self.http.get(f"{self.url}/world/state", params={"run_id": run_id})
            return r.json()
        except Exception:
            return {}

    def flush(self, timeout: float = 5.0) -> None:
        if self.online:
            done = threading.Event()
            self._q.put(done)
            done.wait(timeout)

    def _drain(self) -> None:
        while True:
            ev = self._q.get()
            if isinstance(ev, threading.Event):
                ev.set()
                continue
            try:
                self.http.post(f"{self.url}/world/events", json=ev, timeout=5.0)
            except Exception:
                pass

    # ------------------------------------------------------------------ local fallback
    def _local_load(self) -> list[dict]:
        try:
            return json.loads(self.local_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _local_write(self, item: dict) -> str:
        items = self._local_load()
        item = {**item, "id": uuid.uuid4().hex[:10], "created_at": datetime.now().isoformat(), "tier": "long"}
        items.append(item)
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_path.write_text(json.dumps(items, indent=1), encoding="utf-8")
        return item["id"]

    def _local_recall(self, query: str, k: int, tags: list[str] | None) -> list[dict]:
        words = set(re.findall(r"[a-z0-9\-]{3,}", query.lower()))
        scored = []
        for it in self._local_load():
            if tags and not set(tags) & set(it.get("tags", [])):
                continue
            text = (it.get("content", "") + " " + " ".join(it.get("tags", []))).lower()
            score = sum(1 for w in words if w in text)
            if score or tags:
                scored.append((score, it))
        scored.sort(key=lambda x: -x[0])
        return [dict(it, score=s) for s, it in scored[:k]]


WORLD_KEYS = {"status", "facts", "entities", "relations"}  # structured keys the World Model folds into its graph


def _small(data: dict, limit: int = 1500) -> dict:
    out = {}
    for k, v in data.items():
        if k in WORLD_KEYS:
            out[k] = v
            continue
        s = v if isinstance(v, (int, float, bool)) or v is None else str(v)
        out[k] = s[:limit] if isinstance(s, str) else s
    return out
