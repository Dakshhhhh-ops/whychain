// NVIDIA Nemotron embeddings for H-MEM.
//
// H-MEM calls embed(text) synchronously, so the bridge fetches each text's embedding from NVIDIA NIM before handing
// the text to the engine, and registers a lookup (setEmbeddingProvider) that returns the fetched vector. Memories
// are embedded as "passage", recall queries as "query" (the model is asymmetric). If NVIDIA is unreachable, or no
// key is set, the engine's own deterministic mock embedder is used for everything.
//
//   METERLESS_EMBEDDER   nvidia (default when NVIDIA_API_KEY is set) | mock
//   METERLESS_EMBED_MODEL  default nvidia/nemotron-3-embed-1b
//   NVIDIA_API_KEY       read from the environment, or from WhyChain's .env next to this folder (never logged)

import fs from "node:fs";
import path from "node:path";

import { mockEmbed, setEmbeddingProvider } from "./engines/hmem/reference/src/embeddings.ts";

type Kind = "query" | "passage";

function keyFromDotenv(dir: string): string | undefined {
  try {
    const text = fs.readFileSync(path.join(dir, "..", ".env"), "utf-8");
    const m = text.match(/^NVIDIA_API_KEY=(.*)$/m);
    const v = m?.[1]?.trim().replace(/^["']|["']$/g, "");
    return v && !v.includes("your-") ? v : undefined;
  } catch {
    return undefined;
  }
}

export class NemotronEmbedder {
  readonly model: string;
  readonly url: string;
  private key: string | undefined;
  private cache = new Map<string, number[]>();
  private mode: Kind = "passage";
  active: boolean;
  calls = 0;
  failures = 0;
  lastError: string | null = null;
  dim: number | null = null;

  constructor(dir: string) {
    this.key = process.env.NVIDIA_API_KEY || keyFromDotenv(dir);
    this.model = process.env.METERLESS_EMBED_MODEL ?? "nvidia/nemotron-3-embed-1b";
    this.url = (process.env.NIM_BASE_URL ?? "https://integrate.api.nvidia.com/v1").replace(/\/+$/, "") + "/embeddings";
    const choice = (process.env.METERLESS_EMBEDDER ?? "nvidia").toLowerCase();
    this.active = choice === "nvidia" && !!this.key;
    if (this.active) setEmbeddingProvider((text) => this.cache.get(`${this.mode}\u0000${text}`));
  }

  /** Which kind of text the engine will embed next (set just before HMEM.add / query / scoreOne). */
  as(kind: Kind): void {
    this.mode = kind;
  }

  /** Fetch embeddings for texts not yet cached. Returns false (and the engine falls back to its mock) on failure. */
  async prefetch(texts: string[], kind: Kind): Promise<boolean> {
    if (!this.active) return false;
    const todo = [...new Set(texts.filter((t) => t && !this.cache.has(`${kind}\u0000${t}`)))];
    for (let i = 0; i < todo.length; i += 32) {
      const batch = todo.slice(i, i + 32);
      try {
        this.calls++;
        const r = await fetch(this.url, {
          method: "POST",
          headers: { authorization: `Bearer ${this.key}`, "content-type": "application/json" },
          body: JSON.stringify({ input: batch, model: this.model, input_type: kind, encoding_format: "float", truncate: "END" }),
          signal: AbortSignal.timeout(30_000),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
        const body = (await r.json()) as { data: { embedding: number[]; index: number }[] };
        for (const d of body.data) {
          const norm = Math.sqrt(d.embedding.reduce((s, v) => s + v * v, 0)) || 1;
          const v = d.embedding.map((x) => Math.round((x / norm) * 1e5) / 1e5);
          this.dim = v.length;
          this.cache.set(`${kind}\u0000${batch[d.index]}`, v);
        }
      } catch (err) {
        this.failures++;
        this.lastError = (err as Error).message.slice(0, 200);
        return false;
      }
    }
    return true;
  }

  /** Bring stored memories onto the active embedder (Nemotron vectors, or the mock if NVIDIA is off/unreachable). */
  async reembed(records: { content: string; embedding: number[] }[]): Promise<{ updated: number; embedder: string }> {
    let updated = 0;
    const ok = this.active && (await this.prefetch(records.map((r) => r.content), "passage"));
    for (const r of records) {
      const v = ok ? this.cache.get(`passage\u0000${r.content}`) : undefined;
      const next = v ?? mockEmbed(r.content);
      if (next.length !== r.embedding.length || v) {
        r.embedding = next;
        updated++;
      }
    }
    return { updated, embedder: ok ? this.model : "mock (deterministic token hash)" };
  }

  /** Nemotron vectors for texts (undefined where NVIDIA is off or failed): used by Scout Intent's semantic stage. */
  async vectors(texts: string[], kind: Kind): Promise<(number[] | undefined)[]> {
    if (!this.active) return texts.map(() => undefined);
    await this.prefetch(texts, kind);
    return texts.map((t) => this.cache.get(`${kind}\u0000${t}`));
  }

  status() {
    return { embedder: this.active ? this.model : "mock (deterministic token hash)", active: this.active, dim: this.dim,
             calls: this.calls, failures: this.failures, last_error: this.lastError, cached: this.cache.size };
  }
}
