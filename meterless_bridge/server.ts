// WhyChain <-> Meterless bridge.
//
// A small local HTTP service that exposes the REAL Meterless engine reference
// implementations (vendored, unmodified, under ./engines) to the Python agents:
//
//   H-MEM        engines/hmem/reference/src         -> /memory/*
//   World Model  engines/world-model/reference/src  -> /world/*
//
// Nothing here reimplements memory or event sourcing. The bridge maps the
// WhyChain JSON contract onto the engines' own APIs (HMEM.add / query /
// feedback, store.promote, sleep.preview / execute, conflicts.scan /
// autoResolve, dreaming.*, ledger.*; WorldModel.upsertEntity / upsertContext /
// relate / assertFact / events / views / audit) and persists through the
// engines' own persistence (H-MEM persistDir, World Model "local" storage).
//
// Run: npm start   (= tsx server.ts). Port: METERLESS_BRIDGE_PORT (default 8787).

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { HMEM } from "./engines/hmem/reference/src/index.ts";
import type {
  FeedbackKind,
  LedgerEntry,
  MemoryLayer,
  MemoryRecord,
  ProvenanceOrigin,
  RetrievedMemory,
} from "./engines/hmem/reference/src/index.ts";
import { WorldModel, entityIdFromName, externalId, normalizeName } from "./engines/world-model/reference/src/index.ts";
import type {
  Entity,
  Fact,
  Provenance as WorldProvenance,
  Relationship,
  WorldEvent,
} from "./engines/world-model/reference/src/index.ts";

// ------------------------------------------------------------------ config

const VERSION = "0.1.0";
const HERE = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.METERLESS_BRIDGE_PORT ?? 8787);
const HOSTS = process.env.METERLESS_BRIDGE_HOST ? [process.env.METERLESS_BRIDGE_HOST] : ["127.0.0.1", "::1"];
const DATA_DIR = path.resolve(process.env.METERLESS_BRIDGE_DATA_DIR ?? path.join(HERE, "data"));
const HMEM_DIR = path.join(DATA_DIR, "hmem");
const BACKUP_DIR = path.join(HMEM_DIR, "backups");
const WORLD_DIR = path.join(DATA_DIR, "world");
const WORLD_NAMESPACE = "whychain";
const SYSTEM = "whychain";
const ACTOR = "whychain-bridge";
// The reference embedder is a deterministic token-hash mock, so relevant
// supply-chain matches typically score 0.2-0.4. The spec default threshold
// (0.35) drops most of them; the bridge defaults lower. Pass min_score to
// override per call (min_score: 0.35 reproduces the spec default exactly).
const DEFAULT_MIN_SCORE = Number(process.env.METERLESS_BRIDGE_MIN_SCORE ?? 0.15);
const MAX_BODY_BYTES = 5 * 1024 * 1024;

const ENGINE_SOURCE = (() => {
  try {
    return fs.readFileSync(path.join(HERE, "engines", "SOURCE.txt"), "utf-8").trim();
  } catch {
    return "unknown";
  }
})();

// ------------------------------------------------------------------ engines

let hmem: HMEM | null = null;
let hmemError: string | null = null;
try {
  hmem = new HMEM({ persistDir: HMEM_DIR, actor: ACTOR });
} catch (err) {
  hmemError = (err as Error).message;
}

let world: WorldModel | null = null;
let worldError: string | null = null;
try {
  world = new WorldModel({ storage: "local", namespace: WORLD_NAMESPACE, directory: WORLD_DIR });
} catch (err) {
  worldError = (err as Error).message;
}

function needHmem(): HMEM {
  if (!hmem) throw new HttpError(503, `H-MEM engine unavailable: ${hmemError}`);
  return hmem;
}
function needWorld(): WorldModel {
  if (!world) throw new HttpError(503, `World Model engine unavailable: ${worldError}`);
  return world;
}

// ------------------------------------------------------------------ http plumbing

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}
const bad = (msg: string) => new HttpError(400, msg);

type Json = Record<string, unknown>;

function reqStr(body: Json, key: string): string {
  const v = body[key];
  if (typeof v !== "string" || v.trim() === "") throw bad(`"${key}" is required and must be a non-empty string`);
  return v;
}
function optStr(body: Json, key: string): string | undefined {
  const v = body[key];
  if (v === undefined || v === null) return undefined;
  if (typeof v !== "string") throw bad(`"${key}" must be a string`);
  return v;
}
function optNum(body: Json, key: string, lo?: number, hi?: number): number | undefined {
  const v = body[key];
  if (v === undefined || v === null) return undefined;
  if (typeof v !== "number" || !Number.isFinite(v)) throw bad(`"${key}" must be a number`);
  if ((lo !== undefined && v < lo) || (hi !== undefined && v > hi)) throw bad(`"${key}" must be between ${lo} and ${hi}`);
  return v;
}
function optStrArr(body: Json, key: string): string[] {
  const v = body[key];
  if (v === undefined || v === null) return [];
  if (!Array.isArray(v) || v.some((x) => typeof x !== "string")) throw bad(`"${key}" must be an array of strings`);
  return v as string[];
}
function optBool(body: Json, key: string, dflt: boolean): boolean {
  const v = body[key];
  if (v === undefined || v === null) return dflt;
  if (typeof v !== "boolean") throw bad(`"${key}" must be a boolean`);
  return v;
}
function isObj(v: unknown): v is Json {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
function clampInt(raw: string | null | undefined | number, dflt: number, lo: number, hi: number): number {
  const n = raw === null || raw === undefined || raw === "" ? dflt : Number(raw);
  if (!Number.isFinite(n)) return dflt;
  return Math.min(hi, Math.max(lo, Math.trunc(n)));
}
const round = (x: number) => Number(x.toFixed(4));
const iso = (ms: number | undefined) => (typeof ms === "number" ? new Date(ms).toISOString() : null);

async function readBody(req: http.IncomingMessage): Promise<Json> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY_BYTES) throw new HttpError(413, "request body too large");
    chunks.push(chunk as Buffer);
  }
  const text = Buffer.concat(chunks).toString("utf-8").trim();
  if (!text) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw bad("body must be valid JSON");
  }
  if (!isObj(parsed)) throw bad("body must be a JSON object");
  return parsed;
}

function send(res: http.ServerResponse, status: number, data: unknown): void {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "content-type",
  });
  res.end(body);
}

// Persistence hardening (World Model spec section 3.4: "Debounce writes ... to
// avoid write storms" and "Retry with exponential backoff for transient
// errors"). Both reference stores rewrite their whole JSON file on every
// mutation (the World Model store ~8-10 times per bridge event). The bridge
// wraps each engine's own persist() so that, inside a request, calls are
// coalesced into ONE write when the request finishes (before the response is
// sent), and transient Windows file locks (OneDrive / antivirus: EBUSY, EPERM)
// are retried. What gets written, and how it is replayed, is unchanged.
interface PersistGate {
  name: string;
  begin(): void;
  end(): void;
}
const persistGates: PersistGate[] = [];

function retrying(fn: () => void): void {
  for (let attempt = 0; ; attempt++) {
    try {
      fn();
      return;
    } catch (err) {
      const code = (err as NodeJS.ErrnoException).code;
      if (attempt >= 5 || !(code === "EBUSY" || code === "EPERM" || code === "EACCES")) throw err;
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 25 * 2 ** attempt); // 25..400 ms
    }
  }
}

function gatePersist(name: string, target: { persist: () => void } | undefined): void {
  if (!target || typeof target.persist !== "function") {
    console.warn(`meterless-bridge: ${name} has no persist() hook; writes are not coalesced`);
    return;
  }
  const original = target.persist.bind(target);
  let depth = 0;
  let dirty = false;
  target.persist = () => {
    if (depth > 0) {
      dirty = true;
      return;
    }
    retrying(original);
  };
  persistGates.push({
    name,
    begin: () => {
      depth++;
    },
    end: () => {
      depth = Math.max(0, depth - 1);
      if (depth === 0 && dirty) {
        dirty = false;
        retrying(original);
      }
    },
  });
}
if (hmem) gatePersist("H-MEM store", hmem.store);
if (world) gatePersist("World Model event store", (world as unknown as { store?: { persist: () => void } }).store);

// All writes go through one FIFO so multi-step World Model writes from
// concurrent requests never interleave (the engine serializes single calls;
// this serializes whole requests). Each request is one persistence batch.
let writeChain: Promise<unknown> = Promise.resolve();
function serial<T>(fn: () => Promise<T> | T): Promise<T> {
  const next = writeChain.then(async () => {
    for (const g of persistGates) g.begin();
    let result: T;
    try {
      result = await fn();
    } finally {
      let firstErr: unknown;
      for (const g of persistGates) {
        try {
          g.end();
        } catch (err) {
          firstErr ??= err;
        }
      }
      if (firstErr) throw firstErr;
    }
    return result;
  });
  writeChain = next.catch(() => undefined);
  return next;
}

// ================================================================== H-MEM

const KINDS = ["finding", "hypothesis", "fact", "lesson"] as const;
type Kind = (typeof KINDS)[number];

// WhyChain kind -> H-MEM canonical type + initial tier. Hypotheses are
// speculative, so they start volatile (short_term); everything else lands in
// working memory and is promoted to long_term by /memory/consolidate.
const KIND_MAP: Record<Kind, { type: MemoryRecord["type"]; layer: MemoryLayer }> = {
  finding: { type: "factual", layer: "working" },
  fact: { type: "factual", layer: "working" },
  lesson: { type: "factual", layer: "working" },
  hypothesis: { type: "general", layer: "short_term" },
};
const TIERS: MemoryLayer[] = ["short_term", "working", "long_term"];
const ORIGINS: ProvenanceOrigin[] = [
  "chat_message", "user_correction", "file_save", "file_open", "file_edit",
  "model_response", "plan_completion", "document", "manual", "dream", "sleep_synthesis",
];
const FEEDBACK: FeedbackKind[] = ["helpful", "not_helpful", "wrong"];

type MemLike = Partial<MemoryRecord>;

function ref(rec: MemLike, prefix: string): string | null {
  const hit = rec.provenance?.refs?.find((r) => r.startsWith(`${prefix}:`));
  return hit ? hit.slice(prefix.length + 1) : null;
}
function kindOf(rec: MemLike): string {
  const t = rec.tags?.find((x) => x.startsWith("kind:"));
  if (t) return t.slice("kind:".length);
  if (rec.provenance?.origin === "sleep_synthesis") return "synthesis";
  if (rec.provenance?.origin === "dream") {
    const d = rec.tags?.find((x) => x.startsWith("dream:"));
    return d ? d.slice("dream:".length) : "insight";
  }
  return "memory";
}
const runOf = (rec: MemLike) => rec.goalRunId ?? ref(rec, "run");
const agentOf = (rec: MemLike) => ref(rec, "agent");

function toItem(rec: MemoryRecord, score?: number) {
  return {
    id: rec.id,
    content: rec.content,
    kind: kindOf(rec),
    provenance: {
      source: rec.source,
      query: ref(rec, "query"),
      doc: ref(rec, "doc"),
      origin: rec.provenance?.origin ?? null,
      label: rec.provenance?.label ?? null,
    },
    confidence: round(rec.confidence),
    tier: rec.layer,
    score: score === undefined ? null : round(score),
    run_id: runOf(rec),
    agent: agentOf(rec),
    tags: rec.tags.filter((t) => !t.startsWith("kind:")),
    domain: rec.domain,
    entities: rec.entities,
    related_to: rec.relatedTo,
    supersedes: rec.supersedes ?? null,
    superseded_by: rec.supersededBy ?? null,
    derived_from: rec.derivedFrom ?? [],
    access_count: rec.accessCount,
    created_at: iso(rec.timestamp),
    last_accessed: iso(rec.lastAccessed),
  };
}

// POST /memory/write
function memoryWrite(body: Json) {
  const h = needHmem();
  const run_id = reqStr(body, "run_id");
  const agent = reqStr(body, "agent");
  const kind = reqStr(body, "kind") as Kind;
  if (!KINDS.includes(kind)) throw bad(`"kind" must be one of ${KINDS.join(", ")}`);
  const content = reqStr(body, "content");
  if (!isObj(body.provenance)) throw bad(`"provenance" is required: {source, query?, doc?}`);
  const prov = body.provenance;
  const source = reqStr(prov, "source"); // H-MEM rejects records with no source by construction
  const query = optStr(prov, "query");
  const doc = optStr(prov, "doc");
  const originIn = optStr(prov, "origin");
  if (originIn && !ORIGINS.includes(originIn as ProvenanceOrigin)) throw bad(`"provenance.origin" must be one of ${ORIGINS.join(", ")}`);
  const origin = (originIn as ProvenanceOrigin | undefined) ?? (doc ? "document" : "model_response");
  const confidence = optNum(body, "confidence", 0, 1) ?? 0.7;
  const tags = optStrArr(body, "tags").filter((t) => !t.startsWith("kind:"));
  const tierIn = optStr(body, "tier");
  if (tierIn && !TIERS.includes(tierIn as MemoryLayer)) throw bad(`"tier" must be one of ${TIERS.join(", ")}`);
  const supersedes = optStr(body, "supersedes");
  if (supersedes && !h.store.get(supersedes)) throw bad(`"supersedes" refers to unknown memory ${supersedes}`);

  const refs = [`run:${run_id}`, `agent:${agent}`];
  if (query) refs.push(`query:${query}`);
  if (doc) refs.push(`doc:${doc}`);

  const rec = h.add({
    content,
    type: KIND_MAP[kind].type,
    layer: (tierIn as MemoryLayer | undefined) ?? KIND_MAP[kind].layer,
    tags: [`kind:${kind}`, ...tags],
    confidence,
    source,
    provenance: { origin, learnedAt: h.clock(), label: `${kind} by ${agent} in run ${run_id} via ${source}`, refs },
    goalRunId: run_id,
    supersedes,
  });
  return {
    id: rec.id,
    tier: rec.layer,
    domain: rec.domain,
    entities: rec.entities,
    related_to: rec.relatedTo,
    supersedes: rec.supersedes ?? null,
  };
}

// POST /memory/recall
function memoryRecall(body: Json) {
  const h = needHmem();
  const query = reqStr(body, "query");
  const k = clampInt(optNum(body, "k", 1, 100), 5, 1, 100);
  const minScore = optNum(body, "min_score", 0, 1) ?? DEFAULT_MIN_SCORE;
  const f = {
    tags: optStrArr(body, "tags"),
    kinds: optStrArr(body, "kinds"),
    agents: optStrArr(body, "agents"),
    tiers: optStrArr(body, "tiers"),
    run_id: optStr(body, "run_id"),
    exclude_run_id: optStr(body, "exclude_run_id"),
  };
  const filtered = f.tags.length || f.kinds.length || f.agents.length || f.tiers.length || f.run_id || f.exclude_run_id;

  let top: RetrievedMemory[];
  let context: string;
  let trace: Json;
  if (!filtered) {
    // Straight engine path: hybrid 8-signal ranking, touch + "read" ledger entries, reinjection block.
    const r = h.query(query, { topN: k, threshold: minScore });
    top = r.memories;
    context = r.context;
    trace = r.trace as unknown as Json;
  } else {
    // Filtered path: pre-filter candidates, then score each with the engine's
    // canonical formula (retrieval.scoreOne) and apply the same threshold /
    // top-N / touch / "read" ledger / reinjection steps as HMEM.query().
    const scored: RetrievedMemory[] = [];
    for (const m of h.store.all()) {
      if (f.tags.length && !f.tags.every((t) => m.tags.includes(t))) continue;
      if (f.kinds.length && !f.kinds.includes(kindOf(m))) continue;
      if (f.agents.length && !f.agents.includes(agentOf(m) ?? "")) continue;
      if (f.tiers.length && !f.tiers.includes(m.layer)) continue;
      if (f.run_id && runOf(m) !== f.run_id) continue;
      if (f.exclude_run_id && runOf(m) === f.exclude_run_id) continue;
      const s = h.retrieval.scoreOne(m, query);
      if (s >= minScore) scored.push({ memory: m, relevance: s });
    }
    scored.sort((a, b) => b.relevance - a.relevance || a.memory.id.localeCompare(b.memory.id));
    top = scored.slice(0, k);
    for (const { memory } of top) {
      h.store.touch(memory.id);
      h.ledger.log(memory.id, "read", ACTOR, { details: { query, filters: f } });
    }
    context = h.retrieval.formatReinjection(top);
    trace = {
      retrievalReason: `hybrid ranking for "${query}" over filtered candidates`,
      strategy: top.length <= 1 ? "minimal" : "comprehensive",
      scores: Object.fromEntries(top.map((t) => [t.memory.id, round(t.relevance)])),
      filters: f,
    };
  }
  h.store.persist(); // persist accessCount / lastAccessed bumps from touch()
  return {
    items: top.map((t) => toItem(t.memory, t.relevance)),
    context,
    trace: { ...trace, min_score: minScore, k },
  };
}

// POST /memory/consolidate
function memoryConsolidate(body: Json) {
  const h = needHmem();
  const run_id = optStr(body, "run_id");
  const minConfidence = optNum(body, "min_confidence", 0, 1) ?? 0.7;
  const minAccess = clampInt(optNum(body, "min_access", 0, 1000), 2, 0, 1000);
  const doSleep = optBool(body, "sleep", true);
  const doConflicts = optBool(body, "conflicts", true);
  const dryRun = optBool(body, "dry_run", false);

  // 1) Run-scoped promotion working -> long_term via the engine's store.promote()
  //    (ledger action "promote"). "Important" = lessons and facts; findings at
  //    or above min_confidence; anything recalled >= min_access times. Records
  //    flagged "review" (feedback: wrong) or superseded are never promoted.
  //    Hypotheses only when both confident and repeatedly recalled.
  const candidates = h.store.all().filter((m) => {
    if (run_id && runOf(m) !== run_id) return false;
    return m.layer !== "long_term" && !m.tags.includes("review") && !m.supersededBy;
  });
  const plan: { id: string; from: MemoryLayer; kind: string; reason: string }[] = [];
  for (const m of candidates) {
    const kind = kindOf(m);
    let reason = "";
    if (kind === "hypothesis") {
      if (m.confidence >= minConfidence && m.accessCount >= minAccess) reason = `hypothesis confirmed by use (confidence ${round(m.confidence)}, recalled ${m.accessCount}x)`;
    } else if (kind === "lesson" || kind === "fact") reason = `kind=${kind}`;
    else if (m.confidence >= minConfidence) reason = `confidence ${round(m.confidence)} >= ${minConfidence}`;
    else if (m.accessCount >= minAccess) reason = `recalled ${m.accessCount}x >= ${minAccess}`;
    if (reason) plan.push({ id: m.id, from: m.layer, kind, reason });
  }

  if (dryRun) {
    return {
      promoted: 0,
      details: { dry_run: true, run_id: run_id ?? null, would_promote: plan, sleep_preview: doSleep ? h.sleep.preview() : null },
    };
  }

  for (const p of plan) h.store.promote(p.id, "long_term");

  // 2) Contradiction pass (engine: conflicts.scan + autoResolve, 0.70 gate).
  const conflicts: Json[] = [];
  if (doConflicts) {
    for (const c of h.conflicts.scan()) {
      const outcome = h.conflicts.autoResolve(c);
      conflicts.push({ ...c, outcome });
    }
  }

  // 3) The engine's preview-first sleep cycle: consolidate stale short-term,
  //    archive old low-value records (guardrails apply), synthesize near-
  //    duplicates (cosine >= 0.82) into long_term records with derivedFrom.
  //    The engine keeps its backups in memory; the bridge also writes the
  //    pre-sleep snapshot to disk so /memory/restore survives restarts.
  let sleep: Json | null = null;
  if (doSleep) {
    const preview = h.sleep.preview();
    const snap = h.store.snapshotAll();
    const backup_id = `sleep-${Date.now()}`;
    fs.mkdirSync(BACKUP_DIR, { recursive: true });
    fs.writeFileSync(
      path.join(BACKUP_DIR, `${backup_id}.json`),
      JSON.stringify({ backup_id, created_at: new Date().toISOString(), run_id: run_id ?? null, ...snap }) + "\n",
      "utf-8"
    );
    const report = h.sleep.execute(preview);
    sleep = { backup_id, preview, report };
  }

  const sleepConsolidated = (sleep?.report as { consolidated?: number } | undefined)?.consolidated ?? 0;
  return {
    promoted: plan.length + sleepConsolidated,
    details: {
      run_id: run_id ?? null,
      promoted: plan.map((p) => ({ ...p, to: "long_term" })),
      sleep,
      conflicts,
      tiers: tierCounts(h),
    },
  };
}

function tierCounts(h: HMEM) {
  const out: Record<string, number> = { short_term: 0, working: 0, long_term: 0 };
  for (const m of h.store.all()) out[m.layer] = (out[m.layer] ?? 0) + 1;
  return out;
}

// Ledger entry -> compact JSON (diff of the fields that matter), full states when verbose.
const DIFF_KEYS = ["layer", "confidence", "accessCount", "tags", "domain", "supersededBy", "content"] as const;
function compactLedger(e: LedgerEntry, verbose: boolean) {
  const ps = e.previousState;
  const ns = e.newState;
  const out: Json = { memory_id: e.memoryId, action: e.action, actor: e.actor, at: iso(e.timestamp) };
  const subject = ns ?? ps;
  if (subject?.content !== undefined) {
    out.memory = { content: subject.content, kind: kindOf(subject), tier: subject.layer, source: subject.source, run_id: runOf(subject), agent: agentOf(subject) };
  }
  if (ps && ns) {
    const diff: Json = {};
    for (const k of DIFF_KEYS) {
      if (JSON.stringify(ps[k]) !== JSON.stringify(ns[k])) diff[k] = [ps[k] ?? null, ns[k] ?? null];
    }
    out.diff = diff;
  }
  if (e.details) out.details = e.details;
  if (verbose) {
    out.previous_state = ps ?? null;
    out.new_state = ns ?? null;
  }
  return out;
}

// GET /memory/audit
function memoryAudit(q: URLSearchParams) {
  const h = needHmem();
  const limit = clampInt(q.get("limit"), 50, 1, 5000);
  const memoryId = q.get("memory_id");
  const action = q.get("action");
  const runId = q.get("run_id");
  const verbose = ["1", "true", "yes"].includes((q.get("verbose") ?? "").toLowerCase());
  const all = h.ledger.all();
  const picked: LedgerEntry[] = [];
  for (let i = all.length - 1; i >= 0 && picked.length < limit; i--) {
    const e = all[i];
    if (memoryId && e.memoryId !== memoryId) continue;
    if (action && e.action !== action) continue;
    if (runId) {
      const subj = e.newState ?? e.previousState ?? (h.store.get(e.memoryId) as MemLike | undefined);
      if (!subj || runOf(subj) !== runId) continue;
    }
    picked.push(e);
  }
  return {
    order: "newest_first",
    total: all.length,
    stats: h.ledger.stats(),
    entries: picked.map((e) => compactLedger(e, verbose)),
  };
}

// GET /memory/list?limit=&kind=&tier=  (browse memories, newest first; read-only, no access-count touch)
function memoryList(q: URLSearchParams) {
  const h = needHmem();
  const limit = Math.min(Number(q.get("limit") ?? 100) || 100, 1000);
  const kind = q.get("kind");
  const tier = q.get("tier");
  const items = h.store
    .all()
    .filter((m) => !m.supersededBy)
    .map((m) => toItem(m))
    .filter((it) => (!kind || it.kind === kind) && (!tier || it.tier === tier))
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  return { count: items.length, items: items.slice(0, limit), tiers: tierCounts(h) };
}

// GET /memory/item?id=  (record + full ledger history + lineage)
function memoryItem(id: string) {
  const h = needHmem();
  const rec = h.store.get(id);
  const history = h.ledger.history(id).map((e) => compactLedger(e, false));
  if (!rec && history.length === 0) throw new HttpError(404, `unknown memory ${id}`);
  return {
    item: rec ? toItem(rec) : null,
    deleted: !rec,
    history,
    lineage: rec?.derivedFrom?.map((d) => {
      const src = h.store.get(d);
      return src ? toItem(src) : { id: d, deleted: true, history: h.ledger.history(d).map((e) => compactLedger(e, false)) };
    }) ?? [],
  };
}

// POST /memory/feedback {id, verdict}
function memoryFeedback(body: Json) {
  const h = needHmem();
  const id = reqStr(body, "id");
  const verdict = reqStr(body, "verdict") as FeedbackKind;
  if (!FEEDBACK.includes(verdict)) throw bad(`"verdict" must be one of ${FEEDBACK.join(", ")}`);
  if (!h.store.get(id)) throw new HttpError(404, `unknown memory ${id}`);
  h.feedback(id, verdict);
  return { item: toItem(h.store.get(id)!) };
}

// POST /memory/dream, GET /memory/dream, POST /memory/dream/approve|reject
function memoryDream() {
  const h = needHmem();
  return { proposals: h.dreaming.dream() };
}
function memoryDreamDecide(body: Json, approve: boolean) {
  const h = needHmem();
  const proposalId = reqStr(body, "proposal_id");
  if (!h.dreaming.pending().some((p) => p.id === proposalId)) throw new HttpError(404, `no pending proposal ${proposalId}`);
  if (!approve) {
    h.dreaming.reject(proposalId);
    return { proposal_id: proposalId, status: "rejected" };
  }
  const rec = h.dreaming.approve(proposalId);
  return { proposal_id: proposalId, status: "approved", memory: rec ? toItem(rec) : null };
}

// POST /memory/restore {backup_id}
function memoryRestore(body: Json) {
  const h = needHmem();
  const backupId = reqStr(body, "backup_id");
  if (!/^sleep-\d+$/.test(backupId)) throw bad(`"backup_id" looks like sleep-<ms>`);
  const file = path.join(BACKUP_DIR, `${backupId}.json`);
  if (!fs.existsSync(file)) throw new HttpError(404, `unknown backup ${backupId}`);
  const saved = JSON.parse(fs.readFileSync(file, "utf-8")) as { records: MemoryRecord[]; seq: number };
  h.store.restoreAll(saved.records, saved.seq);
  h.ledger.log("*", "restore", ACTOR, { details: { backupId, source: "bridge-disk-backup" } });
  return { restored: saved.records.length, backup_id: backupId, tiers: tierCounts(h) };
}

// ================================================================== World Model

const RUN_TYPE = "agent-run";
const RUN_CTX_TYPE = "agent-run-context";
const STEP_TYPE = "agent-step";
const AGENT_TYPE = "agent";
// Edges the bridge itself writes for every event. Domain edges supplied in
// payload.relations may not reuse these types.
const STRUCTURAL = new Set(["part_of", "performed_by", "current_step", "last_seen_in"]);

const runEntityId = (runId: string) => externalId(RUN_TYPE, SYSTEM, runId);
const runCtxId = (runId: string) => externalId(RUN_CTX_TYPE, SYSTEM, runId);
const agentEntityId = (actor: string) => externalId(AGENT_TYPE, SYSTEM, actor);

type StepAttrs = { run_id: string; step: number; type: string; actor: string; at: string; event_id: string | null; payload: Json };

// Indexes rebuilt from the engine's replayed event log on boot.
const stepCount = new Map<string, number>();
const eventIdIndex = new Map<string, { seq: number; step: number; entity_id: string }>();
function indexWorld(w: WorldModel): void {
  for (const ev of w.events()) {
    if (ev.kind !== "upsertEntity") continue;
    const p = ev.payload as Entity;
    if (p.type !== STEP_TYPE) continue;
    const a = p.attrs as StepAttrs;
    stepCount.set(a.run_id, Math.max(stepCount.get(a.run_id) ?? 0, a.step));
    if (a.event_id) eventIdIndex.set(`${a.run_id}::${a.event_id}`, { seq: ev.seq, step: a.step, entity_id: p.id });
  }
}
if (world) indexWorld(world);

// Entity references in payloads: an entity id string returned earlier, or a
// spec {type, name?, key?}. Identity rules:
//   - key given              -> exact identity  (externalId type:whychain:<normalized key>)
//   - name containing digits -> exact identity on the name (codes like "Supplier S-14",
//                               "SKU-1182": the engine's fuzzy resolver would auto-merge
//                               "S-14"/"S-15" at similarity >= 0.85)
//   - free-text name         -> engine name-keyed identity + fuzzy resolution
//                               (auto-merge >= 0.85, review queue [0.82, 0.85))
type EntitySpec = { type: string; name?: string; key?: string };
type EntityRef = string | EntitySpec;

function parseSpec(v: unknown, where: string): EntitySpec {
  if (!isObj(v) || typeof v.type !== "string" || !v.type) throw bad(`${where} must be {type, name?, key?}`);
  const name = typeof v.name === "string" && v.name.trim() ? v.name : undefined;
  const key = typeof v.key === "string" && v.key.trim() ? v.key : undefined;
  if (!name && !key) throw bad(`${where} needs a name or a key`);
  return { type: v.type, name, key };
}
function parseRef(v: unknown, where: string): EntityRef {
  if (typeof v === "string" && v.trim()) return v;
  if (isObj(v)) return parseSpec(v, where);
  throw bad(`${where} must be an entity id string or {type, name?, key?}`);
}
function exactKey(s: EntitySpec): string | undefined {
  if (s.key) return normalizeName(s.key);
  if (s.name && /\d/.test(s.name)) return normalizeName(s.name);
  return undefined;
}
function specId(s: EntitySpec): string {
  const k = exactKey(s);
  return k ? externalId(s.type, SYSTEM, k) : entityIdFromName(s.type, s.name!);
}

function entityName(w: WorldModel, id: string): string {
  const e = w.view("graph").entity(id);
  return (e?.name ?? (e?.attrs?.name as string | undefined) ?? id) as string;
}

// POST /world/events
async function worldPostEvent(body: Json) {
  const w = needWorld();
  const run_id = reqStr(body, "run_id");
  const type = reqStr(body, "type");
  const actor = reqStr(body, "actor");
  const payload = body.payload === undefined || body.payload === null ? {} : body.payload;
  if (!isObj(payload)) throw bad(`"payload" must be a JSON object`);
  const atIn = optStr(body, "at");
  if (atIn && Number.isNaN(Date.parse(atIn))) throw bad(`"at" must be an ISO timestamp`);
  const at = atIn ? new Date(atIn).toISOString() : new Date().toISOString();
  const event_id = optStr(body, "event_id");

  if (event_id) {
    const hit = eventIdIndex.get(`${run_id}::${event_id}`);
    if (hit) return { ...hit, run_id, deduplicated: true, world_events: [] };
  }

  // Validate the reserved payload keys BEFORE writing anything.
  const graph = w.view("graph");
  const checkRef = (r: EntityRef, where: string) => {
    if (typeof r === "string" && !graph.entity(r)) throw bad(`${where}: unknown entity id ${r}`);
  };
  const status = payload.status;
  if (status !== undefined && typeof status !== "string") throw bad(`"payload.status" must be a string`);
  const entities = (payload.entities ?? []) as unknown[];
  const relations = (payload.relations ?? []) as unknown[];
  const facts = (payload.facts ?? []) as unknown[];
  if (!Array.isArray(entities) || !Array.isArray(relations) || !Array.isArray(facts)) {
    throw bad(`"payload.entities", "payload.relations" and "payload.facts" must be arrays when present`);
  }
  const ents = entities.map((e, i) => {
    const spec = parseSpec(e, `payload.entities[${i}]`);
    const attrs = (e as Json).attrs;
    if (attrs !== undefined && !isObj(attrs)) throw bad(`payload.entities[${i}].attrs must be an object`);
    return { spec, attrs: attrs as Json | undefined };
  });
  const rels = relations.map((r, i) => {
    if (!isObj(r) || typeof r.type !== "string" || !r.type) throw bad(`payload.relations[${i}] must be {from, type, to, attrs?, confidence?}`);
    if (STRUCTURAL.has(r.type)) throw bad(`payload.relations[${i}].type "${r.type}" is reserved by the bridge`);
    const from = parseRef(r.from, `payload.relations[${i}].from`);
    const to = parseRef(r.to, `payload.relations[${i}].to`);
    checkRef(from, `payload.relations[${i}].from`);
    checkRef(to, `payload.relations[${i}].to`);
    return { from, to, type: r.type, attrs: isObj(r.attrs) ? r.attrs : {}, confidence: typeof r.confidence === "number" ? r.confidence : undefined };
  });
  const fcts = facts.map((f, i) => {
    if (!isObj(f) || typeof f.predicate !== "string" || !f.predicate || !("value" in f)) throw bad(`payload.facts[${i}] must be {about?, predicate, value, confidence?}`);
    const about = f.about === undefined ? undefined : parseRef(f.about, `payload.facts[${i}].about`);
    if (about) checkRef(about, `payload.facts[${i}].about`);
    return { about, predicate: f.predicate, value: f.value, confidence: typeof f.confidence === "number" ? f.confidence : undefined };
  });

  const source: WorldProvenance = { kind: "agent", by: actor, at, runId: run_id };
  const before = w.events().length;
  const runId = runEntityId(run_id);
  const ctx = runCtxId(run_id);

  if (!graph.entity(runId)) {
    await w.upsertEntity({ type: RUN_TYPE, externalKey: { system: SYSTEM, id: run_id }, attrs: { run_id, started_at: at, started_by: actor }, source });
    await w.upsertContext({ type: RUN_CTX_TYPE, externalKey: { system: SYSTEM, id: run_id }, attrs: { run_id }, source });
  }
  if (!graph.entity(agentEntityId(actor))) {
    await w.upsertEntity({ type: AGENT_TYPE, externalKey: { system: SYSTEM, id: actor }, name: actor, attrs: { name: actor }, source });
  }

  const step = (stepCount.get(run_id) ?? 0) + 1;
  const attrs: StepAttrs = { run_id, step, type, actor, at, event_id: event_id ?? null, payload };
  const stepEntity = await w.upsertEntity({
    type: STEP_TYPE,
    externalKey: { system: SYSTEM, id: `${run_id}#${String(step).padStart(4, "0")}` },
    attrs,
    source,
  });
  stepCount.set(run_id, step);
  let seq = 0;
  const log = w.events();
  for (let i = log.length - 1; i >= 0; i--) {
    if (log[i].kind === "upsertEntity" && (log[i].payload as Entity).id === stepEntity.id) {
      seq = log[i].seq;
      break;
    }
  }
  if (event_id) eventIdIndex.set(`${run_id}::${event_id}`, { seq, step, entity_id: stepEntity.id });

  // Structural edges. current_step is functional per (run, context): each new
  // step CLOSES the previous edge (bitemporal validTo), so the engine itself
  // keeps the step-by-step timeline of "what the run was doing when".
  await w.relate({ from: stepEntity.id, type: "part_of", to: runId, context: ctx, source });
  await w.relate({ from: stepEntity.id, type: "performed_by", to: agentEntityId(actor), context: ctx, source });
  await w.relate({ from: runId, type: "current_step", to: stepEntity.id, context: ctx, source });

  // Reserved payload projections into the world graph.
  const upsertSpec = async (s: EntitySpec, attrs?: Json): Promise<string> => {
    const k = exactKey(s);
    const ent = k
      ? await w.upsertEntity({ type: s.type, externalKey: { system: SYSTEM, id: k }, name: s.name ?? s.key, attrs, source })
      : await w.upsertEntity({ type: s.type, name: s.name!, attrs, source }); // engine resolver may auto-merge / queue near-duplicates
    return ent.id;
  };
  const resolve = async (r: EntityRef): Promise<string> => {
    if (typeof r === "string") return w.view("graph").entity(r)!.id;
    const existing = w.view("graph").entity(specId(r));
    return existing ? existing.id : upsertSpec(r);
  };
  const touched: string[] = [];
  for (const e of ents) {
    const id = await upsertSpec(e.spec, e.attrs);
    const ent = { id };
    touched.push(ent.id);
    await w.relate({ from: ent.id, type: "last_seen_in", to: stepEntity.id, context: ctx, source });
  }
  for (const r of rels) {
    const from = await resolve(r.from);
    const to = await resolve(r.to);
    await w.relate({ from, type: r.type, to, context: ctx, attrs: { ...r.attrs, confidence: r.confidence ?? null, step }, source });
  }
  if (typeof status === "string") fcts.unshift({ about: runId, predicate: "status", value: status, confidence: 1 });
  for (const f of fcts) {
    const about = f.about ? await resolve(f.about) : runId;
    await w.assertFact({ about, predicate: f.predicate, value: f.value, confidence: f.confidence, context: ctx, source });
  }

  return {
    seq,
    step,
    run_id,
    entity_id: stepEntity.id,
    entities: touched,
    world_events: w.events().slice(before).map((e) => ({ seq: e.seq, kind: e.kind })),
  };
}

function stepView(ev: WorldEvent) {
  const p = ev.payload as Entity;
  const a = p.attrs as StepAttrs;
  return { seq: ev.seq, step: a.step, run_id: a.run_id, type: a.type, actor: a.actor, at: a.at, event_id: a.event_id, payload: a.payload, entity_id: p.id };
}

// GET /world/events?run_id=&since_seq=&limit=&raw=
function worldGetEvents(q: URLSearchParams) {
  const w = needWorld();
  const runId = q.get("run_id");
  const since = clampInt(q.get("since_seq"), 0, 0, Number.MAX_SAFE_INTEGER);
  const limit = clampInt(q.get("limit"), 1000, 1, 100000);
  const raw = ["1", "true", "yes"].includes((q.get("raw") ?? "").toLowerCase());
  const log = w.events();
  const events: ReturnType<typeof stepView>[] = [];
  for (const ev of log) {
    if (events.length >= limit) break;
    if (ev.seq <= since || ev.kind !== "upsertEntity") continue;
    const p = ev.payload as Entity;
    if (p.type !== STEP_TYPE) continue;
    if (runId && (p.attrs as StepAttrs).run_id !== runId) continue;
    events.push(stepView(ev));
  }
  const out: Json = { run_id: runId, count: events.length, last_seq: log.length ? log[log.length - 1].seq : 0, events };
  if (raw) {
    out.world_events = log
      .filter((ev) => ev.seq > since && (!runId || (ev.payload as { source?: { runId?: string } }).source?.runId === runId))
      .slice(0, limit);
  }
  return out;
}

// Current fact values. The engine's conflict policy here is the default
// "most-recent-wins"; the reference sets fact.supersededBy / entity.attrs only
// in live memory (not on replay), so the bridge recomputes the winner from the
// engine's fact log on every read to stay correct across restarts.
function currentFacts(w: WorldModel, aboutId: string) {
  const byPred = new Map<string, Fact[]>();
  for (const f of w.view("facts").about(aboutId)) {
    const list = byPred.get(f.predicate) ?? [];
    list.push(f);
    byPred.set(f.predicate, list);
  }
  const out: Record<string, Json> = {};
  for (const [pred, list] of byPred) {
    const win = list[list.length - 1]; // facts.about() is sorted by assertedAt; stable sort keeps append order on ties
    out[pred] = {
      value: win.value as unknown,
      confidence: win.confidence,
      asserted_at: win.assertedAt,
      by: win.source.by ?? null,
      run_id: win.source.runId ?? null,
      history: list.map((f) => ({ value: f.value as unknown, at: f.assertedAt, by: f.source.by ?? null, run_id: f.source.runId ?? null })),
    };
  }
  return out;
}

type Shape = { entities: Entity[]; contexts: unknown[]; relationships: Relationship[]; facts: Fact[] };

// GET /world/state?run_id=
function worldGetState(q: URLSearchParams) {
  const w = needWorld();
  const shape = w.stateShape() as Shape;
  const runIdParam = q.get("run_id");
  const pendingMerges = w.mergeQueue().filter((m) => m.status === "pending");

  if (!runIdParam) {
    const runs = shape.entities
      .filter((e) => e.type === RUN_TYPE)
      .map((e) => {
        const rid = String(e.attrs.run_id);
        const facts = currentFacts(w, e.id);
        return { run_id: rid, status: (facts.status?.value as string | undefined) ?? null, steps: stepCount.get(rid) ?? 0, started_at: e.attrs.started_at ?? e.createdAt, last_at: e.updatedAt };
      });
    return {
      runs,
      totals: { events: w.events().length, entities: shape.entities.length, relationships: shape.relationships.length, facts: shape.facts.length },
      merge_queue: pendingMerges,
    };
  }

  const runId = runEntityId(runIdParam);
  const run = w.view("graph").entity(runId);
  if (!run) throw new HttpError(404, `unknown run ${runIdParam}`);
  const ctx = runCtxId(runIdParam);
  const byId = new Map(shape.entities.map((e) => [e.id, e]));

  const steps = shape.entities
    .filter((e) => e.type === STEP_TYPE && (e.attrs as StepAttrs).run_id === runIdParam)
    .map((e) => e.attrs as StepAttrs)
    .sort((a, b) => a.step - b.step);
  const by_type: Record<string, number> = {};
  const by_actor: Record<string, number> = {};
  for (const s of steps) {
    by_type[s.type] = (by_type[s.type] ?? 0) + 1;
    by_actor[s.actor] = (by_actor[s.actor] ?? 0) + 1;
  }

  const rels = shape.relationships.filter((r) => r.context === ctx);
  const currentEdge = rels.find((r) => r.from === runId && r.type === "current_step" && !r.validTo);
  const current = currentEdge ? (byId.get(currentEdge.to)?.attrs as StepAttrs | undefined) : undefined;
  const timeline = rels
    .filter((r) => r.from === runId && r.type === "current_step")
    .map((r) => ({ step: (byId.get(r.to)?.attrs as StepAttrs | undefined)?.step ?? null, valid_from: r.validFrom ?? null, valid_to: r.validTo ?? null }))
    .sort((a, b) => (a.step ?? 0) - (b.step ?? 0));

  const touched = new Set<string>();
  for (const r of rels) {
    if (r.type === "last_seen_in") touched.add(r.from);
    else if (!STRUCTURAL.has(r.type)) {
      touched.add(r.from);
      touched.add(r.to);
    }
  }
  for (const f of shape.facts) if (f.context === ctx && f.about !== runId) touched.add(f.about);

  const entities = [...touched].map((id) => {
    const e = byId.get(id);
    return {
      id,
      type: e?.type ?? null,
      name: e?.name ?? null,
      aliases: e?.aliases ?? [],
      attrs: e?.attrs ?? {},
      facts: currentFacts(w, id),
      last_seen_step: (() => {
        const edge = rels.find((r) => r.from === id && r.type === "last_seen_in" && !r.validTo);
        return edge ? ((byId.get(edge.to)?.attrs as StepAttrs | undefined)?.step ?? null) : null;
      })(),
    };
  });

  const relations = rels
    .filter((r) => !STRUCTURAL.has(r.type))
    .map((r) => ({
      id: r.id,
      from: r.from,
      from_name: entityName(w, r.from),
      type: r.type,
      to: r.to,
      to_name: entityName(w, r.to),
      attrs: r.attrs ?? {},
      valid_from: r.validFrom ?? null,
      valid_to: r.validTo ?? null,
      superseded_by: r.supersededBy ?? null,
      open: !r.validTo,
    }));

  const runFacts = currentFacts(w, runId);
  return {
    run_id: runIdParam,
    run: { id: run.id, attrs: run.attrs, created_at: run.createdAt, updated_at: run.updatedAt },
    status: (runFacts.status?.value as string | undefined) ?? null,
    facts: runFacts,
    steps: {
      count: steps.length,
      by_type,
      by_actor,
      first_at: steps[0]?.at ?? null,
      last_at: steps[steps.length - 1]?.at ?? null,
      current: current ? { step: current.step, type: current.type, actor: current.actor, at: current.at } : null,
      timeline,
    },
    agents: Object.keys(by_actor).map((a) => ({ id: agentEntityId(a), name: a, steps: by_actor[a] })),
    entities,
    relations,
    merge_queue: pendingMerges.filter((m) => touched.has(m.leftId) || touched.has(m.rightId)),
  };
}

// GET /world/audit?limit=&run_id=
function worldAudit(q: URLSearchParams) {
  const w = needWorld();
  const limit = clampInt(q.get("limit"), 50, 1, 5000);
  const runId = q.get("run_id");
  const all = w.audit();
  const picked = [];
  for (let i = all.length - 1; i >= 0 && picked.length < limit; i--) {
    if (runId && all[i].runId !== runId) continue;
    picked.push(all[i]);
  }
  return { order: "newest_first", total: all.length, entries: picked };
}

// ================================================================== routes

function health() {
  return {
    ok: !!hmem && !!world,
    engines: { hmem: !!hmem, world: !!world },
    version: VERSION,
    engine_source: ENGINE_SOURCE,
    errors: { hmem: hmemError, world: worldError },
    data_dir: DATA_DIR,
    memory: hmem ? { records: hmem.store.all().length, tiers: tierCounts(hmem), ledger_entries: hmem.ledger.all().length } : null,
    world: world ? { events: world.events().length, runs: stepCount.size, audit_entries: world.audit().length } : null,
    defaults: { recall_min_score: DEFAULT_MIN_SCORE, recall_k: 5 },
  };
}

const ROUTES = [
  "GET  /health",
  "POST /memory/write        {run_id, agent, kind, content, provenance:{source, query?, doc?, origin?}, confidence?, tags?, tier?, supersedes?}",
  "POST /memory/recall       {query, k?, tags?, kinds?, agents?, tiers?, run_id?, exclude_run_id?, min_score?}",
  "POST /memory/consolidate  {run_id?, min_confidence?, min_access?, sleep?, conflicts?, dry_run?}",
  "GET  /memory/audit        ?limit=50&memory_id=&action=&run_id=&verbose=",
  "GET  /memory/item         ?id=mem-1",
  "GET  /memory/list         ?limit=100&kind=&tier=",
  "POST /memory/feedback     {id, verdict: helpful|not_helpful|wrong}",
  "POST /memory/dream        {}",
  "GET  /memory/dream",
  "POST /memory/dream/approve {proposal_id}",
  "POST /memory/dream/reject  {proposal_id}",
  "POST /memory/restore      {backup_id}",
  "POST /world/events        {run_id, type, actor, payload:{status?, facts?, entities?, relations?, ...anything}, at?, event_id?}",
  "GET  /world/events        ?run_id=&since_seq=&limit=&raw=",
  "GET  /world/state         ?run_id=",
  "GET  /world/audit         ?run_id=&limit=",
];

async function route(req: http.IncomingMessage, url: URL): Promise<[number, unknown]> {
  const key = `${req.method} ${url.pathname.replace(/\/+$/, "") || "/"}`;
  const q = url.searchParams;
  switch (key) {
    case "GET /":
      return [200, { service: "whychain-meterless-bridge", version: VERSION, routes: ROUTES }];
    case "GET /health":
      return [200, health()];
    case "POST /memory/write": {
      const body = await readBody(req);
      return [200, await serial(() => memoryWrite(body))];
    }
    case "POST /memory/recall": {
      const body = await readBody(req);
      return [200, await serial(() => memoryRecall(body))];
    }
    case "POST /memory/consolidate": {
      const body = await readBody(req);
      return [200, await serial(() => memoryConsolidate(body))];
    }
    case "GET /memory/audit":
      return [200, memoryAudit(q)];
    case "GET /memory/list":
      return [200, memoryList(q)];
    case "GET /memory/item": {
      const id = q.get("id");
      if (!id) throw bad(`"id" query parameter is required`);
      return [200, memoryItem(id)];
    }
    case "POST /memory/feedback": {
      const body = await readBody(req);
      return [200, await serial(() => memoryFeedback(body))];
    }
    case "POST /memory/dream":
      return [200, await serial(() => memoryDream())];
    case "GET /memory/dream":
      return [200, { proposals: needHmem().dreaming.pending() }];
    case "POST /memory/dream/approve": {
      const body = await readBody(req);
      return [200, await serial(() => memoryDreamDecide(body, true))];
    }
    case "POST /memory/dream/reject": {
      const body = await readBody(req);
      return [200, await serial(() => memoryDreamDecide(body, false))];
    }
    case "POST /memory/restore": {
      const body = await readBody(req);
      return [200, await serial(() => memoryRestore(body))];
    }
    case "POST /world/events": {
      const body = await readBody(req);
      return [200, await serial(() => worldPostEvent(body))];
    }
    case "GET /world/events":
      return [200, worldGetEvents(q)];
    case "GET /world/state":
      return [200, worldGetState(q)];
    case "GET /world/audit":
      return [200, worldAudit(q)];
    default:
      throw new HttpError(404, `no route ${req.method} ${url.pathname}. GET / lists routes.`);
  }
}

async function handler(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  const t0 = performance.now();
  const url = new URL(req.url ?? "/", "http://localhost");
  let status = 500;
  try {
    if (req.method === "OPTIONS") {
      status = 204;
      res.writeHead(204, {
        "access-control-allow-origin": "*",
        "access-control-allow-methods": "GET, POST, OPTIONS",
        "access-control-allow-headers": "content-type",
      });
      res.end();
      return;
    }
    const [code, data] = await route(req, url);
    status = code;
    send(res, code, data);
  } catch (err) {
    status = err instanceof HttpError ? err.status : 500;
    if (status === 500) console.error(err);
    send(res, status, { error: (err as Error).message, status });
  } finally {
    if (process.env.METERLESS_BRIDGE_QUIET !== "1") {
      console.log(`${new Date().toISOString()} ${req.method} ${url.pathname}${url.search} -> ${status} (${(performance.now() - t0).toFixed(1)} ms)`);
    }
  }
}

// ------------------------------------------------------------------ listen

const servers: http.Server[] = [];
let listening = 0;
for (const host of HOSTS) {
  const server = http.createServer((req, res) => void handler(req, res));
  server.on("error", (err: NodeJS.ErrnoException) => {
    if (host === "::1" && HOSTS.length > 1 && (err.code === "EADDRNOTAVAIL" || err.code === "EAFNOSUPPORT")) return; // no IPv6 loopback; IPv4 is enough
    console.error(`meterless-bridge: cannot listen on ${host}:${PORT}: ${err.message}`);
    process.exit(1);
  });
  server.listen(PORT, host, () => {
    listening++;
    if (listening === 1) {
      console.log(`meterless-bridge v${VERSION} listening on http://localhost:${PORT}`);
      console.log(`  H-MEM:       ${hmem ? "ok" : `FAILED (${hmemError})`}  (${HMEM_DIR})`);
      console.log(`  World Model: ${world ? "ok" : `FAILED (${worldError})`}  (${path.join(WORLD_DIR, ".world", `${WORLD_NAMESPACE}.json`)})`);
      console.log(`  engines:     ${ENGINE_SOURCE}`);
    }
  });
  servers.push(server);
}

function shutdown(): void {
  // Both engines persist synchronously on every write; nothing to flush.
  for (const s of servers) s.close();
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
