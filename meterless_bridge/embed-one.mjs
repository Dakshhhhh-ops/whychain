// Embed one text (stdin) with NVIDIA Nemotron and print the unit vector as JSON. Used by conformance-nemotron.ts,
// which needs embeddings synchronously. The key comes from NVIDIA_API_KEY or WhyChain's .env and is never printed.
//   echo "some text" | node embed-one.mjs
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
let key = process.env.NVIDIA_API_KEY;
if (!key) {
  try {
    const m = fs.readFileSync(path.join(HERE, "..", ".env"), "utf-8").match(/^NVIDIA_API_KEY=(.*)$/m);
    key = m?.[1]?.trim().replace(/^["']|["']$/g, "");
  } catch {
    /* no .env */
  }
}
if (!key) {
  console.error("embed-one: no NVIDIA_API_KEY");
  process.exit(2);
}
const text = fs.readFileSync(0, "utf-8");
const model = process.env.METERLESS_EMBED_MODEL ?? "nvidia/nemotron-3-embed-1b";
const base = (process.env.NIM_BASE_URL ?? "https://integrate.api.nvidia.com/v1").replace(/\/+$/, "");
const r = await fetch(`${base}/embeddings`, {
  method: "POST",
  headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
  body: JSON.stringify({ input: [text || " "], model, input_type: process.env.EMBED_KIND ?? "passage",
                         encoding_format: "float", truncate: "END" }),
});
if (!r.ok) {
  console.error(`embed-one: HTTP ${r.status}`);
  process.exit(1);
}
const v = (await r.json()).data[0].embedding;
const n = Math.sqrt(v.reduce((s, x) => s + x * x, 0)) || 1;
process.stdout.write(JSON.stringify(v.map((x) => x / n)));
