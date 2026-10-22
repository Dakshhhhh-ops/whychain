# Meterless bridge

A small local Node service that gives WhyChain's Python agents HTTP access to the **real Meterless engines**:

- **H-MEM** (hierarchical memory) handles `/memory/*`.
- **World Model** (event-sourced world state) handles `/world/*`.

The engines are the official TypeScript reference builds from [Meterless/Meterless](https://github.com/Meterless/Meterless) (Apache-2.0). They are vendored unmodified under `engines/`, and `server.ts` imports them as libraries. The bridge does not reimplement memory or event sourcing. Each route maps the WhyChain JSON contract onto the engine's own API, and data persists through the engines' own storage under `data/`. Memory therefore survives restarts and compounds across investigation runs.

```
Python agents (LangGraph) ──HTTP/JSON──► server.ts ──imports──► engines/hmem/reference/src         (H-MEM)
                                                    └─imports──► engines/world-model/reference/src  (World Model)
                                                         persist ► data/hmem/{state.json,ledger.jsonl,backups/}
                                                                   data/world/.world/whychain.json
```

## Start

```bash
cd meterless_bridge
npm install        # once: installs tsx (the engines have zero runtime dependencies)
npm start          # = tsx server.ts  ->  http://localhost:8787
```

Run the smoke test with `npm run smoke`. It spawns its own bridge on a temporary data dir, hits every route, restarts the bridge mid-test to prove persistence, then deletes the temp dir. To test a bridge that is already running, use `node smoke.mjs --url http://localhost:8787`. That mode writes `smoke-*` runs into the live data and skips the global sleep, dream-approve and restore steps.

| Env var | Default | Meaning |
|---|---|---|
| `METERLESS_BRIDGE_PORT` | `8787` | Port. |
| `METERLESS_BRIDGE_HOST` | `127.0.0.1` and `::1` | Bind address. By default it binds both loopbacks so `localhost` resolves quickly on Windows. It is never exposed on the LAN unless you set this. |
| `METERLESS_BRIDGE_DATA_DIR` | `./data` | Where both engines persist. It is git-ignored at the repo root. |
| `METERLESS_BRIDGE_MIN_SCORE` | `0.15` | Default recall threshold (see the recall notes below). |
| `METERLESS_BRIDGE_QUIET` | unset | Set to `1` to silence the per-request log line. |

To reset all memory and world state, stop the bridge and delete `data/`.

## Which engine powers each route

| Route | Engine | Engine API used |
|---|---|---|
| `GET /health` | both | Constructs both engines and reports counts. |
| `POST /memory/write` | H-MEM | `HMEM.add()`: source is mandatory (rejected without it); enrichment adds domain, entities and `relatedTo`; ledger action `create`. |
| `POST /memory/recall` | H-MEM | `HMEM.query()`, the 8-signal hybrid ranking × confidence. Filtered recalls use `retrieval.scoreOne()` + `store.touch()` + ledger `read` + `retrieval.formatReinjection()`. |
| `POST /memory/consolidate` | H-MEM | `store.promote()`, `conflicts.scan()` + `autoResolve()` (0.70 gate), and `sleep.preview()` + `sleep.execute()` (consolidate, archive and synthesize, with a backup first). |
| `GET /memory/audit` | H-MEM | The trust ledger (`ledger.all()`, `ledger.stats()`): an append-only `ledger.jsonl` with the 17 canonical actions. |
| `GET /memory/item` | H-MEM | `store.get()` + `ledger.history()` + `derivedFrom` lineage, which answers "why does memory believe this?" |
| `POST /memory/feedback` | H-MEM | `HMEM.feedback()` with exact deltas: helpful +0.05, not_helpful −0.03, wrong −0.20 plus a `review` tag. |
| `POST/GET /memory/dream`, `/memory/dream/approve`, `/memory/dream/reject` | H-MEM | `dreaming.dream()`, `pending()`, `approve()`, `reject()`. This is the human-approval boundary: proposals become memory only when approved. |
| `POST /memory/restore` | H-MEM | `store.restoreAll()` from a pre-sleep backup + ledger action `restore`. |
| `POST /world/events` | World Model | `upsertEntity`, `upsertContext`, `relate` (bitemporal edges) and `assertFact`, all through the engine's single-writer queue. |
| `GET /world/events` | World Model | `events()`, the engine's append-only log, projected to agent steps (`raw=1` returns the raw `WorldEvent`s). |
| `GET /world/state` | World Model | `stateShape()` (fold of the log), `view("graph")`, `view("facts")`, `mergeQueue()`. |
| `GET /world/audit` | World Model | `audit()`: the engine writes one audit entry per mutation. |

`GET /` lists every route with its parameters.

## Routes

All bodies are JSON. Errors return `{error, status}` with status 400, 404, 413 or 503.

### Memory (H-MEM)

**`POST /memory/write`**
```json
{"run_id": "r1", "agent": "investigator", "kind": "finding",
 "content": "Supplier S-14 shipments to Pune were delayed 9 days because of the Chennai port strike",
 "provenance": {"source": "zetaris", "query": "SELECT ...", "doc": null},
 "confidence": 0.85, "tags": ["supplier:S-14", "pune"]}
```
Response: `{"id": "mem-1", "tier": "working", "domain": "general", "entities": [...], "related_to": [...], "supersedes": null}`

Optional fields:
- `provenance.origin`: one of H-MEM's origins. Use `user_correction` for a human approve/reject; the conflict resolver trusts it more.
- `tier`: overrides the starting tier.
- `supersedes`: the id of a memory this one corrects. The old one is kept but penalised in ranking.

Each `kind` maps to an H-MEM `type` and a starting tier:

| kind | H-MEM type | starting tier |
|---|---|---|
| finding | factual | working |
| fact | factual | working |
| lesson | factual | working (always promoted by consolidate) |
| hypothesis | general | short_term |

Provenance is stored natively on the record:
- `source` holds `provenance.source`.
- `provenance.origin`, `provenance.label` and `provenance.refs` hold `run:`, `agent:`, `query:` and `doc:` refs.
- `goalRunId` holds `run_id`.
- `kind` is stored as a `kind:` tag.

**`POST /memory/recall`**
```json
{"query": "Why are Pune orders at risk?", "k": 5,
 "tags": ["pune"], "kinds": ["finding","lesson"], "agents": [], "tiers": [],
 "run_id": null, "exclude_run_id": "r2", "min_score": 0.15}
```
Response:
```json
{"items": [{"id": "mem-1", "content": "...", "kind": "finding",
            "provenance": {"source": "zetaris", "query": "SELECT ...", "doc": null, "origin": "model_response", "label": "..."},
            "confidence": 0.85, "tier": "long_term", "score": 0.3586,
            "run_id": "r1", "agent": "investigator", "tags": [...], "derived_from": [], "superseded_by": null, ...}],
 "context": "[MEMORY CONTEXT]\n## general\n- [LT] Supplier S-14 ... <mem-1>\n[/MEMORY CONTEXT]",
 "trace": {"retrievalReason": "...", "strategy": "comprehensive", "scores": {...}}}
```
- `context` is H-MEM's reinjection block. Paste it straight into an agent prompt.
- `tags` filters with AND semantics.
- `exclude_run_id` is how a new run asks "what do we remember from earlier runs?"
- Each recalled memory is touched (`accessCount`++) and logged as a `read` in the ledger. Recall frequency feeds promotion.

**`POST /memory/consolidate`**
```json
{"run_id": "r1", "min_confidence": 0.7, "min_access": 2, "sleep": true, "conflicts": true, "dry_run": false}
```
Response: `{"promoted": 4, "details": {"promoted": [{id, from, to, kind, reason}], "sleep": {backup_id, preview, report}, "conflicts": [...], "tiers": {...}}}`

Consolidation runs three steps:

1. **Run-scoped promotion** (bridge policy, applied through the engine's `store.promote()`, which writes a ledger `promote`). The run's non-long-term memories are promoted to `long_term` when:
   - the kind is `lesson` or `fact`, or
   - `confidence ≥ min_confidence`, or
   - the memory was recalled at least `min_access` times.

   Hypotheses are promoted only when they are both confident and repeatedly recalled. Anything marked `wrong` (tag `review`) or superseded is never promoted.
2. **Contradiction pass** using the engine's conflict detector, with auto-resolution gated at decision confidence ≥ 0.70. The loser gets `supersededBy`; nothing is deleted.
3. **The engine's sleep cycle**, preview-first. It consolidates short-term memories idle for 7 days or more, archives old low-value memories (with guardrails), and synthesizes near-duplicates (cosine ≥ 0.82) into one `long_term` record with `derivedFrom` lineage. This step is global, not run-scoped. Before it runs, the bridge writes a backup to `data/hmem/backups/sleep-<ms>.json`; `POST /memory/restore {"backup_id"}` rolls back to it.

`dry_run: true` returns the promotion plan and the sleep preview without changing anything.

**`GET /memory/audit?limit=50`** (filters: `memory_id`, `action`, `run_id`; add `verbose=1` for full before and after states)

Response: `{"order": "newest_first", "total": 576, "stats": {"create": 300, "read": 250, ...}, "entries": [{"memory_id": "mem-3", "action": "promote", "actor": "whychain-bridge", "at": "...", "memory": {...}, "diff": {"layer": ["working", "long_term"]}, "details": {...}}]}`

**Extra memory routes** (not in the original contract, but they expose more of H-MEM):
- `GET /memory/item?id=mem-1`: the record, its full ledger history, and its lineage. A memory deleted by sleep synthesis still returns its history.
- `POST /memory/feedback {id, verdict}`: for the Evaluator/Skeptic, e.g. `verdict: "wrong"` when a claim is rejected.
- `POST /memory/dream`, `GET /memory/dream`, `POST /memory/dream/approve {proposal_id}`, `POST /memory/dream/reject {proposal_id}`: insight proposals that need human approval.
- `POST /memory/restore {backup_id}`.

### World Model

**`POST /world/events`**
```json
{"run_id": "r1", "type": "sql_query", "actor": "investigator", "event_id": "q-1",
 "payload": {"sql": "SELECT ...", "rows": 42,
   "status": "investigating",
   "entities":  [{"type": "supplier", "name": "Supplier S-14", "attrs": {"region": "Chennai"}}],
   "relations": [{"from": {"type": "event", "name": "Chennai port strike"}, "type": "causes",
                  "to": {"type": "supplier", "name": "Supplier S-14"}, "confidence": 0.8}],
   "facts":     [{"about": {"type": "supplier", "name": "Supplier S-14"}, "predicate": "avg_delay_days", "value": 9}]}}
```
Response: `{"seq": 15, "step": 3, "run_id": "r1", "entity_id": "agent-step:whychain:r1#0003", "entities": [...], "world_events": [{seq, kind}, ...]}`

- `seq` is the World Model log sequence number of this step. It is global and strictly increasing.
- `event_id` is optional and makes retries idempotent: a repeat returns the original `seq` with `deduplicated: true`.
- `at` is an optional ISO timestamp, defaulting to now. The engine derives every record timestamp from it.

The payload is stored verbatim. Four payload keys are reserved and projected into the world graph:

| key | becomes |
|---|---|
| `status` | `assertFact(run, "status", value)` |
| `facts[]` | `assertFact`. `about` defaults to the run. The engine's conflict policy is most-recent-wins, and history is kept. |
| `entities[]` | `upsertEntity` plus a `last_seen_in` edge to this step |
| `relations[]` | `relate` in the run's context. These are bitemporal edges: a newer edge with the same `from` + `type` in the same run **closes** the older one (it gets `valid_to`) instead of deleting it. |

Each event writes the following engine structures:
- an `agent-run` entity and context (first event of a run only);
- an `agent` entity per actor;
- an `agent-step` entity;
- the edges `part_of`, `performed_by` and `current_step`. `current_step` is functional, so each step closes the previous one, and the engine's bitemporal history is the run timeline.

Entity identity in `entities`, `relations` and `facts.about`:
- **`key` given, or a name containing a digit** (codes such as `Supplier S-14`, `SKU-1182`, `PO-778`): exact identity, `type:whychain:<normalized key>`.
- **Free-text names**: the engine's name-keyed identity with fuzzy resolution. Similarity ≥ 0.85 auto-merges as an alias (e.g. "Chennai port strike" and "Chennai port strikes"); [0.82, 0.85) goes to the merge review queue.
- **An id string** returned earlier also works as a reference.

Why codes are keyed exactly: the resolver would otherwise auto-merge "Supplier S-14" with "Supplier S-15" (similarity 0.92).

**`GET /world/events?run_id=r1`** (also `since_seq=` for polling, `limit=`, and `raw=1` for the engine's raw `WorldEvent`s)

Response: `{"run_id": "r1", "count": 5, "last_seq": 41, "events": [{"seq", "step", "run_id", "type", "actor", "at", "event_id", "payload", "entity_id"}]}`

**`GET /world/state?run_id=r1`**

Response:
```json
{"run_id": "r1", "status": "completed",
 "facts":  {"question": {"value": "...", "history": [...]}, "root_cause": {...}, "status": {...}},
 "steps":  {"count": 5, "by_type": {...}, "by_actor": {...}, "current": {...}, "timeline": [{"step", "valid_from", "valid_to"}]},
 "agents": [...],
 "entities":  [{"id", "type", "name", "aliases", "attrs", "facts", "last_seen_step"}],
 "relations": [{"from_name": "Chennai port strike", "type": "causes", "to_name": "Supplier S-14", "valid_from", "valid_to", "open", ...}],
 "merge_queue": [...]}
```
Without `run_id`, the route returns `{runs: [{run_id, status, steps, started_at, last_at}], totals, merge_queue}`. Entity facts are world knowledge, not per-run state: they show the current value across all runs, plus history.

**`GET /world/audit?run_id=r1&limit=50`** returns the engine's audit log, one entry per mutation.

## Python usage

```python
import requests
B = "http://localhost:8787"
requests.post(f"{B}/world/events", json={"run_id": run_id, "type": "plan", "actor": "planner", "payload": {"status": "running"}}).json()["seq"]
mem = requests.post(f"{B}/memory/recall", json={"query": question, "k": 5, "exclude_run_id": run_id}).json()
prompt = mem["context"] + "\n\n" + question          # H-MEM reinjection block
requests.post(f"{B}/memory/write", json={"run_id": run_id, "agent": "investigator", "kind": "finding",
              "content": claim, "provenance": {"source": "zetaris", "query": sql}, "confidence": 0.8, "tags": ["pune"]})
requests.post(f"{B}/memory/consolidate", json={"run_id": run_id})   # end of run
```

## Conformance and test results

Engines are from `Meterless/Meterless` @ `1742dec56a66e58eef9dda2d2b865823758ca8cd` (2026-10-19), Node v24.14.0, Windows 11. Raw output is in `conformance-results/`.

| Suite | Result |
|---|---|
| H-MEM conformance (`engines/hmem/conformance/runner.ts`) | **13/13 PASS**: record validation, feedback deltas, ranking order, sleep guardrails, conflict gate, ledger audit, dream boundary |
| H-MEM reference `npm test` (vitest) | **10/10 PASS** |
| World Model conformance (`engines/world-model/conformance/runner.ts`) | **8/8 PASS**: stable ids, merge bands, provenance rejection, ingest idempotency (and that the check can fail), bitemporal edges, audit-per-mutation |
| World Model reference `npm test` (vitest) | **10/10 PASS** |
| World Model `scripts/idempotency-check.ts` | Empty diff on re-ingest. The `--mutate` run shows a non-empty diff, which is the expected result. |
| Bridge smoke test (`npm run smoke`) | **61/61 PASS**, including restart persistence and cross-run compounding |

`npm run conformance` re-runs both conformance suites from the bridge folder. The suites load the same `reference/src/index.ts` files that `server.ts` imports. To run the engines' own vitest suites: `cd engines/<engine>/reference && npm install && npm test`.

Latency, measured on this machine with data under OneDrive, after 1.8k world events and 300 memories:

| Route | Time |
|---|---|
| `POST /world/events` | ~8 ms |
| `POST /memory/write` | ~4 ms |
| `POST /memory/recall` | ~8–11 ms |
| `GET /world/state` | ~5 ms |

## Caveats

- **Recall is lexical, not semantic.** The H-MEM reference embedder is a deterministic token-hash mock, and the reference README says to swap it for a real provider in your own build. It is hard-wired, so the bridge uses it as is: no embedding model or API key is needed, but synonyms don't match. Relevant hits typically score 0.2–0.4, so the spec's 0.35 threshold drops many of them. The bridge therefore defaults to `min_score` 0.15; pass `0.35` to get the spec default. Putting key entities in `tags` (e.g. `pune`, `supplier:S-14`) adds to the score, because the tag signal is part of the ranking.
- **Sleep synthesis merges near-duplicates across runs.** It produces `Synthesis: A | B` content with `derived_from`, and deletes the sources, so their ids stop resolving in `/memory/recall`. `/memory/item` still shows their history. Pass `sleep: false` to skip this, or use `/memory/restore`.
- **Some state is in memory only.** Dream proposals, and the engine's own sleep backups, live in memory and are lost on restart. The bridge's disk backups do survive.
- **The H-MEM ledger `actor` is always `whychain-bridge`.** The engine fixes the actor per instance. The calling agent is recorded on each memory (`agent`) and shown in the audit entries.
- **World Model fact winners are recomputed on read.** The reference `assertFact` writes the winning value onto the entity and sets `supersededBy` only in live memory, not on replay. The bridge therefore computes the current fact (most-recent-wins, the engine's default policy) from the fact log on every read, and `/world/state` stays identical across restarts (the smoke test checks this).
- **Persistence is coalesced.** Both reference stores rewrite their whole JSON file on every mutation. The bridge wraps each engine's `persist()` so a request causes one write before the response is sent, and it retries transient Windows locks (`EBUSY`/`EPERM` from OneDrive or antivirus). The World Model spec itself recommends this (§3.4: debounce writes, retry transient errors). Without it, a world event took ~51 ms at 1.8k events. The World Model file still grows with every event (~1 KB each); fine for a hackathon. If OneDrive sync gets in the way, set `METERLESS_BRIDGE_DATA_DIR` to a folder outside OneDrive.
- **Run a single bridge process.** Two processes on the same data dir would overwrite each other.
- **Port 8787 is also on Nightshift's Auto Detect list** (8088, 8090, 8787, 11434). The runtime defaults to 8088, so there is no clash. If Nightshift is ever set to 8787, change `METERLESS_BRIDGE_PORT`.
- **The other engines are not wired in.** Markovian and Scout Intent are also in the Meterless repo. Scout Intent is spec-only, with no runnable reference.

## Nightshift hotspot (for the Doc Analyst model client)

Not part of this bridge. These details come from the repo docs `docs/products/nightshift/hotspot.md` and `docs/agent-ready/add-meterless-provider.md`, and from meterless.ai/engines.html.

| Setting | Value |
|---|---|
| Base URL | `http://127.0.0.1:8088/v1` (OpenAI-compatible) |
| Auth | `Authorization: Bearer <token>`, read from `<stateDir>/proxy-token`. The `rt-local` placeholder is rejected. |
| Headers | `meterless-app: whychain` on every request; `meterless-scope: user` on chat |
| Models | Routing modes, not model files. `meterless/auto`, `fast`, `deep`, `cheap`, `local` (never leaves the machine), `fusion`; `meterless/<class>` (`text`, `vision`, `image`, `video`), optionally `.<disposition>`. List them with `GET /v1/models`. |
| Health | `GET /v1/models` decides: 401/403 means bad token, 200 with `meterless/*` ids means online. `GET /healthz` only proves the process is up. |
| Timeout | 180 s, because a cold model load can take minutes |
| LAN / hotspot mode | Connect link `http://<host>:<port>/connect#k=<key>`, giving base URL `origin/v1` |

## License

The `engines/` folder is © Meterless and licensed under Apache-2.0 (`engines/*/LICENSE`, `engines/LICENSE.meterless-root`). The source commit is in `engines/SOURCE.txt`. Bridge code: Apache-2.0.
