"""Check every integration before a demo:  python -m whychain.doctor"""
from __future__ import annotations

import time

import httpx
import psycopg

from whychain.config import SETTINGS, is_placeholder
from whychain.llm import LLM


def line(ok: bool | None, name: str, detail: str) -> None:
    mark = {True: "OK  ", False: "FAIL", None: "--  "}[ok]
    print(f"[{mark}] {name:<22} {detail}")


def main() -> None:
    print(f"WhyChain doctor · harness preference: {SETTINGS.harness}\n")
    # data
    try:
        with psycopg.connect(SETTINGS.pg_dsn, connect_timeout=3) as c:
            n = c.execute("SELECT COUNT(*) FROM sales_orders").fetchone()[0]
        line(True, "Postgres ERP", f"{n} sales orders")
    except Exception as e:
        line(False, "Postgres ERP", f"{str(e)[:90]}  (docker compose up -d; python -m datagen.load_postgres)")
    gen = SETTINGS.data_dir / "manifest.json"
    line(gen.exists(), "Generated files", str(SETTINGS.data_dir) if gen.exists() else "run python -m datagen.generate")
    if not is_placeholder(SETTINGS.zetaris_api_key):
        from whychain.harness.zetaris import ZetarisHarness

        st = ZetarisHarness().status()
        line(st["ok"], "Zetaris", f"{st.get('url')} {st.get('error', '')}")
    else:
        line(None, "Zetaris", "ZETARIS_API_KEY not set (local DuckDB harness will be used)")
    # memory
    try:
        h = httpx.get(f"{SETTINGS.meterless_url}/health", timeout=3).json()
        line(bool(h.get("ok")), "Meterless bridge", f"engines {h.get('engines')} · {h.get('memory', {}).get('records')} memories")
    except Exception:
        line(False, "Meterless bridge", f"not reachable at {SETTINGS.meterless_url} (cd meterless_bridge && npm start)")
    # models
    llm = LLM()
    for name, p in llm.providers.items():
        if not p.configured:
            line(None, f"model: {name}", "not configured")
            continue
        t0 = time.time()
        try:
            r = llm._client(p).chat.completions.create(model=p.model, max_tokens=5,
                                                        messages=[{"role": "user", "content": "Reply OK"}])
            line(True, f"model: {name}", f"{p.model} · {time.time() - t0:.1f}s · {r.choices[0].message.content!r}")
        except Exception as e:
            line(False, f"model: {name}", f"{p.model} · {str(e)[:100]}")


if __name__ == "__main__":
    main()
