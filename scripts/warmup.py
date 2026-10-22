"""Warm up before a demo: wait for Zetaris and the web app, then run the slow first queries once.

  python scripts/warmup.py            (start.cmd runs it)

A cold Zetaris takes a few minutes to accept queries, and its first queries per source (JDBC to Postgres, reading the
file tables) are much slower than later ones. This waits for it, loads the catalog and the analysis engine's fact
tables for both datasets through Zetaris, then opens the app's pages once so its caches are warm too.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from whychain.config import SETTINGS  # noqa: E402

APP = "http://localhost:8000"


def wait(what: str, ok, timeout_s: int = 600) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            if ok():
                print(f"  {what}: ready after {time.time() - t0:.0f} s")
                return True
        except Exception:
            pass
        time.sleep(5)
    print(f"  {what}: NOT ready after {timeout_s} s")
    return False


def main() -> None:
    from whychain.config import utf8_output
    utf8_output()
    print("Warm-up")
    if SETTINGS.harness == "zetaris":
        from whychain.analysis import get_engine
        from whychain.analysis.supply import SupplyEngine
        from whychain.harness.zetaris import ZetarisHarness

        if wait("Zetaris", lambda: ZetarisHarness(dataset="olist").status()["ok"]):
            for ds in ("olist", "kaveri"):
                t0 = time.time()
                h = ZetarisHarness(dataset=ds)
                n = len(h.catalog())
                eng = get_engine(h)
                rows = len(eng.facts()) if eng else 0
                if ds == "kaveri":
                    SupplyEngine(h).lines()
                print(f"  {ds}: {n} tables, {rows:,} orders loaded through Zetaris in {time.time() - t0:.0f} s")
    else:
        print(f"  harness is {SETTINGS.harness!r}, not zetaris: nothing to warm there")
    if wait("web app", lambda: httpx.get(f"{APP}/api/status", timeout=60).status_code == 200):
        for ds in ("olist", "kaveri"):
            for page in ("overview", "sources", "regions" if ds == "olist" else None):
                if page:
                    t0 = time.time()
                    r = httpx.get(f"{APP}/api/{page}?dataset={ds}", timeout=300)
                    print(f"  app /api/{page} ({ds}): HTTP {r.status_code} in {time.time() - t0:.1f} s")
        st = httpx.get(f"{APP}/api/status", timeout=60).json()
        print(f"  harness: {st['harness']['name']} ({'ok' if st['harness'].get('ok') else 'NOT ok'}); memory: "
              f"{'Meterless' if st['memory'].get('online') else 'OFFLINE'}, embeddings "
              f"{st['memory'].get('embedder') or 'unknown'}")
    print("Warm-up done.")


if __name__ == "__main__":
    main()
