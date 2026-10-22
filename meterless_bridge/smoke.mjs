// Smoke test for the Meterless bridge. Hits every route.
//
//   node smoke.mjs                       # spawns its own bridge on a temp data dir,
//                                        # restarts it mid-test to prove persistence
//   node smoke.mjs --url http://localhost:8787   # test an already-running bridge
//                                        # (writes smoke-* runs into ITS data dir)
//
// Exit code 0 = all checks passed.

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const argUrl = (() => {
  const i = process.argv.indexOf("--url");
  return i !== -1 ? process.argv[i + 1] : process.env.BRIDGE_URL;
})();

let base = argUrl?.replace(/\/+$/, "");
// Against a live bridge (--url), skip the steps that touch memories this smoke
// test did not write: the global sleep cycle, conflict pass, dream approval, restore.
const SAFE = !!argUrl;
let child = null;
let dataDir = null;
const port = 18787 + Math.floor(Math.random() * 1000);

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}${detail ? `  -- ${detail}` : ""}`);
}

async function call(method, route, body) {
  const res = await fetch(base + route, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {}
  return { status: res.status, data };
}
const get = (r) => call("GET", r);
const post = (r, b) => call("POST", r, b ?? {});

async function startServer() {
  const tsxCli = path.join(HERE, "node_modules", "tsx", "dist", "cli.mjs");
  child = spawn(process.execPath, [tsxCli, path.join(HERE, "server.ts")], {
    cwd: HERE,
    env: { ...process.env, METERLESS_BRIDGE_PORT: String(port), METERLESS_BRIDGE_DATA_DIR: dataDir, METERLESS_BRIDGE_QUIET: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stderr = "";
  child.stderr.on("data", (d) => (stderr += d));
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(`${base}/health`);
      if (r.ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error(`bridge did not start: ${stderr}`);
}
async function stopServer() {
  if (!child) return;
  const exited = new Promise((r) => child.once("exit", r));
  child.kill();
  await exited;
  child = null;
}

async function main() {
  if (!base) {
    dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "meterless-bridge-smoke-"));
    base = `http://127.0.0.1:${port}`;
    console.log(`spawning bridge on ${base} with data dir ${dataDir}`);
    await startServer();
  } else {
    console.log(`testing running bridge at ${base}`);
  }
  const runA = `smoke-A-${Date.now()}`;
  const runB = `smoke-B-${Date.now()}`;

  // ---------------------------------------------------------------- health
  let r = await get("/health");
  check("GET /health ok + both engines", r.status === 200 && r.data.ok && r.data.engines.hmem && r.data.engines.world, JSON.stringify(r.data.engines));
  check("GET /health version", typeof r.data.version === "string", r.data.version);
  console.log(`INFO  embedder: ${r.data.embeddings?.embedder ?? "(not reported)"}${r.data.embeddings?.dim ? ` (${r.data.embeddings.dim} dimensions)` : ""}`);
  r = await get("/");
  check("GET / lists routes", r.status === 200 && Array.isArray(r.data.routes) && r.data.routes.length >= 15);

  // ---------------------------------------------------------------- memory write
  const writes = [
    { kind: "finding", content: "Supplier S-14 shipments to Pune were delayed 9 days because of the Chennai port strike", provenance: { source: "zetaris", query: "SELECT po_id, delay_days FROM procurement.po JOIN carrier.shipments ..." }, confidence: 0.85, tags: ["supplier:S-14", "pune", "delay"] },
    { kind: "finding", content: "Pune warehouse stock of SKU-1182 fell below its reorder point on 14 Oct", provenance: { source: "zetaris", query: "SELECT * FROM wms.inventory_snapshots WHERE wh='PUNE'" }, confidence: 0.8, tags: ["pune", "stockout"] },
    { kind: "fact", content: "Mumbai warehouse holds 1200 surplus units of SKU-1182 that could be transferred to Pune", provenance: { source: "zetaris", query: "SELECT qty FROM wms.inventory_snapshots WHERE wh='MUMBAI'" }, confidence: 0.75, tags: ["mumbai", "transfer"] },
    { kind: "hypothesis", content: "Carrier BlueDart on-time rate on the Mumbai-Pune route may have dropped after the contract change", provenance: { source: "planner" }, confidence: 0.4, tags: ["carrier"] },
    { kind: "lesson", content: "Duplicate orders can inflate the at-risk order count; dedupe orders before computing risk", provenance: { source: "evaluator", doc: "contracts/sla_bluedart.pdf" }, confidence: 0.9, tags: ["data-quality"] },
  ];
  const ids = [];
  for (const w of writes) {
    r = await post("/memory/write", { run_id: runA, agent: w.kind === "lesson" ? "evaluator" : "investigator", ...w });
    ids.push(r.data?.id);
    check(`POST /memory/write (${w.kind})`, r.status === 200 && /^mem-\d+$/.test(r.data.id), `${r.data?.id} tier=${r.data?.tier}`);
  }
  r = await post("/memory/write", { run_id: runA, agent: "x", kind: "finding", content: "no source", provenance: {} });
  check("POST /memory/write rejects missing provenance.source (H-MEM contract)", r.status === 400, r.data?.error);
  r = await post("/memory/write", { run_id: runA, agent: "x", kind: "rumour", content: "bad kind", provenance: { source: "s" } });
  check("POST /memory/write rejects unknown kind", r.status === 400);

  // ---------------------------------------------------------------- recall
  r = await post("/memory/recall", { query: "Why are Pune orders at risk next week?", k: 5 });
  const it = r.data?.items?.[0];
  check("POST /memory/recall returns ranked items", r.status === 200 && r.data.items.length > 0, `${r.data?.items?.length} items, top=${it?.id} score=${it?.score}`);
  check("recall item shape {id,content,kind,provenance,confidence,tier,score}",
    it && ["id", "content", "kind", "provenance", "confidence", "tier", "score"].every((k) => k in it) && "source" in it.provenance);
  check("recall returns reinjection context block", typeof r.data?.context === "string" && r.data.context.includes("[MEMORY CONTEXT]"));
  r = await post("/memory/recall", { query: "Chennai port strike supplier delay", tags: ["supplier:S-14"] });
  check("recall with tags filter", r.status === 200 && r.data.items.length === 1 && r.data.items[0].id === ids[0], r.data?.items?.map((i) => i.id).join(","));
  check("recall provenance carries query", r.data?.items?.[0]?.provenance?.query?.startsWith("SELECT"));
  r = await post("/memory/recall", { query: "dedupe duplicate orders", kinds: ["lesson"], run_id: runA });
  check("recall with kinds + run_id filter", r.status === 200 && r.data.items.length === 1 && r.data.items[0].kind === "lesson" && r.data.items[0].provenance.doc === "contracts/sla_bluedart.pdf");
  r = await post("/memory/recall", { query: "Pune stockout SKU-1182", min_score: 0.99 });
  check("recall min_score threshold honoured", r.status === 200 && r.data.items.length === 0);

  // ---------------------------------------------------------------- feedback / item
  r = await post("/memory/feedback", { id: ids[3], verdict: "wrong" });
  check("POST /memory/feedback wrong -> -0.20 + review tag", r.status === 200 && Math.abs(r.data.item.confidence - 0.2) < 1e-9 && r.data.item.tags.includes("review"), `confidence=${r.data?.item?.confidence}`);
  r = await get(`/memory/item?id=${ids[0]}`);
  check("GET /memory/item returns record + ledger history", r.status === 200 && r.data.item.id === ids[0] && r.data.history.some((h) => h.action === "create"), `${r.data?.history?.length} ledger entries`);

  // ---------------------------------------------------------------- consolidate
  r = await post("/memory/consolidate", { run_id: runA, dry_run: true });
  check("POST /memory/consolidate dry_run previews, mutates nothing", r.status === 200 && r.data.promoted === 0 && r.data.details.would_promote.length >= 4, `would promote ${r.data?.details?.would_promote?.length}`);
  r = await post("/memory/consolidate", { run_id: runA, sleep: !SAFE, conflicts: !SAFE });
  const backupId = r.data?.details?.sleep?.backup_id;
  check("POST /memory/consolidate promotes working -> long_term", r.status === 200 && r.data.promoted >= 4, `promoted=${r.data?.promoted} tiers=${JSON.stringify(r.data?.details?.tiers)}`);
  if (!SAFE) check("consolidate ran the engine sleep cycle", r.data?.details?.sleep?.report?.actionLog?.length >= 1 && /^sleep-\d+$/.test(backupId ?? ""), backupId);
  check("hypothesis marked wrong was NOT promoted", !r.data?.details?.promoted?.some((p) => p.id === ids[3]));
  r = await post("/memory/recall", { query: "Chennai port strike", tags: ["supplier:S-14"] });
  check("promoted memory now recalls as long_term", r.data?.items?.[0]?.tier === "long_term");

  // ---------------------------------------------------------------- audit
  r = await get("/memory/audit?limit=50");
  const actions = new Set((r.data?.entries ?? []).map((e) => e.action));
  check("GET /memory/audit returns trail", r.status === 200 && r.data.entries.length > 0 && r.data.entries.length <= 50, `${r.data?.total} total; actions=${[...actions].join(",")}`);
  check("audit trail has create/read/feedback/promote", ["create", "read", "feedback", "promote"].every((a) => actions.has(a)));
  r = await get(`/memory/audit?limit=5&run_id=${runA}&action=promote`);
  check("audit filters (run_id, action)", r.status === 200 && r.data.entries.every((e) => e.action === "promote") && r.data.entries[0]?.diff?.layer?.[1] === "long_term");

  // ---------------------------------------------------------------- dreaming
  r = await post("/memory/dream");
  check("POST /memory/dream returns proposals", r.status === 200 && Array.isArray(r.data.proposals), `${r.data?.proposals?.length} proposals`);
  r = await get("/memory/dream");
  check("GET /memory/dream lists pending", r.status === 200 && Array.isArray(r.data.proposals));
  // Only act on proposals built from memories this smoke run wrote.
  const pending = (r.data?.proposals ?? []).filter((p) => p.derivedFrom.some((d) => ids.includes(d)));
  if (pending.length >= 1 && !SAFE) {
    r = await post("/memory/dream/approve", { proposal_id: pending[0].id });
    check("POST /memory/dream/approve materializes long_term memory with lineage",
      r.status === 200 && (pending[0].type === "domain" || (r.data.memory?.tier === "long_term" && r.data.memory.derived_from.length >= 1)),
      `${pending[0].type} -> ${r.data?.memory?.id} derived_from=${r.data?.memory?.derived_from}`);
  }
  if (pending.length >= 2 && !SAFE) {
    r = await post("/memory/dream/reject", { proposal_id: pending[1].id });
    check("POST /memory/dream/reject", r.status === 200 && r.data.status === "rejected");
  } else {
    r = await post("/memory/dream/reject", { proposal_id: "dream-does-not-exist" });
    check("POST /memory/dream/reject unknown -> 404", r.status === 404);
  }

  // ---------------------------------------------------------------- world events
  const ev = [];
  ev.push(await post("/world/events", { run_id: runA, type: "run_started", actor: "planner", payload: { status: "running", facts: [{ predicate: "question", value: "Why are 23% of Pune orders at risk next week?" }] } }));
  ev.push(await post("/world/events", { run_id: runA, type: "plan", actor: "planner", payload: { hypotheses: ["supplier delay", "carrier issue", "duplicate orders"] } }));
  ev.push(await post("/world/events", {
    run_id: runA, type: "sql_query", actor: "investigator", event_id: "q-1",
    payload: {
      sql: "SELECT ...", rows: 42,
      entities: [{ type: "supplier", name: "Supplier S-14", attrs: { region: "Chennai" } }, { type: "supplier", name: "Supplier S-15" }, { type: "warehouse", name: "Pune WH" }],
      relations: [
        { from: { type: "event", name: "Chennai port strike" }, type: "causes", to: { type: "supplier", name: "Supplier S-14" }, confidence: 0.8 },
        { from: { type: "supplier", name: "Supplier S-14" }, type: "delays_supply_to", to: { type: "warehouse", name: "Pune WH" }, confidence: 0.85 },
      ],
      facts: [{ about: { type: "supplier", name: "Supplier S-14" }, predicate: "avg_delay_days", value: 9, confidence: 0.85 }],
    },
  }));
  const dup = await post("/world/events", { run_id: runA, type: "sql_query", actor: "investigator", event_id: "q-1", payload: { sql: "retry" } });
  check("POST /world/events event_id retry is deduplicated", dup.status === 200 && dup.data.deduplicated === true && dup.data.seq === ev[2].data.seq);
  ev.push(await post("/world/events", {
    run_id: runA, type: "claim_rejected", actor: "evaluator",
    payload: { claim: "carrier issue", reason: "on-time rate unchanged", relations: [{ from: { type: "event", name: "Chennai port strikes" }, type: "explains_risk_at", to: { type: "warehouse", name: "Pune WH" } }] },
  }));
  ev.push(await post("/world/events", { run_id: runA, type: "run_completed", actor: "reporter", payload: { status: "completed", facts: [{ predicate: "root_cause", value: "Supplier S-14 delay (Chennai port strike)", confidence: 0.85 }] } }));
  check("POST /world/events returns {seq}", ev.every((e) => e.status === 200 && Number.isInteger(e.data.seq)), ev.map((e) => e.data?.seq).join(","));
  check("seq strictly increasing", ev.every((e, i) => i === 0 || e.data.seq > ev[i - 1].data.seq));
  r = await post("/world/events", { run_id: runA, type: "x", actor: "y", payload: { relations: [{ from: "ent_doesnotexist", type: "causes", to: { type: "a", name: "b" } }] } });
  check("POST /world/events rejects unknown entity ref (no partial write)", r.status === 400);
  r = await post("/world/events", { run_id: runA, actor: "y" });
  check("POST /world/events requires type", r.status === 400);

  r = await get(`/world/events?run_id=${runA}`);
  check("GET /world/events?run_id lists the run's events in order", r.status === 200 && r.data.events.length === 5 && r.data.events.map((e) => e.step).join() === "1,2,3,4,5", r.data?.events?.map((e) => `${e.step}:${e.type}`).join(" "));
  check("event shape {seq, type, actor, payload, at}", ["seq", "type", "actor", "payload", "at"].every((k) => k in (r.data?.events?.[0] ?? {})));
  const lastSeq = r.data?.events?.[2]?.seq;
  r = await get(`/world/events?run_id=${runA}&since_seq=${lastSeq}`);
  check("GET /world/events since_seq for polling", r.data?.events?.length === 2);
  r = await get(`/world/events?run_id=${runA}&raw=1&limit=500`);
  check("GET /world/events raw=1 exposes engine WorldEvents", Array.isArray(r.data?.world_events) && r.data.world_events.some((e) => e.kind === "relate") && r.data.world_events.some((e) => e.kind === "assertFact"));

  r = await get(`/world/state?run_id=${runA}`);
  const st = r.data;
  check("GET /world/state status derived from facts", r.status === 200 && st.status === "completed", st?.status);
  check("state has run facts (question, root_cause)", st?.facts?.question?.value?.startsWith("Why") && st?.facts?.root_cause?.value?.includes("S-14"));
  check("state steps summary + current step", st?.steps?.count === 5 && st.steps.current?.type === "run_completed" && st.steps.by_actor.planner === 2, JSON.stringify(st?.steps?.by_type));
  check("state bitemporal timeline (closed edges)", st?.steps?.timeline?.length === 5 && st.steps.timeline.filter((t) => t.valid_to === null).length === 1);
  check("state causal relations", st?.relations?.some((x) => x.type === "causes" && x.from_name.startsWith("Chennai port strike") && x.to_name === "Supplier S-14"));
  const suppliers = (st?.entities ?? []).filter((e) => e.type === "supplier");
  check("coded names keyed exactly (Supplier S-14 / S-15 stay distinct)", suppliers.length === 2 && new Set(suppliers.map((e) => e.id)).size === 2, suppliers.map((e) => e.id).join(" "));
  const evs = (st?.entities ?? []).filter((e) => e.type === "event");
  check("free-text names use engine fuzzy resolution (strike/strikes auto-merged)", evs.length === 1 && evs[0].aliases.length === 2, JSON.stringify(evs.map((e) => e.aliases)));
  check("state entities with facts", st?.entities?.some((e) => e.name === "Supplier S-14" && e.facts?.avg_delay_days?.value === 9));
  r = await get("/world/state");
  check("GET /world/state (no run_id) lists runs", r.status === 200 && r.data.runs.some((x) => x.run_id === runA && x.status === "completed"));
  r = await get(`/world/state?run_id=does-not-exist`);
  check("GET /world/state unknown run -> 404", r.status === 404);
  r = await get(`/world/audit?run_id=${runA}&limit=10`);
  check("GET /world/audit", r.status === 200 && r.data.entries.length === 10);

  // ---------------------------------------------------------------- restart -> persistence
  if (child) {
    const before = (await get("/health")).data;
    await stopServer();
    await startServer();
    const after = (await get("/health")).data;
    check("restart: memory records survive", after.memory.records === before.memory.records && after.memory.records > 0, `${after.memory.records} records, tiers=${JSON.stringify(after.memory.tiers)}`);
    check("restart: world events survive (replayed)", after.world.events === before.world.events, `${after.world.events} events`);
    r = await get(`/world/state?run_id=${runA}`);
    check("restart: derived state identical (status, facts, relations)", r.data.status === "completed" && r.data.facts.root_cause && r.data.relations.length === st.relations.length && r.data.steps.current?.step === 5);
    r = await post("/world/events", { run_id: runA, type: "sql_query", actor: "investigator", event_id: "q-1", payload: {} });
    check("restart: event_id dedupe index rebuilt from log", r.data?.deduplicated === true);
  }

  // ---------------------------------------------------------------- run B: compounding
  r = await post("/memory/recall", { query: "Why are Pune orders at risk? supplier delay", exclude_run_id: runB });
  check("run B recalls run A's memories (compounding)", r.status === 200 && r.data.items.some((i) => i.run_id === runA), r.data?.items?.map((i) => `${i.id}[${i.tier}]`).join(" "));
  r = await post("/memory/write", { run_id: runB, agent: "investigator", kind: "finding", content: "Supplier S-14 shipments to Pune were delayed 9 days because of the Chennai port strike", provenance: { source: "zetaris", query: "SELECT ..." }, confidence: 0.9, tags: ["supplier:S-14", "pune", "delay"] });
  check("run B writes a repeat finding", r.status === 200);
  if (SAFE) {
    console.log("(live bridge: skipped global sleep/synthesis, dream approval and restore checks; run without --url for the full test)");
  } else {
  r = await post("/memory/consolidate", { run_id: runB });
  const synth = r.data?.details?.sleep?.report?.synthesized ?? 0;
  check("run B consolidate: sleep synthesizes the cross-run duplicate", r.status === 200 && synth >= 1, `synthesized=${synth}; ${r.data?.details?.sleep?.report?.actionLog?.join("; ")}`);
  r = await post("/memory/recall", { query: "Chennai port strike Supplier S-14", tags: ["supplier:S-14"] });
  const top = r.data?.items?.[0];
  check("synthesized memory has lineage (derived_from both runs)", top?.kind && top.derived_from?.length >= 2 && top.tier === "long_term", `${top?.id} derived_from=${top?.derived_from}`);

  // ---------------------------------------------------------------- restore
  r = await post("/memory/restore", { backup_id: backupId });
  check("POST /memory/restore from disk backup", r.status === 200 && r.data.restored > 0, `restored ${r.data?.restored}`);
  r = await get("/memory/audit?limit=1&action=restore");
  check("restore is audited", r.data?.entries?.[0]?.action === "restore");
  }

  const failed = results.filter((x) => !x.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  return failed.length === 0;
}

let ok = false;
try {
  ok = await main();
} catch (err) {
  console.error("smoke: fatal", err);
} finally {
  await stopServer();
  if (dataDir) fs.rmSync(dataDir, { recursive: true, force: true });
}
process.exit(ok ? 0 : 1);
