# Build Plan — Supply Chain "Why" Agent

## Status: night of 21 Oct (before the build window; declare as pre-existing)

**Built and tested without API keys:**
- Simulated company data across 6 sources (Postgres ERP + CSV/JSON files + documents), with 5 planted scenarios.
- Data harness: Zetaris REST client (primary) + local DuckDB fallback with the same table names and SQL dialect.
- Full agent graph (LangGraph): Memory → Data Scout → Planner → parallel Investigators / Doc Analyst → Evaluator (revise loop) → Risk & Action (escalation) → Reporter → Memory.
- Meterless bridge (`meterless_bridge/`) running the real H-MEM and World Model engines. Memory carries over between runs (verified).
- Web app: FastAPI backend (`python -m whychain.api`) + React/TypeScript/Tailwind frontend (`frontend/`). It has a control tower with live problem detection and an "Investigate why" button on each problem, a live investigation view (agent feed, hypothesis board, brief with evidence links, approve/reject), a supply network map that highlights the cause path, plus memory and data-source pages.
- CLI, scoring script (`evals/`), wiring test (`tests/`), health check (`python -m whychain.doctor`), README.
- Ideas borrowed (no code) from harshbpathak/prism.ai: the control-tower dashboard and the network ("digital twin") map. That repo has no license, so none of its code is copied.

**Update, 22 Oct ~01:30 IST:**
- NVIDIA NIM key added. All agents run on **Nemotron 3.5 Super**: thinking on for Planner, Evaluator and Reporter, off for data and document work. NIM JSON mode, retries, and repairs for malformed replies are in place. Ollama is disabled (not enough RAM).
- Real runs work end to end, taking about 8–12 minutes. The second real run ranked the Chennai port strike first (~74% of at-risk orders) after one Evaluator send-back.
- **Merged with Optiagent** (declared pre-existing component, pinned commit). The Risk & Action agent and the Action Executor call `optimize`, which builds a transportation LP from live data and returns a **proven-optimal** rebalancing plan with a duality certificate. Evidence ids are `A.P1` and `X.P1`. If the first solver's dual prices are loose, the certificate is retried with HiGHS's.
- New data tables: `wms.transfer_lanes`, `procurement.expedite_options`.
- Tests: 8 passing (wiring, optimizer, executor repairs, action executor).

**Update, 22 Oct ~02:00–03:05 IST. This was still BEFORE the build window, which opened at 05:30 IST, and is declared as pre-existing in the README:**
- The Olist real dataset loader and the dataset switch. Kaveri (simulated) stays as the second dataset.
- 16 researched external events and 13 news briefs (`datagen/olist_events.json`), and the event markers on the control-tower chart.
- Agent fixes from the first real runs:
  - a normal baseline and segment breakdowns;
  - is_cause for measured mechanisms;
  - currency per dataset;
  - retries.

**In-window work (from 22 Oct 13:25 IST):** see the README ("Pre-existing work" and "Known limitations") and `git log`.

**Earlier note (now resolved):** real model runs were untested because no API keys were in `.env`, and Ollama couldn't load a model (only 1.7 GB of RAM free, plus a CUDA error).

**Next, in order:**
1. Free RAM: restart the laptop, then keep Chrome and other apps closed while working.
2. Add `NVIDIA_API_KEY` and `XAI_API_KEY` to `.env`, run `python -m whychain.doctor`, then `python -m evals.run_evals`. Tune prompts on the failures.
3. Zetaris: sign up, install locally (needs about 8 GB RAM for Docker, so it may need the AWS option), put the API key and org id in `.env`, then run `python scripts/register_zetaris.py --apply`.
4. Install Meterless Nightshift and put its proxy token in `.env`.
5. Install Cursor and do the remaining work in it.
6. Connect the GitHub repo (user will send the URL). No commits or pushes until then.

---

Working name: **WhyChain** (rename any time)
Track: **2 — The Agent That Can Explain Why**
Builder: solo · Build window: 22 Oct 05:30 IST → **submit by 27 Oct evening IST** (hard deadline is 28 Oct 05:15 IST)

---

## 1. The pitch (one paragraph)

A supply chain planner asks: *"Why are 23% of Pune orders at risk next week, and what should I do?"*
Today, answering that means opening the ERP, the warehouse export, the carrier portal, supplier emails and contracts, and stitching it together by hand over a few hours.
WhyChain sends a team of agents that query all of those sources **where they live** (through Zetaris), argue about the cause, throw out claims that lack evidence, and return a one-page action brief. Every number in the brief links back to the query or document that produced it.

**Target user:** supply chain or operations planners at mid-size distributors and manufacturers.
**Output they can use:** root cause, evidence, recommended fixes with cost and impact, and an approve/reject button for each fix.

---

## 2. Data: one fictional company, five separate sources

Company: a mid-size Indian consumer-electronics distributor with 3 warehouses (Mumbai, Pune, Chennai), about 40 suppliers, about 200 SKUs and 4 carriers.
A seeded Python generator creates the data, so each scenario can be regenerated and nothing is hand-edited.

| # | Source | Format / where it lives | Contents |
|---|--------|-------------------------|----------|
| 1 | ERP | Postgres (Docker) | customers, sales orders, order lines, promised dates |
| 2 | Warehouse system | CSV / Parquet files | daily inventory snapshots per SKU per warehouse, reorder points |
| 3 | Carrier feed | JSON files | shipments, tracking events, delays |
| 4 | Procurement | Excel / CSV | purchase orders, supplier lead times, actual receipt dates |
| 5 | Contracts and emails | PDF / text | SLAs, late-delivery penalties, force-majeure clauses, supplier delay emails |
| 6 | External signals (optional) | JSON snapshot | port congestion, weather, strikes |

**Planted scenarios** (each has a known true cause, used for testing and the demo):
- A: Supplier delay (Chennai port strike) → Pune stockout → late orders. Best fix: transfer stock from Mumbai.
- B: One carrier's on-time rate drops on one route after a contract change.
- C: Promo-driven demand spike that reorder points never accounted for.
- D: **Red herring**: duplicate orders inflate the risk number. The evaluator should catch it.
- E: Two causes at once (supplier delay plus carrier issue). Tests whether agents stop at the first answer.

---

## 3. Agent architecture (LangGraph, Python)

```
              ┌─────────────── question ───────────────┐
              ▼                                         │
         PLANNER (Grok) ── hypotheses + plan            │
              │   ▲                                     │
   dispatches │   │ revise plan / new hypotheses        │
              ▼   │                                     │
   ┌──────────────┴───────────────┐                     │
   │ DATA SCOUT  → maps sources,  │                     │
   │   joins, columns via Zetaris │                     │
   │ INVESTIGATORS (NIM) → SQL    │  ◄── retry with     │
   │   across sources, self-fix   │      SQL error      │
   │ DOC ANALYST (Meterless local │                     │
   │   GPU) → contracts, emails   │                     │
   └──────────────┬───────────────┘                     │
                  ▼                                     │
         EVALUATOR / SKEPTIC (Grok)                     │
           re-runs queries, looks for counter-evidence  │
           ├─ weak or unsupported → back to PLANNER ────┘ (max 3 loops)
           ▼
         RISK & ACTION AGENT → fixes, cost, penalties at stake
           ├─ low confidence or high cost → ESCALATE to human (approve/reject in UI)
           ▼
         REPORTER → action brief with citations
```

Shared across all agents:
- **Meterless memory (H-MEM)**: findings with provenance, so later runs remember things like "supplier S-14 is late every monsoon".
- **Meterless World Model**: every agent step is recorded as an event. This becomes the trace log and the UI timeline.

Why judges should see this as strong rather than a pipeline: the planner dispatches dynamically, the evaluator rejects claims and loops back, failed SQL is retried, high-risk fixes go to a human, and memory carries over between runs.

---

## 4. Sponsor technology: what each one actually does

| Sponsor | Role in WhyChain | What's unique about it |
|---------|-----------------|------------------------|
| **Zetaris** | The only way agents touch company data. All 5 sources are registered in Zetaris, and agents run federated SQL that joins Postgres, CSV and JSON in one query. The Data Scout uses the Zetaris catalog to work out how the sources connect. | Every claim in the brief cites the exact Zetaris query behind it, so the explanation is checkable. No data is copied into a vector DB or a warehouse. |
| **Meterless** | H-MEM for shared, compounding memory. World Model for the event trace. Nightshift serves a local model on the GPU for reading documents. | Contracts and supplier emails never leave the machine, which suits sensitive commercial terms. Memory improves answers across runs. |
| **NVIDIA** | NIM API (Llama / Nemotron) for the investigator agents. Local GPU (CUDA) runs Nightshift. NIM embeddings for document search. | Cloud and local NVIDIA compute, each used where it fits. |
| **Cursor** | Development environment for the whole build. Use Cursor's agent for real tasks so the slides can describe it accurately. | — |

Reliability fallbacks (agents keep working during judging):
- Model calls: Grok → NIM → local Nightshift, in that order.
- Data: if Zetaris is unreachable, a local DuckDB adapter with the same interface takes over. The UI shows clearly when it does. Zetaris stays the main path.

---

## 5. Day-by-day plan (IST)

**Tonight, 21 Oct (no code):** see section 7.

**Day 1, 22 Oct: get one question answered end to end**
- Create a separate git repo in this folder, plus `.gitignore`, `.env.example` and the folder layout.
- Data generator for scenario A. Load into Postgres and files. Register all sources in Zetaris.
- Run one federated query from Python against Zetaris.
- Model clients for Grok, NIM and Nightshift, with fallback.
- One agent answers the scenario A question, badly but end to end.

**Day 2, 23 Oct: the multi-agent loop**
- LangGraph graph: planner, scout, investigators and evaluator, with the reject-and-revise loop.
- SQL self-repair on errors. Trace logging (JSONL) from the start.

**Day 3, 24 Oct: memory, documents, actions**
- Meterless H-MEM and World Model wired in.
- Doc analyst on contracts and emails (local Nightshift).
- Risk and action agent with escalation to a human.

**Day 4, 25 Oct: UI and testing**
- Streamlit UI: question box, live agent timeline, brief with expandable evidence, approve/reject buttons.
- Scenarios B to E. A test script runs all of them and scores whether the right cause was found (this table goes in the README).
- Failure testing: kill Zetaris, kill a model, give bad SQL. Check that each one recovers.

**Day 5, 26 Oct: package**
- README (setup, usage, dependencies, sample inputs and outputs, sponsor explanation).
- Clean-setup test from a fresh clone.
- Slide deck, then record the demo video (aim for about 3 minutes).

**Day 6, 27 Oct: buffer and submit**
- Fix whatever broke. Check for secrets. Declare pre-existing components (none planned).
- **Submit on HackOS by 27 Oct evening IST.** Don't wait until near 05:15 IST on 28 Oct.

---

## 6. Things to verify on day 1 (unknowns)

- Exact Zetaris connection details (MCP vs REST vs JDBC, auth, how to register files). Check the Zetaris folder in HackOS Resources and the Data Day recording.
- Nightshift endpoint URL, port and model names. Check meterless.ai docs, "How the hotspot works".
- H-MEM and World Model reference builds are TypeScript/Node. Plan: run them as a small local Node service that the Python agents call over HTTP.
- Whether xAI / Grok credits come with the hackathon. Check Space XAI resources on HackOS.

---

## 7. Tonight's checklist (only you can do these)

- [ ] **Register** on hackathon.genai.works / HackOS if not done (closes 22 Oct 23:45 UTC = 23 Oct 05:15 IST). Pick Track 2. Select "happy to build solo".
- [ ] **Install Cursor** (cursor.com) and open this folder in it.
- [ ] **Sign up for Zetaris Cloud**: cloud.enterprise.zetaris.com/register
- [ ] **Install Meterless Nightshift** for Windows (meterless.ai). Check that it detects your NVIDIA GPU.
- [ ] **NVIDIA NIM key** from build.nvidia.com
- [ ] **xAI / Grok key**, plus any hackathon credits listed on HackOS
- [ ] Make sure **Docker Desktop** runs (it will host Postgres).
- [ ] Watch the **Data Day (Zetaris)** and **onboarding** recordings on HackOS. Onboarding was today at 17:30 IST.
- [ ] Keep all keys in a notes file for now, never in the repo. They go into `.env` tomorrow.
