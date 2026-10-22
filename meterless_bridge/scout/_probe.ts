// Print Scout's decision for every example of the vendored regression set (a debugging aid; not shipped).
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Scout } from "./scout.ts";

const base = join(dirname(fileURLToPath(import.meta.url)), "..", "engines", "scout-intent", "evals");
const s = new Scout({ intentRegistry: join(base, "fixtures", "intents.json"), policyPack: ["default", "adversarial-tight"] });
for (const d of ["intents", "injection", "multi-intent", "edge-cases"])
  for (const f of readdirSync(join(base, "regression-set", "v1", d)))
    for (const l of readFileSync(join(base, "regression-set", "v1", d, f), "utf-8").split("\n").filter(Boolean)) {
      const ex = JSON.parse(l);
      const o = await s.decide({ prompt: ex.prompt, user: { id: "eval", role: ex.user?.role ?? "ae" }, surface: ex.surface ?? "chat" });
      console.log(ex.id.padEnd(26), `${o.intent.primary} ${o.intent.confidence} ${o.intent.band}${o.stages.semantic ? " S2" : ""}`.padEnd(37), o.risk.level.padEnd(6),
        o.clarification ? "CLARIFY " + o.clarification.reasons.join(",") : "", "| exp", JSON.stringify(ex.expected).slice(0, 60));
    }
