// H-MEM conformance with the Nemotron provider ON: the vendored reference engine, with the provider hook set to
// NVIDIA Nemotron embeddings (every text the engine embeds is fetched through embed-one.mjs).
//
//   HMEM_IMPL=<absolute path of this file> npx tsx engines/hmem/conformance/runner.ts
//
// The conformance vectors don't say which texts are queries, so every text is embedded as a passage here (the bridge
// itself embeds recall queries as queries).
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { setEmbeddingProvider } from "./engines/hmem/reference/src/embeddings.ts";

export { HMEM } from "./engines/hmem/reference/src/index.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const cache = new Map<string, number[]>();
setEmbeddingProvider((text) => {
  let v = cache.get(text);
  if (!v) {
    const out = execFileSync(process.execPath, [path.join(HERE, "embed-one.mjs")],
                             { input: text, encoding: "utf-8", timeout: 60_000 });
    v = JSON.parse(out) as number[];
    cache.set(text, v);
  }
  return v;
});
process.on("exit", () => {
  console.log(`\n  Nemotron provider ON: ${cache.size} distinct texts embedded with ` +
              `${process.env.METERLESS_EMBED_MODEL ?? "nvidia/nemotron-3-embed-1b"} (${cache.values().next().value?.length ?? 0} dimensions)`);
});
