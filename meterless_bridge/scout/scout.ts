// Meterless Scout Intent: WhyChain's implementation of the specification in Meterless/Meterless @ 1742dec
// (engines/scout-intent: AGENTS.md and docs/). The spec ships no code ("you build the engine"); this file is ours.
// It is checked with the spec's own eval harness (vendored unchanged under engines/scout-intent/evals):
//
//   SCOUT_IMPL=<abs path to this file> npx tsx engines/scout-intent/evals/runner/index.ts all
//
// Pipeline (docs/architecture.md): sense -> interpret -> guard -> route -> recommend.
//   Sense      Stage 1, a deterministic weighted trigger scan over the registry (always on); Stage 2, semantic
//              similarity to each intent's examples with NVIDIA Nemotron embeddings when an embed function is given.
//              Merged with the canonical weights (docs/scoring.md), renormalised over the stages that ran.
//   Interpret  a few parameters (count, channel, amounts, a capitalised account name).
//   Guard      pattern injection check (instruction override, role confusion, secret exfiltration, privilege and
//              unregistered-tool references, invisible control characters), plus role and surface scope.
//   Route      the intent's capabilities as the tool plan; approval for high-risk intents.
//   Recommend  a model profile and an HMAC-signed execution contract (docs/execution-contract.md).

import { createHmac, randomBytes } from "node:crypto";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

export type IntentDef = {
  id: string;
  category?: string;
  description?: string;
  parameters?: unknown;
  capabilities: string[];
  riskClass: "low" | "medium" | "high";
  surfaces?: string[];
  roles?: string[];
  examples?: string[];
  triggers?: string[];
  version?: number;
};
type Registry = { version?: number; intents: IntentDef[] };
export type Embed = (texts: string[], kind: "query" | "passage") => Promise<(number[] | undefined)[]>;
export type Candidate = { intent: string; score: number; lexical: number; semantic: number | null };
type Flag = { layer: "injection" | "scope" | "policy"; kind: string; evidence: string[] };

const WEIGHTS = { lexical: 0.4, semantic: 0.35, contextual: 0.2, recency: 0.05 }; // docs/scoring.md
const BANDS = { high: 0.8, medium: 0.55, low: 0.4 };
const STOP = new Set(
  ("a an the and or but if of to in on at by for with from into about over under as is are was were be been being " +
    "do does did done i me my we our you your it its this that these those there here what which who whom whose " +
    "how why when where can could should would will shall may might must please me us them they he she his her " +
    "all any some each every more most other such only own same so than too very just also now then up out")
    .split(" "),
);

function stem(w: string): string {
  if (w.length > 5 && w.endsWith("ies")) return w.slice(0, -3) + "y";
  if (w.length > 4 && w.endsWith("ing")) return w.slice(0, -3);
  if (w.length > 4 && w.endsWith("ed")) return w.slice(0, -2);
  if (w.length > 3 && w.endsWith("s") && !w.endsWith("ss")) return w.slice(0, -1);
  return w;
}

export function tokens(text: string): string[] {
  return (text.toLowerCase().normalize("NFKD").match(/[a-z0-9$][a-z0-9$\-']*/g) ?? [])
    .map((t) => t.replace(/'s$/, ""))
    .filter((t) => !STOP.has(t) && t.length > 1)
    .map(stem);
}

function grams(text: string): string[] {
  const t = tokens(text);
  return [...t, ...t.slice(1).map((w, i) => `${t[i]} ${w}`)];
}

const cos = (a: number[], b: number[]) => {
  let s = 0;
  for (let i = 0; i < Math.min(a.length, b.length); i++) s += a[i] * b[i];
  return s;
};

// ---------------------------------------------------------------------------- guard: policy packs
type Rule = { kind: string; re: RegExp };
const PACKS: Record<string, Rule[]> = {
  default: [
    { kind: "instruction-override",
      re: /\b(ignore|disregard|forget|override|bypass)\b[^\n.]{0,40}?\b(previous|prior|above|earlier|preceding|all|the|any|your)\b[^\n.]{0,25}?\b(instructions?|rules?|prompts?|directions?|guidelines?|user|policy|policies)\b/i },
    { kind: "instruction-override", re: /\b(ignore|disregard)\s+(previous|prior|above|everything)\b/i },
    { kind: "mode-switch", re: /\byou are now\b|\b(unrestricted|developer|god|jailbreak|dan)\s+mode\b|\bno (longer )?(bound|restricted) by\b/i },
    { kind: "role-confusion", re: /<\/?\s*(system|user|assistant|tool)\s*>|\[\/?INST\]|<<\/?SYS>>|\bpretend (to be|you are) (the )?(system|admin|developer)\b|\bact as (the )?(system|root)\b/i },
    { kind: "secret-exfiltration", re: /\b(reveal|show|print|dump|leak|exfiltrate|list|send)\b[^\n.]{0,30}?\b(api[ _-]?keys?|credentials?|passwords?|secrets?|access tokens?|private keys?)\b/i },
    { kind: "privilege-escalation", re: /\b(grant|give|make)\s+(me|us|my account)\s+(admin|root|superuser|owner)\b|\bdrop\s+(the\s+)?\w+\s+table\b/i },
  ],
  "adversarial-tight": [
    { kind: "tool-ref", re: /\b(use|call|invoke|run)\s+(the\s+)?[a-z]+_[a-z0-9_]+\b/i },
    { kind: "control-bytes", re: /[​-‏‪-‮⁦-⁩﻿]|\\u20(0[b-f]|2[a-e]|6[6-9])/i },
    { kind: "embedded-instruction", re: /(^|\n)\s*#+\s*(important|system|instructions?)\b[\s\S]{0,200}\b(disregard|ignore|reveal|override)\b/i },
  ],
};
const UNRESOLVED = /\b(the thing|that thing|the stuff|what we (talked|spoke|discussed)( about)?|as (we )?discussed|you know what)\b/i;
const VAGUE = /^\s*(handle|deal with|take care of|sort out|look into|fix|manage)\b/i;

// ---------------------------------------------------------------------------- the engine
export class Scout {
  readonly registry: Registry;
  readonly policyPackVersion: string;
  private rules: Rule[];
  private weights = new Map<string, Map<string, number>>(); // intent -> gram -> weight
  private protos = new Map<string, number[]>();
  private protosReady: Promise<void> | null = null;
  private embed?: Embed;
  private secret: Buffer;
  private seq = 0;

  constructor(opts: { intentRegistry: string | Registry; policyPack?: string | string[]; embed?: Embed; secret?: string }) {
    this.registry = typeof opts.intentRegistry === "string"
      ? (JSON.parse(fs.readFileSync(opts.intentRegistry, "utf-8")) as Registry)
      : opts.intentRegistry;
    const packs = Array.isArray(opts.policyPack) ? opts.policyPack : [opts.policyPack ?? "default"];
    this.rules = packs.flatMap((p) => PACKS[p] ?? []);
    this.policyPackVersion = packs.join("+") + "@1";
    this.embed = opts.embed ?? Scout.envEmbed();
    this.secret = Buffer.from(opts.secret ?? process.env.SCOUT_SECRET ?? randomBytes(32).toString("hex"));
    this.buildTriggers();
  }

  /** SCOUT_SEMANTIC=nemotron: Stage 2 on NVIDIA Nemotron embeddings (the bridge's embedder, key from the environment
   * or WhyChain's .env), for runs outside the bridge such as the eval harness. */
  private static envEmbed(): Embed | undefined {
    if ((process.env.SCOUT_SEMANTIC ?? "").toLowerCase() !== "nemotron") return undefined;
    let emb: { active: boolean; vectors: Embed } | null = null;
    return async (texts, kind) => {
      if (!emb) {
        const { NemotronEmbedder } = await import("../nemotronEmbed.ts");
        // the bridge folder: the embedder reads WhyChain's .env next to it
        emb = new NemotronEmbedder(fileURLToPath(new URL("..", import.meta.url))) as never;
      }
      return emb!.active ? emb!.vectors(texts, kind) : texts.map(() => undefined);
    };
  }

  /** Stage 1 weights: each intent's grams (from its examples, description and id; explicit triggers count
   * double), weighted by how specific they are to that intent (inverse document frequency over the registry). */
  private buildTriggers(): void {
    const per = new Map<string, Map<string, number>>();
    const df = new Map<string, number>();
    for (const it of this.registry.intents) {
      const m = new Map<string, number>();
      const add = (text: string, w: number) => {
        for (const g of grams(text)) m.set(g, Math.max(m.get(g) ?? 0, w));
      };
      for (const ex of it.examples ?? []) add(ex, 1);
      add(it.description ?? "", 0.6);
      add(it.id.replace(/[._-]/g, " "), 1);
      for (const t of it.triggers ?? []) add(t, 2);
      per.set(it.id, m);
      for (const g of m.keys()) df.set(g, (df.get(g) ?? 0) + 1);
    }
    const n = this.registry.intents.length;
    for (const [id, m] of per) {
      const w = new Map<string, number>();
      for (const [g, base] of m) w.set(g, base * Math.log(1 + n / (df.get(g) ?? 1)) * (g.includes(" ") ? 1.5 : 1));
      this.weights.set(id, w);
    }
  }

  /** Stage 1: weighted trigger matches; an intent's confidence is its share of all matched weight, scaled by how
   * much of the prompt it accounts for. */
  lexical(prompt: string): Map<string, number> {
    const g = new Set(grams(prompt));
    const raw = new Map<string, number>();
    let total = 0;
    for (const it of this.registry.intents) {
      if (it.id === "GENERAL") continue;
      let s = 0;
      for (const x of g) s += this.weights.get(it.id)?.get(x) ?? 0;
      raw.set(it.id, s);
      total += s;
    }
    const content = Math.max(1, tokens(prompt).length);
    const out = new Map<string, number>();
    const pt = new Set(tokens(prompt));
    for (const [id, s] of raw) {
      const covered = [...pt].filter((t) => this.weights.get(id)?.has(t)).length / content;
      let conf = total ? (s / total) * Math.min(1, 0.35 + covered) : 0;
      // a registered example phrase, said (almost) word for word, is a full-strength trigger
      const ex = this.registry.intents.find((i) => i.id === id)?.examples ?? [];
      if (pt.size && ex.some((e) => jaccard(pt, new Set(tokens(e))) >= 0.85)) conf = Math.max(conf, 0.9);
      out.set(id, conf);
    }
    return out;
  }

  private async prototypes(): Promise<void> {
    if (!this.embed) return;
    this.protosReady ??= (async () => {
      for (const it of this.registry.intents) {
        const texts = [...(it.examples ?? []), it.description ?? ""].filter(Boolean);
        const vs = (await this.embed!(texts, "passage")).filter(Boolean) as number[][];
        if (!vs.length) continue;
        const mean = vs[0].map((_, i) => vs.reduce((s, v) => s + v[i], 0) / vs.length);
        const norm = Math.sqrt(mean.reduce((s, x) => s + x * x, 0)) || 1;
        this.protos.set(it.id, mean.map((x) => x / norm));
      }
    })();
    await this.protosReady;
  }

  /** Stage 2: similarity of the prompt to each intent's prototype (mean of its example embeddings), rescaled so
   * that an unrelated prompt scores near 0 and a close paraphrase near 1. Null when no embeddings are available. */
  async semantic(prompt: string): Promise<Map<string, number> | null> {
    if (!this.embed) return null;
    try {
      await this.prototypes();
      const [q] = await this.embed([prompt], "query");
      if (!q || !this.protos.size) return null;
      const out = new Map<string, number>();
      for (const [id, p] of this.protos) out.set(id, Math.max(0, Math.min(1, (cos(q, p) - 0.15) / 0.45)));
      return out;
    } catch {
      return null; // Stage 2 degrades gracefully: Stage 1 stands
    }
  }

  /** Stage 1 and (when available) Stage 2, merged with the canonical weights over the stages that ran. */
  async rank(text: string): Promise<{ candidates: Candidate[]; semantic: boolean }> {
    const lex = this.lexical(text);
    const sem = await this.semantic(text);
    const wsum = WEIGHTS.lexical + (sem ? WEIGHTS.semantic : 0);
    const candidates = this.registry.intents
      .filter((i) => i.id !== "GENERAL")
      .map((i) => {
        const l = lex.get(i.id) ?? 0;
        const s = sem ? sem.get(i.id) ?? 0 : null;
        return { intent: i.id, lexical: round(l), semantic: s === null ? null : round(s),
                 score: round((WEIGHTS.lexical * l + (s === null ? 0 : WEIGHTS.semantic * s)) / wsum) };
      })
      .sort((a, b) => b.score - a.score);
    return { candidates, semantic: sem !== null };
  }

  guard(prompt: string, intent: IntentDef | undefined, user: { role?: string }, surface: string) {
    const flags: Flag[] = [];
    for (const r of this.rules) {
      const m = prompt.match(r.re);
      if (m) flags.push({ layer: "injection", kind: r.kind, evidence: [m[0].slice(0, 60)] });
    }
    if (intent && intent.roles && !intent.roles.includes("*") && user.role && !intent.roles.includes(user.role))
      flags.push({ layer: "scope", kind: "role", evidence: [`role ${user.role} not in ${intent.roles.join(", ")}`] });
    if (intent && intent.surfaces && !intent.surfaces.includes(surface))
      flags.push({ layer: "scope", kind: "surface", evidence: [`surface ${surface}`] });
    const blocked = flags.some((f) => f.layer === "injection");
    const order = ["low", "medium", "high"] as const;
    let level: "low" | "medium" | "high" | "block" = intent?.riskClass ?? "low";
    if (!blocked && flags.some((f) => f.layer === "scope"))
      level = order[Math.min(2, order.indexOf(level as (typeof order)[number]) + 1)]; // scope lifts one band
    return { level: blocked ? ("block" as const) : level, flags, redactions: [], approvals: [] };
  }

  private interpret(prompt: string): Record<string, unknown> {
    const p: Record<string, unknown> = {};
    const words: Record<string, number> = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10 };
    const n = prompt.match(/\b(?:top|give me|find|show)?\s*(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)\b(?!\s*(%|days?|weeks?|months?|k\b))/i);
    if (n) p.count = Number(n[1]) || words[n[1].toLowerCase()];
    const ch = prompt.match(/\b(email|call|slack)s?\b/i);
    if (ch) p.channel = ch[1].toLowerCase();
    const amt = prompt.match(/\$\s?(\d+(?:\.\d+)?)\s*(k|m)?\b/i);
    if (amt) p.valueMin = Number(amt[1]) * (amt[2]?.toLowerCase() === "k" ? 1e3 : amt[2]?.toLowerCase() === "m" ? 1e6 : 1);
    const acct = prompt.match(/\b(?:the\s+)?([A-Z][a-zA-Z]+)\s+(?:deal|account|renewal|situation)\b/);
    if (acct) p.account = acct[1];
    return p;
  }

  private sign(contract: Record<string, unknown>): string {
    const canon = (v: unknown): unknown =>
      Array.isArray(v) ? v.map(canon)
        : v && typeof v === "object"
          ? Object.fromEntries(Object.keys(v as object).sort().map((k) => [k, canon((v as Record<string, unknown>)[k])]))
          : v;
    return createHmac("sha256", this.secret).update(JSON.stringify(canon(contract))).digest("hex");
  }

  verify(contract: Record<string, unknown>): boolean {
    const { signature, ...rest } = contract as { signature?: string };
    return typeof signature === "string" && this.sign(rest) === signature;
  }

  private contractId(): string {
    // ULID-style: time-ordered, then random
    const t = Date.now().toString(36).padStart(10, "0");
    return `${t}${(this.seq++).toString(36).padStart(3, "0")}${randomBytes(5).toString("hex")}`.toUpperCase();
  }

  async decide(req: { prompt: string; user?: { id?: string; role?: string }; surface?: string; traceId?: string }) {
    const prompt = String(req.prompt ?? "");
    const user = req.user ?? { id: "anonymous" };
    const surface = req.surface ?? "chat";
    const t0 = Date.now();
    // Sense
    let { candidates, semantic } = await this.rank(prompt);
    let top = candidates[0];
    // a composed request: each clause is ranked on its own, and when two clauses name different intents with at
    // least Medium confidence, the plan carries both instead of choosing (docs/scoring.md)
    let intents: string[] = [];
    const parts = spans(prompt);
    if (parts.length >= 2) {
      const tops = (await Promise.all(parts.map((p) => this.rank(p)))).map((r) => r.candidates[0]);
      const strong = tops.filter((c) => c && c.score >= BANDS.medium);
      const distinct = [...new Set(strong.map((c) => c.intent))];
      if (distinct.length >= 2) {
        intents = distinct;
        top = strong[0];
        candidates = [top, ...candidates.filter((c) => c.intent !== top.intent)];
      }
    }
    const band = !top || top.score < BANDS.low ? "reject" : top.score < BANDS.medium ? "low"
      : top.score < BANDS.high ? "medium" : "high";
    const general = this.registry.intents.find((i) => i.id === "GENERAL");
    const primary = band === "reject" ? general ?? { id: "GENERAL", capabilities: [], riskClass: "low" as const }
      : this.registry.intents.find((i) => i.id === top.intent)!;
    if (!intents.length) intents = [primary.id];
    const sem = semantic;
    // Interpret
    const parameters = this.interpret(prompt);
    // Guard
    const risk = this.guard(prompt, primary.id === "GENERAL" ? undefined : primary, user, surface);
    // clarification triggers (docs/scoring.md), plus an unresolved reference or a vague verb with nothing to act on
    const second = candidates[1];
    const reasons: string[] = [];
    if (band === "low") reasons.push("low-confidence");
    if (band === "medium" && second && top.score - second.score < 0.05) reasons.push("close-alternatives");
    if (primary.riskClass === "high" && top && top.score < 0.95 && band !== "reject") reasons.push("high-risk");
    if (UNRESOLVED.test(prompt)) reasons.push("reference-unresolved");
    if (VAGUE.test(prompt) && tokens(prompt).length <= 3) reasons.push("underspecified");
    const clarification = risk.level !== "block" && reasons.length > 0;
    // Route
    const toolPlan = risk.level === "block" ? [] : [
      ...primary.capabilities.map((capability, i) => ({ step: i + 1, capability })),
      ...(risk.level === "high" && !primary.capabilities.includes("approval.require")
        ? [{ step: primary.capabilities.length + 1, capability: "approval.require" }] : []),
    ];
    // Recommend
    const modelProfile = {
      id: primary.id === "GENERAL" ? "small-chat" : /^(plan|research)/.test(primary.id) ? "reasoning-long"
        : primary.riskClass === "high" ? "careful" : "fast-tools",
    };
    let executionContract: Record<string, unknown> | null = null;
    if (risk.level !== "block") {
      const issued = new Date();
      const body: Record<string, unknown> = {
        contractId: this.contractId(),
        traceId: req.traceId ?? randomBytes(8).toString("hex"),
        intent: {
          primary: { id: primary.id, version: primary.version ?? 1 },
          parameters,
          references: {},
          confidence: band === "reject" ? round(top?.score ?? 0) : top.score,
          alternates: candidates.slice(1, 4),
        },
        risk,
        toolPlan,
        modelProfile,
        scope: { user, surface, capabilities: toolPlan.map((s) => s.capability) },
        issuedAt: issued.toISOString(),
        expiresAt: new Date(issued.getTime() + 5 * 60_000).toISOString(),
        registryVersion: this.registry.version ?? 1,
        policyPackVersion: this.policyPackVersion,
        signedBy: "scout",
      };
      executionContract = { ...body, signature: this.sign(body) };
    }
    return {
      intent: { primary: primary.id, confidence: round(top?.score ?? 0), band, intents },
      candidates,
      parameters,
      risk,
      toolPlan,
      modelProfile,
      clarification: clarification ? { reasons, options: candidates.slice(0, 4).map((c) => c.intent) } : false,
      executionContract,
      stages: { lexical: true, semantic: sem },
      ms: Date.now() - t0,
    };
  }
}

function round(x: number): number {
  return Math.round(x * 1000) / 1000;
}

function jaccard(a: Set<string>, b: Set<string>): number {
  let inter = 0;
  for (const x of a) if (b.has(x)) inter++;
  return inter / Math.max(1, a.size + b.size - inter);
}

/** Clauses of a composed request ("Pull the Q2 numbers and draft a board email"), split where a new action starts. */
const ACTION = "draft|write|send|email|schedule|book|put|add|create|summari[sz]e|find|pull|show|list|compare|research|plan|move|update|reply";
export function spans(prompt: string): string[] {
  return prompt
    .split(new RegExp(`\\s*(?:,|;|\\band then\\b|\\bthen\\b|\\band\\b)\\s+(?=(?:${ACTION})\\b)`, "i"))
    .map((s) => s.trim())
    .filter((s) => tokens(s).length >= 2);
}
