"""Does the embedder matter? The same findings go into two fresh Meterless bridges, one on H-MEM's reference mock
embedder and one on NVIDIA Nemotron embeddings. Then questions that share almost no words with the finding they
should recall are asked of both. Prints where the right finding ranks (1 = first).

  python scripts/recall_demo.py            (needs Node, and NVIDIA_API_KEY in .env for the Nemotron bridge)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "meterless_bridge"

FINDINGS = {
    "port": "CONFIRMED CAUSE: inbound purchase orders from Voltix (S-14) are late, held at the Chennai port by a dock "
            "workers' strike; 58.4% of the short units at the Pune DC.",
    "milk_run": "CONFIRMED CAUSE: Nashik deliveries are late since SwiftCargo (C02) moved the MUM-NSK lane to a "
                "consolidated milk run that leaves on Tuesdays and Fridays.",
    "promo": "CONFIRMED CAUSE: demand for SoundWave earbuds at the Mumbai DC spiked to 7.5x the planning baseline on "
             "promotion DIWALI26; reorder points were last reviewed in August.",
    "duplicates": "CONFIRMED CAUSE: 23 of 81 open Chennai orders are EDI copies of earlier orders with the same external "
                  "reference.",
    "rain": "RULED OUT: the IMD orange alert for heavy rain in Pune (19 to 22 October) did not slow the DC; dispatch "
            "times stayed normal.",
    "stock_wait": "MEASURED: carrier transit to Hyderabad stayed at 2 days; dispatch slowed because orders waited for "
                  "stock at the warehouse.",
}
QUESTIONS = {
    "Why can't our suppliers' goods get into the country?": "port",
    "Why does the courier only go to that town twice a week now?": "milk_run",
    "Did a marketing campaign empty our headphone shelves?": "promo",
    "Are customers' systems sending us the same purchase twice?": "duplicates",
    "Did the monsoon hold up the warehouse?": "rain",
    "Why are parcels leaving the warehouse later than they used to?": "stock_wait",
}


def start(mode: str, port: int, data: Path) -> subprocess.Popen:
    env = {**os.environ, "METERLESS_BRIDGE_PORT": str(port), "METERLESS_BRIDGE_DATA_DIR": str(data),
           "METERLESS_BRIDGE_QUIET": "1", "METERLESS_EMBEDDER": mode}
    p = subprocess.Popen("npx tsx server.ts", cwd=BRIDGE, env=env, shell=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    for _ in range(90):
        try:
            if httpx.get(f"http://localhost:{port}/health", timeout=2).status_code == 200:
                return p
        except httpx.HTTPError:
            time.sleep(1)
    raise RuntimeError(f"bridge ({mode}) did not start on :{port}")


def stop(p: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    else:
        p.terminate()


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="recall-demo-"))
    procs, ports = {}, {"mock": 18911, "nvidia": 18912}
    ranks: dict[str, dict[str, int | None]] = {"mock": {}, "nvidia": {}}
    embedders = {}
    try:
        for mode, port in ports.items():
            procs[mode] = start(mode, port, tmp / mode)
            url = f"http://localhost:{port}"
            embedders[mode] = httpx.get(f"{url}/health").json().get("embeddings", {}).get("embedder")
            ids = {}
            for key, text in FINDINGS.items():
                r = httpx.post(f"{url}/memory/write", timeout=60, json={
                    "run_id": "recall-demo", "agent": "demo", "kind": "finding", "content": text,
                    "provenance": {"source": "recall-demo"}})
                ids[r.json()["id"]] = key
            for q, want in QUESTIONS.items():
                items = httpx.post(f"{url}/memory/recall", timeout=60,
                                   json={"query": q, "k": len(FINDINGS), "min_score": 0}).json()["items"]
                order = [ids.get(m["id"]) for m in items]
                ranks[mode][q] = order.index(want) + 1 if want in order else None
    finally:
        for p in procs.values():
            stop(p)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"Recall demo, {time.strftime('%Y-%m-%d %H:%M')}: {len(FINDINGS)} findings, {len(QUESTIONS)} reworded questions.")
    print(f"  mock:   {embedders.get('mock')}\n  nvidia: {embedders.get('nvidia')}\n")
    print(f"{'question':<66} {'wanted':<11} {'mock':>5} {'Nemotron':>9}")
    for q, want in QUESTIONS.items():
        print(f"{q:<66} {want:<11} {str(ranks['mock'][q]):>5} {str(ranks['nvidia'][q]):>9}")
    for mode in ("mock", "nvidia"):
        top1 = sum(1 for r in ranks[mode].values() if r == 1)
        print(f"\n{'Nemotron' if mode == 'nvidia' else 'mock'}: right finding first in {top1}/{len(QUESTIONS)}", end="")
    print()


if __name__ == "__main__":
    main()
